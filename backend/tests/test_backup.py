"""SR-2026-052 item 3.2 (SEC-BCK-001): encrypted backups, expiry, restore.

The backup job runs with real gpg against a fake pg_dump (so the encryption,
naming, checksum, expiry and audit behaviour are exercised anywhere gpg is
installed). Where the development database container is running, a full round
trip also runs: the real pg_dump of the test database, encrypted here, then
restored by deploy/backup/ses-restore.sh into a new database and compared.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import get_settings
from app.jobs import backup
from app.jobs.backup import BackupConfigError, backup_name, expire, keep_window, libpq_environment
from tests.db import scalar
from tests.test_audit import audit_log  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[2]
RESTORE = REPO / "deploy" / "backup" / "ses-restore.sh"
NOW = datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc)
FAKE_DUMP = b"PGDMP-fake-" + bytes(range(256)) * 64
needs_gpg = pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg not installed")


def _gpg(home: str, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["gpg", "--homedir", home, "--batch", "--no-tty", *args],
                          capture_output=True, check=True, **kw)


@pytest.fixture(scope="module")
def keypair():
    """A throwaway backup key: (GNUPGHOME holding the private key, public key file)."""
    if shutil.which("gpg") is None:
        pytest.skip("gpg not installed")
    home = tempfile.mkdtemp(prefix="gk", dir="/tmp")  # short: agent socket paths
    os.chmod(home, 0o700)
    _gpg(home, "--pinentry-mode", "loopback", "--passphrase", "",
         "--quick-gen-key", "SES backup test <backup-test@invalid>", "rsa2048", "encr", "never")
    public = Path(home) / "backup-public.asc"
    public.write_bytes(_gpg(home, "--armor", "--export", "backup-test@invalid").stdout)
    yield home, public
    subprocess.run(["gpgconf", "--homedir", home, "--kill", "all"], capture_output=True)
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def fake_pg_dump(tmp_path, monkeypatch):
    """pg_dump on PATH that records how it was called and prints FAKE_DUMP."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "pg_dump"
    payload = tmp_path / "payload"
    payload.write_bytes(FAKE_DUMP)
    shim.write_text(f"""#!/bin/sh
printf '%s\\n' "$@" > "{tmp_path}/argv"
env | grep '^PG' > "{tmp_path}/env"
if [ -n "$SHIM_FAIL" ]; then echo "pg_dump: error: connection refused" >&2; exit 1; fi
cat "{payload}"
""")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


@pytest.fixture
def configured(tmp_path, keypair, monkeypatch):
    s = get_settings()
    directory = tmp_path / "backups"
    monkeypatch.setattr(s, "backup_dir", str(directory))
    monkeypatch.setattr(s, "backup_public_key", str(keypair[1]))
    monkeypatch.setattr(s, "backup_keep_days", 14)
    monkeypatch.setattr(s, "retention_course_grace_days", 30)
    monkeypatch.setattr(s, "retention_telemetry_days", 30)
    monkeypatch.setattr(s, "database_url",
                        "postgresql+asyncpg://sis:hunter2%40x%3Ay@db.internal:6543/sis?sslmode=require")
    return directory


def _decrypt(home: str, path: Path) -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory() as out:
        target = Path(out) / "plain"
        status = _gpg(home, "--status-fd", "1", "--output", str(target), "--decrypt", str(path))
        return target.read_bytes(), status.stdout.decode()


# --- configuration ----------------------------------------------------------------------


def test_the_database_url_becomes_libpq_environment():
    env = libpq_environment("postgresql+asyncpg://sis:hunter2%40x%3Ay@db.internal:6543/sis?sslmode=require")
    assert env == {"PGDATABASE": "sis", "PGHOST": "db.internal", "PGPORT": "6543",
                   "PGUSER": "sis", "PGPASSWORD": "hunter2@x:y", "PGSSLMODE": "require"}
    with pytest.raises(BackupConfigError):
        libpq_environment("mysql://x@y/z")
    with pytest.raises(BackupConfigError):
        libpq_environment("postgresql://x@y/")


@pytest.mark.parametrize("keep,grace,telemetry,expected", [
    (14, 30, 30, (14, False)),
    (45, 30, 30, (30, True)),   # never longer than the retention schedule allows
    (14, 30, 7, (7, True)),
])
def test_the_keep_window_is_capped_by_the_retention_schedule(monkeypatch, keep, grace, telemetry, expected):
    s = get_settings()
    monkeypatch.setattr(s, "backup_keep_days", keep)
    monkeypatch.setattr(s, "retention_course_grace_days", grace)
    monkeypatch.setattr(s, "retention_telemetry_days", telemetry)
    window = keep_window()
    assert (window.days, window.clamped) == expected


