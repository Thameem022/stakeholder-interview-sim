"""Synchronous DB helpers for tests.

Deliberately psycopg2 rather than the app's asyncpg pool: these run outside the
event loop TestClient drives, so they can set up and assert on state without
fighting it for the connection.

⚠️ The test database IS the development database. Every delete here is scoped
to TEST_PREFIX addresses — an unscoped `DELETE FROM interview_sessions` would
take the real research data with it.
"""

from __future__ import annotations

import psycopg2

from app.config import settings

TEST_PREFIX = "sis-test-"
_TEST_LIKE = TEST_PREFIX + "%"


def _dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def sql(query: str, args: tuple = ()) -> None:
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(query, args)


def scalar(query: str, args: tuple = ()):
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(query, args)
        row = cur.fetchone()
        return row[0] if row else None


def cleanup_test_rows() -> None:
    """Remove everything this suite created, in FK-safe order.

    interview_sessions must go before users: the FK added in 0005 is
    ON DELETE RESTRICT, so deleting a user who owns a session raises
    ForeignKeyViolation. Because this runs on both sides of the `client`
    fixture, that failure would otherwise cascade into every later test.
    session_evaluations cascades from interview_sessions.
    """
    sql(
        "DELETE FROM interview_sessions WHERE user_id IN "
        "(SELECT id FROM users WHERE email LIKE %s)",
        (_TEST_LIKE,),
    )
    # auth_sessions cascades from users.
    sql("DELETE FROM users WHERE email LIKE %s", (_TEST_LIKE,))
    sql("DELETE FROM pending_registrations WHERE email LIKE %s", (_TEST_LIKE,))
    # Rate-limit buckets embed the address, and IP buckets are shared by every
    # test, so clear the whole table or later tests inherit earlier counts.
    sql("DELETE FROM auth_rate_limits")
