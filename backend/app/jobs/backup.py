"""Daily encrypted database backup (SR-2026-052 item 3.2, SEC-BCK-001).

Run daily by the systemd timer in deploy/systemd/:

    uv run python -m app.jobs.backup                # back up, then expire old backups
    uv run python -m app.jobs.backup --expire-only  # expire only

How a backup is made:

  pg_dump --format=custom | gpg --encrypt (AES-256, to BACKUP_PUBLIC_KEY)

The dump is streamed straight into gpg, so no plaintext copy ever touches the
disk, and it is encrypted to a *public* key: this server can write backups but
cannot read them. The private key is held offline by the Support Owner, and is
needed only to restore (deploy/backup/ses-restore.sh). Each backup gets a
`sha256sum`-format checksum file beside it.

How long a backup lives:

  BACKUP_KEEP_DAYS, capped at the shortest retention window
  (RETENTION_COURSE_GRACE_DAYS, RETENTION_TELEMETRY_DAYS). A backup is a copy
  of data the retention job will later delete, so it must not outlive that
  data by more than one window. Expiry runs on every run, whether or not the
  new backup succeeded: retention is a promise, and a failing backup alerts
  on its own (the unit fails).

Each run emits one `admin.backup_run` audit event (file name, size, timing,
counts — never content). Exit codes: 0 ok, 1 backup failed, 2 configuration
error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

from app.config import settings
from app.observability.audit import audit, configure_audit_logging

_NAME = re.compile(r"^ses-(?P<db>[A-Za-z0-9_]+)-(?P<ts>\d{8}T\d{6}Z)\.dump\.gpg$")
_TS_FORMAT = "%Y%m%dT%H%M%SZ"
# A .partial left by a crashed run is removed once it is clearly abandoned.
_ABANDONED_PARTIAL = timedelta(hours=6)


class BackupConfigError(Exception):
    """The backup cannot run as configured; nothing was attempted."""


class BackupFailed(Exception):
    """pg_dump or gpg failed; no backup file was written."""


@dataclass(frozen=True)
class KeepWindow:
    days: int
    clamped: bool


def keep_window() -> KeepWindow:
    """BACKUP_KEEP_DAYS, capped at the shortest retention window."""
    for name in ("backup_keep_days", "retention_course_grace_days", "retention_telemetry_days"):
        if getattr(settings, name) < 1:
            raise BackupConfigError(f"{name.upper()} must be at least 1")
    cap = min(settings.retention_course_grace_days, settings.retention_telemetry_days)
    return KeepWindow(days=min(settings.backup_keep_days, cap), clamped=settings.backup_keep_days > cap)


def libpq_environment(database_url: str) -> dict[str, str]:
    """DATABASE_URL as libpq environment variables.

    Passed to pg_dump through its environment, never its arguments: another
    user on the server can read a process's command line, not its environment.
    """
    url = urlsplit(database_url)
    if not url.scheme.startswith("postgres"):
        raise BackupConfigError("DATABASE_URL is not a PostgreSQL URL")
    database = unquote(url.path.lstrip("/"))
    if not database:
        raise BackupConfigError("DATABASE_URL names no database")
    env = {"PGDATABASE": database}
    if url.hostname:
        env["PGHOST"] = url.hostname
    if url.port:
        env["PGPORT"] = str(url.port)
    if url.username:
        env["PGUSER"] = unquote(url.username)
    if url.password:
        env["PGPASSWORD"] = unquote(url.password)
    sslmode = parse_qs(url.query).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = sslmode[0]
    return env


def backup_name(database: str, at: datetime) -> str:
    return f"ses-{database}-{at.astimezone(timezone.utc).strftime(_TS_FORMAT)}.dump.gpg"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BackupConfigError(f"{name} is not installed (or not on PATH)")
    return path


def _backup_dir() -> Path:
    directory = Path(settings.backup_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o007:
        raise BackupConfigError(f"{directory} is accessible to other users; chmod 700 it")
    return directory


def _public_key() -> Path:
    if not settings.backup_public_key:
        raise BackupConfigError("BACKUP_PUBLIC_KEY is not set")
    key = Path(settings.backup_public_key)
    if not key.is_file():
        raise BackupConfigError(f"BACKUP_PUBLIC_KEY {key} does not exist")
    return key


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def take_backup(now: datetime) -> Path:
    """Write one encrypted backup and its checksum. Returns the backup's path."""
    directory = _backup_dir()
    key = _public_key()
    pg_dump, gpg = _tool("pg_dump"), _tool("gpg")
    pg_env = libpq_environment(settings.database_url)
    target = directory / backup_name(pg_env["PGDATABASE"], now)
    partial = target.with_name(target.name + ".partial")

    # A throwaway keyring: encrypting to a key file needs no trust database,
    # and the server keeps no keyring for anyone to tamper with.
    with tempfile.TemporaryDirectory(prefix="ses-backup-gnupg-") as home, \
            tempfile.TemporaryFile() as dump_err, tempfile.TemporaryFile() as gpg_err:
        os.chmod(home, 0o700)
        dump = subprocess.Popen(
            [pg_dump, "--format=custom", "--no-password"],
            env={**os.environ, **pg_env}, stdout=subprocess.PIPE, stderr=dump_err,
        )
        encrypt = subprocess.Popen(
            [gpg, "--batch", "--no-tty", "--quiet", "--homedir", home,
             "--trust-model", "always", "--cipher-algo", "AES256",
             # The custom-format dump is already compressed.
             "--compress-algo", "none",
             "--recipient-file", str(key), "--output", str(partial), "--encrypt"],
            stdin=dump.stdout, stderr=gpg_err,
        )
        assert dump.stdout is not None
        dump.stdout.close()  # gpg owns the read end now
        encrypt.wait()
        dump.wait()
        gpgconf = shutil.which("gpgconf")
        if gpgconf:
            subprocess.run([gpgconf, "--homedir", home, "--kill", "all"],
                           capture_output=True, check=False)
        if dump.returncode != 0 or encrypt.returncode != 0:
            partial.unlink(missing_ok=True)
            for label, err in (("pg_dump", dump_err), ("gpg", gpg_err)):
                err.seek(0)
                message = err.read().decode(errors="replace").strip()
                if message:
                    print(f"backup: {label}: {message[-2000:]}", file=sys.stderr)
            failed = "pg_dump" if dump.returncode != 0 else "gpg"
            raise BackupFailed(f"{failed} exited with status "
                               f"{dump.returncode if failed == 'pg_dump' else encrypt.returncode}")

    if not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise BackupFailed("gpg produced no output")
    os.replace(partial, target)
    checksum = target.with_name(target.name + ".sha256")
    checksum.write_text(f"{_sha256(target)}  {target.name}\n")
    return target


