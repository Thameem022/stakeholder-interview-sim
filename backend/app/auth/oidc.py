"""Microsoft Entra ID (OIDC) — authorization code flow with PKCE.

SR-2026-052 item 1.3 (SEC-IAM-001). The only way to sign in.

Everything that touches the identity provider lives here: provider discovery,
signing keys, PKCE, and ID-token validation. The HTTP calls go through two
small functions (_get_json, _post_form) so tests can stand in for Entra
without a network.

An ID token is accepted only if ALL of these hold:
  - RS256 signature by a key in the tenant's published JWKS (no "none", no HMAC)
  - iss  == the tenant's issuer from discovery
  - aud  == our client id
  - exp / nbf / iat within 60 s of now
  - nonce == the nonce we generated for this sign-in
  - tid  == our tenant id, and an oid (the stable user id) is present
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import jwt

from app.config import settings

# Seconds of clock skew tolerated on exp / nbf / iat.
LEEWAY = 60
# Signing keys rotate; refetch at most this often unless an unknown kid shows up.
_JWKS_TTL = 3600

APP_ROLE_MAP: dict[str, Optional[str]] = {
    # Entra app role value -> application role. "Student" is a valid assignment
    # that carries no extra application role.
    "Student": None,
    "Instructor": "instructor",
    "StudyPersonnel": "study_personnel",
    "ExportApprover": "export_approver",
    "SupportOwner": "support_owner",
}


class OIDCError(Exception):
    """A sign-in that must be refused. `code` is safe to show the user."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


# --- HTTP (patched in tests) -----------------------------------------------------


async def _get_json(url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def _post_form(url: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, data=data)
        if r.status_code >= 400:
            # The error body is Entra's (error / error_description); it names
            # what went wrong without carrying a credential.
            raise OIDCError("token_exchange_failed", f"HTTP {r.status_code}")
        return r.json()


# --- discovery and keys --------------------------------------------------------------

_metadata: Optional[ProviderMetadata] = None
_jwks: Optional[dict] = None
_jwks_fetched_at = 0.0


def discovery_url() -> str:
    return (
        f"{settings.entra_authority.rstrip('/')}/{settings.entra_tenant_id}"
        "/v2.0/.well-known/openid-configuration"
    )


async def provider() -> ProviderMetadata:
    global _metadata
    if _metadata is None:
        doc = await _get_json(discovery_url())
        _metadata = ProviderMetadata(
            issuer=doc["issuer"],
            authorization_endpoint=doc["authorization_endpoint"],
            token_endpoint=doc["token_endpoint"],
            jwks_uri=doc["jwks_uri"],
        )
    return _metadata


async def signing_keys(force: bool = False) -> dict:
    global _jwks, _jwks_fetched_at
    if force or _jwks is None or time.monotonic() - _jwks_fetched_at > _JWKS_TTL:
        _jwks = await _get_json((await provider()).jwks_uri)
        _jwks_fetched_at = time.monotonic()
    return _jwks


def reset_caches() -> None:
    global _metadata, _jwks, _jwks_fetched_at
    _metadata, _jwks, _jwks_fetched_at = None, None, 0.0


# --- PKCE, state, nonce -------------------------------------------------------------


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    # RFC 7636: 43-128 characters from the unreserved set. 64 bytes -> 86 chars.
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def authorization_url(*, state: str, nonce: str, verifier: str) -> str:
    meta = await provider()
    query = urlencode({
        "client_id": settings.entra_client_id,
        "response_type": "code",
        "redirect_uri": settings.entra_redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    })
    return f"{meta.authorization_endpoint}?{query}"


# --- token exchange and validation ------------------------------------------------------


async def exchange_code(code: str, verifier: str) -> str:
    """Swap the authorization code for tokens; return the raw ID token."""
    meta = await provider()
    body = await _post_form(meta.token_endpoint, {
        "grant_type": "authorization_code",
        "client_id": settings.entra_client_id,
        "client_secret": settings.entra_client_secret,
        "code": code,
        "redirect_uri": settings.entra_redirect_uri,
        "code_verifier": verifier,
        "scope": "openid profile email",
    })
    token = body.get("id_token")
    if not token:
        raise OIDCError("token_exchange_failed", "no id_token in response")
    return token


async def validate_id_token(token: str, *, nonce: str) -> dict[str, Any]:
    meta = await provider()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise OIDCError("invalid_token", type(e).__name__) from None
    if header.get("alg") != "RS256":
        raise OIDCError("invalid_token", f"alg {header.get('alg')!r} not allowed")

    key_dict = _find_key(await signing_keys(), header.get("kid"))
    if key_dict is None:
        # Entra rotates keys; one refetch before giving up.
        key_dict = _find_key(await signing_keys(force=True), header.get("kid"))
    if key_dict is None:
        raise OIDCError("invalid_token", "unknown signing key")

    try:
        claims = jwt.decode(
            token,
            key=jwt.PyJWK(key_dict).key,
            algorithms=["RS256"],
            audience=settings.entra_client_id,
            issuer=meta.issuer,
            leeway=LEEWAY,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise OIDCError("invalid_token", "expired") from None
    except jwt.InvalidAudienceError:
        raise OIDCError("invalid_token", "audience") from None
    except jwt.InvalidIssuerError:
        raise OIDCError("invalid_token", "issuer") from None
    except jwt.PyJWTError as e:
        raise OIDCError("invalid_token", type(e).__name__) from None

    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("invalid_token", "nonce")
    if claims.get("tid") != settings.entra_tenant_id:
        raise OIDCError("invalid_token", "tenant")
    if not claims.get("oid"):
        raise OIDCError("invalid_token", "no oid")
    return claims


def _find_key(jwks: dict, kid: Optional[str]) -> Optional[dict]:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid and key.get("kty") == "RSA":
            return key
    return None


# --- claims -> application identity ------------------------------------------------------


@dataclass(frozen=True)
class SignInIdentity:
    subject: str          # oid
    email: str
    first_name: str
    last_name: str
    roles: frozenset[str]  # application roles beyond student


def identity_from_claims(claims: dict[str, Any]) -> SignInIdentity:
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not email.endswith("@" + settings.auth_email_domain.lower()):
        raise OIDCError("wrong_domain")

    token_roles = [r for r in claims.get("roles") or [] if isinstance(r, str)]
    known = [r for r in token_roles if r in APP_ROLE_MAP]
    if settings.entra_require_app_role and not known:
        # Entra's "assignment required" should stop this at the door; this is
        # the app refusing to rely on that alone.
        raise OIDCError("not_assigned")

    first = str(claims.get("given_name") or "").strip()
    last = str(claims.get("family_name") or "").strip()
    if not (first or last):
        first, _, last = str(claims.get("name") or "").strip().partition(" ")
    return SignInIdentity(
        subject=str(claims["oid"]),
        email=email,
        first_name=first[:100],
        last_name=last[:100],
        roles=frozenset(r for r in (APP_ROLE_MAP[k] for k in known) if r),
    )
