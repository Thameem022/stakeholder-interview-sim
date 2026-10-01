"""An in-process stand-in for Microsoft Entra ID, for tests.

Serves discovery and JWKS, records each authorize request's PKCE challenge
and nonce, verifies the code verifier at the token endpoint exactly as Entra
does, and signs ID tokens with a key generated per test. `token_overrides`
lets a test corrupt any claim (or the header, or the key) to prove the app
refuses it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import oidc

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "ses-test-client"
AUTHORITY = "https://login.microsoftonline.com"
REDIRECT_URI = "https://ses.example.test/api/auth/callback"
ISSUER = f"{AUTHORITY}/{TENANT}/v2.0"


def _b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


class FakeEntra:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = secrets.token_hex(6)
        self.grants: dict[str, dict] = {}
        self.token_overrides: dict[str, Any] = {}
        self.header_overrides: dict[str, Any] = {}
        self.sign_with: Optional[Any] = None  # a different key, to forge a signature
        self.token_requests: list[dict] = []

    # --- what the app fetches --------------------------------------------------------

    def discovery(self) -> dict:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{AUTHORITY}/{TENANT}/oauth2/v2.0/authorize",
            "token_endpoint": f"{AUTHORITY}/{TENANT}/oauth2/v2.0/token",
            "jwks_uri": f"{AUTHORITY}/{TENANT}/discovery/v2.0/keys",
        }

    def jwks(self) -> dict:
        pub = self.key.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "use": "sig", "kid": self.kid, "n": _b64(pub.n), "e": _b64(pub.e)}]}

    async def get_json(self, url: str) -> dict:
        if url.endswith("/.well-known/openid-configuration"):
            return self.discovery()
        if url.endswith("/discovery/v2.0/keys"):
            return self.jwks()
        raise AssertionError(f"unexpected GET {url}")

    async def post_form(self, url: str, data: dict) -> dict:
        self.token_requests.append(dict(data))
        grant = self.grants.pop(data.get("code"), None)
        if grant is None:
            raise oidc.OIDCError("token_exchange_failed", "unknown code")
        verifier = data.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if challenge != grant["challenge"] or data.get("client_id") != CLIENT_ID:
            raise oidc.OIDCError("token_exchange_failed", "PKCE or client mismatch")
        return {"id_token": self.id_token(grant["nonce"], grant["claims"])}

    # --- the user's side of the redirect -------------------------------------------------

    def approve(self, authorize_url: str, **claims: Any) -> tuple[str, str]:
        """The user signs in at Entra. Returns (code, state) for the callback."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        code = secrets.token_urlsafe(16)
        self.grants[code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "claims": claims}
        return code, q["state"]

    def id_token(self, nonce: str, claims: dict) -> str:
        now = int(time.time())
        email = claims.get("email", "sis-test-sso@wpi.edu")
        body = {
            "iss": ISSUER, "aud": CLIENT_ID, "tid": TENANT, "nonce": nonce,
            "iat": now, "nbf": now, "exp": now + 3600,
            "oid": claims.get("oid") or str(uuid.uuid5(uuid.NAMESPACE_URL, email)),
            "sub": secrets.token_hex(8),
            "preferred_username": email, "email": email,
            "given_name": "Sam", "family_name": "Rivera",
            "roles": ["Student"],
            **{k: v for k, v in claims.items() if k != "email"},
            **self.token_overrides,
        }
        body = {k: v for k, v in body.items() if v is not ...}
        headers = {"kid": self.kid, **self.header_overrides}
        alg = headers.pop("alg", "RS256")
        key: Any = self.sign_with or self.key
        if alg == "HS256":
            key = "a-shared-secret-an-attacker-picked-1234567890"
        elif alg == "none":
            key = None
        return jwt.encode(body, key, algorithm=alg, headers=headers)