def expire(directory: Path, keep_days: int, now: datetime) -> int:
    """Delete backups older than the keep window. Returns how many went.

    Age comes from the timestamp in the file name, not the file's mtime, so
    copying or touching a backup cannot extend its life. Files this job did
    not name are never touched.
    """
    cutoff = now - timedelta(days=keep_days)
    expired = 0
    if not directory.is_dir():
        return 0
    for path in sorted(directory.iterdir()):
        match = _NAME.match(path.name)
        if match:
            taken = datetime.strptime(match["ts"], _TS_FORMAT).replace(tzinfo=timezone.utc)
            if taken < cutoff:
                path.unlink()
                path.with_name(path.name + ".sha256").unlink(missing_ok=True)
                expired += 1
        elif path.name.endswith(".dump.gpg.partial") and _NAME.match(path.name[: -len(".partial")]):
            age = now - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if age > _ABANDONED_PARTIAL:
                path.unlink()
    return expired


def run(*, now: Optional[datetime] = None, expire_only: bool = False) -> int:
    """One run: back up (unless expire_only), then expire. Returns the exit code."""
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    # Everything this run creates is readable by its owner only.
    previous_umask = os.umask(0o077)
    fields: dict = {"stage": "expire_only" if expire_only else "backup"}
    code = 0
    try:
        window = keep_window()
        fields.update(keep_days=window.days, keep_days_clamped=window.clamped)
        if window.clamped:
            print(f"backup: BACKUP_KEEP_DAYS={settings.backup_keep_days} is longer than the "
                  f"retention schedule allows; keeping {window.days} days", file=sys.stderr)
        try:
            if not expire_only:
                target = take_backup(now)
                fields.update(backup_file=target.name, bytes=target.stat().st_size)
        except BackupFailed as e:
            code = 1
            fields["error_type"] = "BackupFailed"
            print(f"backup: failed: {e}", file=sys.stderr)
        finally:
            fields["expired"] = expire(Path(settings.backup_dir), window.days, now)
    except BackupConfigError as e:
        code = 2
        fields["error_type"] = "ConfigurationError"
        print(f"backup: bad configuration: {e}", file=sys.stderr)
    except Exception as e:
        code = 1
        fields["error_type"] = type(e).__name__
        print(f"backup: failed: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        os.umask(previous_umask)

    fields["duration_s"] = round(time.monotonic() - started, 1)
    audit("admin.backup_run", "success" if code == 0 else "failure", **fields)
    print(json.dumps({"exit_code": code, **fields}, indent=2))
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Take an encrypted SES database backup.")
    parser.add_argument("--expire-only", action="store_true",
                        help="only delete backups older than the keep window")
    args = parser.parse_args()
    configure_audit_logging()
    sys.exit(run(expire_only=args.expire_only))
