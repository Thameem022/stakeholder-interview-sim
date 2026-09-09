"""The shared error shape for everything auth-related.

Lives here rather than in app/api/auth.py so that app/auth/* can raise it
without importing from app/api/*, which would invert the layering the rest of
the package follows.
"""

from __future__ import annotations

from fastapi import HTTPException


def auth_error(code: int, slug: str, message: str) -> HTTPException:
    """A machine-readable code the frontend branches on, plus display copy.

    The message is written to be safe to show verbatim — where being specific
    would reveal whether an account or a record exists, the copy is
    deliberately vague and the detail goes to the log instead.
    """
    return HTTPException(status_code=code, detail={"code": slug, "message": message})
