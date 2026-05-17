from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return _pool


async def init_pool(dsn: str) -> None:
    global _pool

    async def _init_connection(conn):
        await register_vector(conn)

    raw_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    _pool = await asyncpg.create_pool(
        raw_dsn,
        min_size=1,
        max_size=5,
        init=_init_connection,
    )
    async with _pool.acquire() as conn:
        await conn.execute("SELECT 1")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