def test_a_zero_keep_window_is_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_keep_days", 0)
    with pytest.raises(BackupConfigError):
        keep_window()


# --- taking a backup --------------------------------------------------------------------------


@needs_gpg
def test_a_backup_is_the_dump_encrypted_with_aes256_to_the_public_key(
    configured, keypair, fake_pg_dump, audit_log  # noqa: F811
):
    assert backup.run(now=NOW) == 0
    target = configured / backup_name("sis", NOW)
    assert target.name == "ses-sis-20261002T040000Z.dump.gpg"

    data = target.read_bytes()
    assert b"PGDMP" not in data  # nothing in the clear
    plain, status = _decrypt(keypair[0], target)
    assert plain == FAKE_DUMP
    # DECRYPTION_INFO <mdc> <cipher> <aead>: cipher 9 is AES-256.
    info = next(line for line in status.splitlines() if "DECRYPTION_INFO" in line).split()
    assert info[3] == "9", status

    # Only the owner can read it; its checksum is in sha256sum format.
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(configured.stat().st_mode) == 0o700
    checksum = (configured / (target.name + ".sha256")).read_text()
    assert checksum == f"{backup._sha256(target)}  {target.name}\n"
    assert not list(configured.glob("*.partial"))

    # The password went through the environment, never the command line.
    argv = (fake_pg_dump / "argv").read_text()
    assert "hunter2" not in argv
    assert "PGPASSWORD=hunter2@x:y" in (fake_pg_dump / "env").read_text()
    assert "--format=custom" in argv

    (event,) = audit_log.named("admin.backup_run")
    assert event["outcome"] == "success" and event["backup_file"] == target.name
    assert event["bytes"] == len(data) and event["keep_days"] == 14


@needs_gpg
def test_a_failed_dump_leaves_no_file_and_still_expires_old_backups(
    configured, fake_pg_dump, monkeypatch, audit_log  # noqa: F811
):
    configured.mkdir(mode=0o700)
    old = configured / backup_name("sis", NOW - timedelta(days=20))
    old.write_bytes(b"x")
    monkeypatch.setenv("SHIM_FAIL", "1")
    assert backup.run(now=NOW) == 1
    assert sorted(p.name for p in configured.iterdir()) == []
    (event,) = audit_log.named("admin.backup_run")
    assert event["outcome"] == "failure" and event["error_type"] == "BackupFailed"
    assert event["expired"] == 1


def test_no_public_key_is_a_configuration_error_and_old_backups_still_expire(
    configured, monkeypatch, audit_log  # noqa: F811
):
    configured.mkdir(mode=0o700)
    (configured / backup_name("sis", NOW - timedelta(days=20))).write_bytes(b"x")
    monkeypatch.setattr(get_settings(), "backup_public_key", "")
    assert backup.run(now=NOW) == 2
    (event,) = audit_log.named("admin.backup_run")
    assert event["outcome"] == "failure" and event["error_type"] == "ConfigurationError"
    # Retention is kept even when backups cannot be taken.
    assert event["expired"] == 1 and list(configured.iterdir()) == []


def test_a_directory_others_can_read_is_refused(configured, fake_pg_dump, capsys):
    configured.mkdir()
    configured.chmod(0o755)
    assert backup.run(now=NOW) == 2
    assert "accessible to other users" in capsys.readouterr().err
    assert list(configured.iterdir()) == []


# --- expiry ------------------------------------------------------------------------------------


def test_expiry_goes_by_the_name_not_the_file_date(tmp_path):
    def make(name: str) -> Path:
        path = tmp_path / name
        path.write_bytes(b"x")
        return path

    old = make(backup_name("sis", NOW - timedelta(days=15)))
    make(old.name + ".sha256")
    os.utime(old)  # touched today: still expired
    kept = make(backup_name("sis", NOW - timedelta(days=13)))
    stranger = make("notes.txt")
    stale = make(backup_name("sis", NOW - timedelta(days=2)) + ".partial")
    os.utime(stale, (0, 0))
    fresh = make(backup_name("sis", NOW) + ".partial")

    assert expire(tmp_path, 14, NOW) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [kept.name, stranger.name, fresh.name]
    )


# --- the full round trip, where the database container is available -----------------------------

CONTAINER = "stakeholder-interview-sim-db-1"


