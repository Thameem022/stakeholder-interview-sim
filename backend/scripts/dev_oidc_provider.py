"""A stand-in for Microsoft Entra ID, for LOCAL DEVELOPMENT ONLY.

SES has exactly one way to sign in — OIDC against Entra — and this lets that
same code path run on a laptop without a tenant. It speaks the slice of the
protocol SES uses (discovery, authorize with PKCE, token, JWKS) at Entra's URL
shapes, and signs ID tokens with a key generated at startup.

    cd backend
    uv run python -m scripts.dev_oidc_provider          # http://127.0.0.1:9999

and in .env:

    ENTRA_AUTHORITY=http://127.0.0.1:9999
    ENTRA_TENANT_ID=00000000-0000-0000-0000-00000000dev0
    ENTRA_CLIENT_ID=ses-local-dev
    ENTRA_CLIENT_SECRET=not-a-secret
    ENTRA_REDIRECT_URI=http://localhost:5173/api/auth/callback

It binds to loopback only and refuses anything else; production refuses any
authority but Microsoft's (app/config.py), so it can never stand in for real
sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import sys
import time
import uuid
from typing import Optional
from urllib.parse import urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

HOST, PORT = "127.0.0.1", 9999
BASE = f"http://{HOST}:{PORT}"
ROLES = ["Student", "Instructor", "StudyPersonnel", "ExportApprover", "SupportOwner"]

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_kid = secrets.token_hex(8)
_codes: dict[str, dict] = {}

app = FastAPI(title="SES dev OIDC provider (NOT FOR PRODUCTION)")


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@app.get("/{tenant}/v2.0/.well-known/openid-configuration")
def discovery(tenant: str) -> dict:
    return {
        "issuer": f"{BASE}/{tenant}/v2.0",
        "authorization_endpoint": f"{BASE}/{tenant}/oauth2/v2.0/authorize",
        "token_endpoint": f"{BASE}/{tenant}/oauth2/v2.0/token",
        "jwks_uri": f"{BASE}/{tenant}/discovery/v2.0/keys",
        "response_types_supported": ["code"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/{tenant}/discovery/v2.0/keys")
def keys(tenant: str) -> dict:
    pub = _key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": _kid,
                      "n": _b64(pub.n), "e": _b64(pub.e)}]}


@app.get("/{tenant}/oauth2/v2.0/authorize", response_class=HTMLResponse)
def authorize_form(tenant: str, request: Request) -> str:
    q = request.query_params
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        raise HTTPException(400, "PKCE (S256) required")
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(q.get(k, ""))}">'
        for k in ("client_id", "redirect_uri", "state", "nonce", "code_challenge")
    )
    boxes = "".join(
        f'<label><input type="checkbox" name="roles" value="{r}"{" checked" if r == "Student" else ""}> {r}</label><br>'
        for r in ROLES
    )
    return f"""<!doctype html><meta charset="utf-8"><title>Dev sign-in</title>
<body style="font-family:system-ui;max-width:28rem;margin:3rem auto">
<p style="background:#fde68a;padding:.5rem"><b>Development identity provider.</b>
Not Microsoft Entra. Never used in production.</p>
<form method="post">{hidden}
<p><label>WPI email<br><input name="email" type="email" required value="dev.student@wpi.edu"></label></p>
<p><label>First name<br><input name="given_name" value="Dev"></label></p>
<p><label>Last name<br><input name="family_name" value="Student"></label></p>
<fieldset><legend>Entra app roles</legend>{boxes}</fieldset>
<p><button name="decision" value="allow">Sign in</button>
<button name="decision" value="deny">Cancel</button></p></form></body>"""


@app.post("/{tenant}/oauth2/v2.0/authorize")
def authorize_submit(
    tenant: str,
    client_id: str = Form(...), redirect_uri: str = Form(...), state: str = Form(...),
    nonce: str = Form(...), code_challenge: str = Form(...), decision: str = Form("allow"),
    email: str = Form(""), given_name: str = Form(""), family_name: str = Form(""),
    roles: Optional[list[str]] = Form(None),
) -> RedirectResponse:
    if decision != "allow":
        return RedirectResponse(f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}", 302)
    code = secrets.token_urlsafe(24)
    _codes[code] = {
        "tenant": tenant, "client_id": client_id, "redirect_uri": redirect_uri, "nonce": nonce,
        "challenge": code_challenge, "expires": time.time() + 120,
        "claims": {
            "email": email.strip().lower(), "preferred_username": email.strip().lower(),
            "given_name": given_name, "family_name": family_name,
            "name": f"{given_name} {family_name}".strip(),
            # Stable per address, like a real object id is stable per person.
            "oid": str(uuid.uuid5(uuid.NAMESPACE_URL, "ses-dev:" + email.strip().lower())),
            "roles": [r for r in (roles or []) if r in ROLES],
        },
    }
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", 302)


@app.post("/{tenant}/oauth2/v2.0/token")
def token(
    tenant: str, grant_type: str = Form(...), code: str = Form(...), redirect_uri: str = Form(...),
    client_id: str = Form(...), code_verifier: str = Form(...),
) -> dict:
    grant = _codes.pop(code, None)
    if grant is None or grant["expires"] < time.time() or grant_type != "authorization_code":
        raise HTTPException(400, "invalid_grant")
    if grant["client_id"] != client_id or grant["redirect_uri"] != redirect_uri:
        raise HTTPException(400, "invalid_grant")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    if challenge != grant["challenge"]:
        raise HTTPException(400, "invalid_grant: PKCE verification failed")
    now = int(time.time())
    claims = {
        **grant["claims"], "iss": f"{BASE}/{tenant}/v2.0", "aud": client_id, "tid": tenant,
        "sub": grant["claims"]["oid"], "nonce": grant["nonce"], "iat": now, "nbf": now, "exp": now + 3600,
    }
    id_token = jwt.encode(claims, _key, algorithm="RS256", headers={"kid": _kid})
    return {"token_type": "Bearer", "id_token": id_token, "expires_in": 3600}


if __name__ == "__main__":
    if HOST not in ("127.0.0.1", "localhost"):
        sys.exit("refusing to bind the dev identity provider to a non-loopback address")
    uvicorn.run(app, host=HOST, port=PORT)
