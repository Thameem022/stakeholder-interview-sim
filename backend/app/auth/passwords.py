"""Password hashing, the account password policy, and temp-password handling."""

from __future__ import annotations

import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import settings

_hasher = PasswordHasher()

# Burned when no user row exists, so a missing account costs the same wall time
# as a wrong password. Without it, response latency alone reveals which
# addresses are registered.
_DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")

# Temp passwords are shown to humans and retyped, so compare them without
# caring about the grouping dashes or letter case.
_TEMP_NOISE_RE = re.compile(r"[\s\-]")

# Excludes I/O/0/1 — they are the characters people misread when copying a
# code out of an email.
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(hashed: str | None, raw: str) -> bool:
    """Verify `raw` against `hashed`. A None hash always fails, at full cost."""
    try:
        _hasher.verify(hashed if hashed is not None else _DUMMY_HASH, raw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return hashed is not None


def normalize_temp_password(raw: str) -> str:
    return _TEMP_NOISE_RE.sub("", raw).upper()


def generate_temp_password() -> str:
    """A fresh temp password in the 7QF-42KD-XM shape."""
    body = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(9))
    return f"{body[:3]}-{body[3:7]}-{body[7:]}"


def issue_temp_password() -> str:
    """The temp password for a new registration.

    In `fixed` mode this is the known development constant — no email exists
    yet, so a random value would be unrecoverable. `get_settings()` refuses to
    boot in production while this mode is active.
    """
    if settings.auth_temp_password_mode == "fixed":
        return settings.auth_dev_temp_password
    return generate_temp_password()


def password_policy_failures(raw: str) -> list[str]:
    """Unmet password rules, labelled to match the registration checklist.

    The frontend's strength meter is presentation only; this is the check that
    decides whether a password is accepted.
    """
    min_len = settings.auth_min_password_length
    rules = (
        (f"{min_len}+ characters", len(raw) >= min_len),
        ("One uppercase letter", bool(re.search(r"[A-Z]", raw))),
        ("One number", bool(re.search(r"[0-9]", raw))),
        ("One symbol", bool(re.search(r"[^A-Za-z0-9]", raw))),
    )
    return [label for label, ok in rules if not ok]
