"""SR-2026-052 items 2.3 / 3.2 / 3.3: what the deployment files promise.

Static checks on deploy/: the TLS floor and cipher list, the security headers,
the backup schedule — and that SES still sends no email. The live checks
(TLS scan, at-rest encryption, a restore on the server) are in
deploy/WPI_DEPLOY.md; deploy/check_tls.sh is the scan.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.config import Settings

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"
VHOST = (DEPLOY / "apache" / "stakeholder-engagement-simulator.conf").read_text()
SYSTEMD = DEPLOY / "systemd"


def _directives(name: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(rf"^\s*{name}\s+(.+)$", VHOST, re.M)]


# --- 2.3 TLS and headers ---------------------------------------------------------------------


def test_tls_is_1_2_minimum_and_1_3_enabled():
    assert _directives("SSLProtocol") == ["-all +TLSv1.2 +TLSv1.3"]


def test_tls_1_2_offers_only_forward_secret_aead_ciphers():
    (tls12,) = [v for v in _directives("SSLCipherSuite") if not v.startswith("TLSv1.3")]
    ciphers = tls12.split(":")
    assert ciphers
    for cipher in ciphers:
        assert cipher.startswith("ECDHE-"), cipher
        assert re.search(r"GCM|CHACHA20-POLY1305", cipher), cipher
        assert not re.search(r"CBC|SHA$|RC4|3DES|NULL|EXPORT|anon", cipher), cipher


def test_tls_1_3_suites_are_the_standard_aead_ones():
    (tls13,) = [v for v in _directives("SSLCipherSuite") if v.startswith("TLSv1.3")]
    assert set(tls13.split()[1].split(":")) <= {
        "TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384", "TLS_CHACHA20_POLY1305_SHA256",
    }


def _header(name: str) -> str:
    found = re.findall(rf'^\s*Header always set {re.escape(name)} "([^"]+)"', VHOST, re.M)
    assert len(found) == 1, f"{name} must be set exactly once, with `always`"
    return found[0]


def test_hsts_and_the_security_headers_are_set():
    assert int(re.search(r"max-age=(\d+)", _header("Strict-Transport-Security")).group(1)) >= 31536000
    assert _header("X-Content-Type-Options") == "nosniff"
    assert _header("X-Frame-Options") == "DENY"
    assert _header("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "microphone=(self)" in _header("Permissions-Policy")


def test_the_content_security_policy_allows_no_inline_or_foreign_script():
    csp = dict(
        (part.split()[0], part.split()[1:]) for part in _header("Content-Security-Policy").split("; ")
    )
    assert csp["default-src"] == ["'self'"]
    assert csp["script-src"] == ["'self'"]
    assert csp["object-src"] == ["'none'"] and csp["frame-ancestors"] == ["'none'"]
    assert not any("unsafe" in v for values in csp.values() for v in values)
    # The interview WebSocket is the site's own.
    assert "wss://stakeholder-engagement-simulator.wpi.edu" in csp["connect-src"]


def test_the_websocket_route_comes_before_the_catch_all():
    passes = _directives("ProxyPass")
    assert passes[0].startswith("/api/realtime/stream ") and passes[-1].startswith("/ ")


# --- 3.2 backups -----------------------------------------------------------------------------


def _calendar(unit: str) -> str:
    return re.search(r"^OnCalendar=\*-\*-\* (\d\d:\d\d):\d\d$",
                     (SYSTEMD / unit).read_text(), re.M).group(1)


def test_the_backup_runs_daily_after_the_retention_job():
    # So a backup holds nothing the retention job has just deleted.
    assert _calendar("stakeholder-engagement-simulator-backup.timer") > \
        _calendar("stakeholder-engagement-simulator-retention.timer")
    assert "Persistent=true" in (SYSTEMD / "stakeholder-engagement-simulator-backup.timer").read_text()


def test_the_backup_unit_runs_the_job_and_writes_only_the_backup_directory():
    unit = (SYSTEMD / "stakeholder-engagement-simulator-backup.service").read_text()
    assert re.search(r"^ExecStart=.*python -m app\.jobs\.backup$", unit, re.M)
    assert re.search(r"^ProtectSystem=strict$", unit, re.M)
    assert re.findall(r"^ReadWritePaths=(.+)$", unit, re.M) == [Settings.model_fields["backup_dir"].default]


def test_backups_are_kept_no_longer_than_the_retention_windows_by_default():
    defaults = {k: Settings.model_fields[k].default for k in
                ("backup_keep_days", "retention_course_grace_days", "retention_telemetry_days")}
    assert defaults["backup_keep_days"] <= min(defaults["retention_course_grace_days"],
                                               defaults["retention_telemetry_days"])


@pytest.mark.parametrize("script", ["backup/ses-restore.sh", "check_tls.sh"])
def test_the_operator_scripts_parse_and_are_executable(script):
    path = DEPLOY / script
    assert path.stat().st_mode & 0o111, f"{script} is not executable"
    subprocess.run(["bash", "-n", str(path)], check=True)


# --- 3.3 email -------------------------------------------------------------------------------

_MAIL = re.compile(
    r"smtplib|aiosmtplib|sendmail|send_mail|sendgrid|mailgun|postmark|fastapi_mail|"
    r"email\.mime|EmailMessage|client\(\s*[\"']sesv?2?[\"']",
    re.I,
)
_MAIL_PACKAGES = re.compile(
    r'^name = "(aiosmtplib|sendgrid|fastapi-mail|emails|yagmail|mailgun|postmarker|flask-mail)"$', re.M
)


def test_the_application_sends_no_email():
    """SES sends no mail (SEC-INT-001). If that changes, it must go through the
    institution's relay from a registered sender and carry links only — never
    transcript, feedback or reflection text — and this test must change with it."""
    hits = []
    for root in (REPO / "backend" / "app", REPO / "backend" / "scripts", REPO / "frontend" / "src"):
        for path in root.rglob("*"):
            if path.suffix in {".py", ".ts", ".tsx"}:
                for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                    if _MAIL.search(line):
                        hits.append(f"{path.relative_to(REPO)}:{n}")
    assert hits == []
    assert not _MAIL_PACKAGES.findall((REPO / "backend" / "uv.lock").read_text())
