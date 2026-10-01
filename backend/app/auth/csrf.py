"""Double-submit CSRF protection for the cookie-authenticated API.

The session cookie is sent by the browser on its own, so a request carrying it
proves nothing about which page made it. Every state-changing /api call must
therefore also echo the CSRF cookie back in the X-CSRF-Token header. Only
script running on our own origin can read that cookie, and a cross-site form or
fetch cannot set a custom header without a CORS preflight we do not grant.

The cookie is issued on any response to a request that arrived without one, so
the page has it after its first call (in practice, /api/auth/me on load).
"""

from __future__ import annotations

import secrets

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import settings
from app.observability.audit import audit

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _rejection() -> JSONResponse:
    # Same {detail: {code, message}} shape as auth_error, so the frontend reads
    # it like any other API failure. 403, not 401: the session may be fine.
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "detail": {
                "code": "csrf_failed",
                "message": "This request could not be verified. Reload the page and try again.",
            }
        },
    )


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        cookie_name = settings.csrf_cookie_name
        cookie = request.cookies.get(cookie_name)

        if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
            header = request.headers.get(CSRF_HEADER)
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                audit(
                    "auth.csrf_rejected",
                    "denied",
                    request=request,
                    reason="missing" if not (cookie and header) else "mismatch",
                    origin=request.headers.get("origin"),
                )
                return _rejection()

        response = await call_next(request)

        if not cookie:
            response.set_cookie(
                key=cookie_name,
                value=secrets.token_urlsafe(32),
                httponly=False,
                samesite="lax",
                secure=settings.is_production,
                path="/",
            )
        return response
