"""Structured security audit events (SR-2026-052 item 1.7, SEC-LOG-001).

One JSON object per line on a dedicated logger, `ses.audit`, kept apart from
the application log so it can be shipped to the SIEM on its own and retained
on its own schedule. Every event names:

- `event`            what happened, dotted: auth.login, ai.scoring, ...
- `outcome`          success | failure | denied
- `actor_user_id`    the signed-in account that acted (null before sign-in)
- `participant_id`   the pseudonymous id the work is keyed to

Transcript content is kept out by construction, not by care at each call
site: fields whose names suggest interview text are refused outright, and
every string value is length-capped. An audit event says *that* something
happened to a session, never *what was said* in it.

Where it goes is deployment configuration (see AUDIT_LOG_SINK in .env.example):
stdout for the service manager's journal, or syslog for a forwarder.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import socket
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import Request

from app.auth.ratelimit import client_ip
from app.config import settings

AUDIT_LOGGER_NAME = "ses.audit"
# Bump when a field is renamed or removed, so SIEM parsing rules can follow.
AUDIT_SCHEMA_VERSION = 1

Outcome = Literal["success", "failure", "denied"]

# Field names that would carry what someone said. A call site passing one of
# these is a bug, so it fails loudly in tests rather than leaking quietly.
FORBIDDEN_FIELDS = frozenset({
    "text", "transcript", "turns", "query", "quote", "evidence_quote",
    "student_quote", "prompt", "content", "message", "messages", "password",
    "token", "id_token", "code", "email", "first_name", "last_name", "name",
})
_MAX_STR = 200

_logger = logging.getLogger(AUDIT_LOGGER_NAME)
# Audit events go only where configure_audit_logging() sends them — never
# duplicated into the application log, which has a different audience and
# retention.
_logger.propagate = False


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value][:20]
    text = str(value)
    return text if len(text) <= _MAX_STR else text[:_MAX_STR] + "…"


def audit(
    event: str,
    outcome: Outcome,
    *,
    actor_user_id: Optional[UUID] = None,
    participant_id: Optional[UUID] = None,
    request: Optional[Request] = None,
    **fields: Any,
) -> None:
    """Emit one audit event. Never raises into the request on a sink failure."""
    leaked = FORBIDDEN_FIELDS.intersection(fields)
    if leaked:
        raise ValueError(f"audit field(s) {sorted(leaked)} could carry interview text or PII")

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "type": "audit",
        "schema": AUDIT_SCHEMA_VERSION,
        "service": "ses",
        "env": settings.environment,
        "event": event,
        "outcome": outcome,
        "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
        "participant_id": str(participant_id) if participant_id is not None else None,
    }
    if request is not None:
        record["ip"] = client_ip(request)
        record["method"] = request.method
        record["path"] = request.url.path
    for key, value in fields.items():
        record[key] = _clean(value)

    _logger.info(json.dumps(record, default=str, separators=(",", ":")))


class _MessageOnly(logging.Formatter):
    # The message already is the JSON record; anything around it would break
    # line-oriented SIEM parsing.
    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


_sink: Optional[logging.Handler] = None


def configure_audit_logging() -> None:
    """Attach the configured sink. Idempotent: safe to call more than once."""
    global _sink
    if _sink is not None:
        _logger.removeHandler(_sink)
        _sink = None

    sink = settings.audit_log_sink.strip().lower()
    handler: logging.Handler
    if sink == "none":
        return
    if sink == "syslog":
        address: Any = settings.audit_syslog_address
        if ":" in address and not address.startswith("/"):
            host, _, port = address.rpartition(":")
            address = (host, int(port))
        handler = logging.handlers.SysLogHandler(
            address=address,
            facility=logging.handlers.SysLogHandler.LOG_AUTH,
            socktype=socket.SOCK_DGRAM,
        )
        handler.ident = "ses-audit: "
    elif sink == "stdout":
        handler = logging.StreamHandler()
    else:
        raise RuntimeError(f"AUDIT_LOG_SINK={sink!r}: expected stdout, syslog or none")

    handler.setFormatter(_MessageOnly())
    _logger.addHandler(handler)
    _sink = handler
    _logger.setLevel(logging.INFO)