def _container_ready() -> bool:
    if shutil.which("docker") is None or shutil.which("gpg") is None:
        return False
    probe = subprocess.run(["docker", "exec", CONTAINER, "pg_restore", "--version"],
                           capture_output=True)
    return probe.returncode == 0


def _in_container(*args: str, user: str = "postgres", env: dict | None = None,
                  check: bool = True) -> subprocess.CompletedProcess:
    flags = [f"-e{k}={v}" for k, v in (env or {}).items()]
    return subprocess.run(["docker", "exec", "-u", user, *flags, CONTAINER, *args],
                          capture_output=True, text=True, check=check)


@pytest.mark.skipif(not _container_ready(), reason="no database container with pg tools")
def test_a_backup_restores_into_a_new_database(tmp_path, keypair, monkeypatch, audit_log):  # noqa: F811
    s = get_settings()
    source_db = s.database_url.rsplit("/", 1)[-1]
    restored_db = f"{source_db}_restore_test"
    _in_container("dropdb", "--if-exists", restored_db)

    # pg_dump is the container's, reached through a shim; gpg is this machine's.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pg_dump").write_text(
        f'#!/bin/sh\nexec docker exec -i -e PGUSER -e PGPASSWORD -e PGDATABASE {CONTAINER} pg_dump -h localhost "$@"\n'
    )
    (bin_dir / "pg_dump").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(s, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(s, "backup_public_key", str(keypair[1]))

    assert backup.run() == 0
    (made,) = (tmp_path / "backups").glob("*.dump.gpg")
    tables = ["interview_sessions", "session_evaluations", "session_flags", "participants",
              "identity.users", "deletion_log"]
    before = {t: scalar(f"SELECT count(*) FROM {t}") for t in tables}

    # The operator's side: the backup and its checksum on the server, the
    # private key in a temporary keyring, the restore script run as postgres.
    work = "/tmp/ses-restore-test"
    _in_container("rm", "-rf", work, user="root")
    _in_container("mkdir", "-p", f"{work}/gnupg", user="root")
    for path in (made, made.with_name(made.name + ".sha256"), RESTORE):
        subprocess.run(["docker", "cp", str(path), f"{CONTAINER}:{work}/"], check=True)
    secret = _gpg(keypair[0], "--pinentry-mode", "loopback", "--passphrase", "",
                  "--armor", "--export-secret-keys", "backup-test@invalid").stdout
    subprocess.run(["docker", "exec", "-i", CONTAINER, "sh", "-c", f"cat > {work}/key.asc"],
                   input=secret, check=True)
    _in_container("chown", "-R", "postgres", work, user="root")
    _in_container("chmod", "700", f"{work}/gnupg")
    gnupg = {"GNUPGHOME": f"{work}/gnupg"}
    _in_container("gpg", "--batch", "--import", f"{work}/key.asc", env=gnupg)

    try:
        # Refuses a tampered backup and an existing database; restores otherwise.
        _in_container("cp", f"{work}/{made.name}.sha256", f"{work}/good.sha256")
        _in_container("sh", "-c", f"echo 0000 > {work}/{made.name}.sha256")
        tampered = _in_container("bash", f"{work}/ses-restore.sh", f"{work}/{made.name}",
                                 restored_db, "postgres", env=gnupg, check=False)
        assert tampered.returncode == 65 and "checksum" in tampered.stderr
        _in_container("cp", f"{work}/good.sha256", f"{work}/{made.name}.sha256")

        live = _in_container("bash", f"{work}/ses-restore.sh", f"{work}/{made.name}",
                             source_db, "postgres", env=gnupg, check=False)
        assert live.returncode == 73 and "already exists" in live.stderr

        done = _in_container("bash", f"{work}/ses-restore.sh", f"{work}/{made.name}",
                             restored_db, "postgres", env=gnupg, check=False)
        assert done.returncode == 0, done.stderr
        assert f"backup taken:    {made.name.split('-')[-1].split('.')[0]}" in done.stdout
        for table, count in before.items():
            got = _in_container("psql", "-XAt", "-d", restored_db, "-c",
                                f"SELECT count(*) FROM {table}").stdout.strip()
            assert int(got) == count, table
        revision = _in_container("psql", "-XAt", "-d", restored_db, "-c",
                                 "SELECT version_num FROM alembic_version").stdout.strip()
        assert revision == scalar("SELECT version_num FROM alembic_version")
    finally:
        _in_container("dropdb", "--if-exists", restored_db, check=False)
        _in_container("rm", "-rf", work, user="root", check=False)
