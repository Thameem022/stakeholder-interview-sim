"""
pgvector-backed retrieval. Replaces FAISS halves of rag_engine.py.

Public API:
  - search_persona(persona_id, query, k, source=None)  → list[Chunk]
  - search_world(query, k)                              → list[Chunk]
  - embed(texts)                                        → list[list[float]]

Chunks returned with shape: {chunk_id, text, metadata, score, source}
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from app.ai.embeddings import embed_texts
from app.db import get_pool


async def embed(texts: List[str]) -> List[List[float]]:
    """Titan V2 embeddings on Bedrock (app/ai/embeddings.py)."""
    return await embed_texts(texts)


async def embed_one(text: str) -> List[float]:
    vectors = await embed([text])
    return vectors[0]


async def search_persona(
    persona_id: str,
    query: str,
    k: int = 5,
    source: Optional[str] = None,
    query_vec: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Top-k semantic search over a persona's chunks (optionally filtered by source)."""
    if query_vec is None:
        query_vec = np.array(await embed_one(query), dtype="float32")
    else:
        query_vec = np.asarray(query_vec, dtype="float32")

    pool = await get_pool()
    async with pool.acquire() as conn:
        if source is None:
            rows = await conn.fetch(
                """
                SELECT chunk_id, text, metadata, source,
                       1 - (embedding <=> $1) AS score
                FROM persona_chunks
                WHERE persona_id = $2
                ORDER BY embedding <=> $1
                LIMIT $3
                """,
                query_vec, persona_id, k,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT chunk_id, text, metadata, source,
                       1 - (embedding <=> $1) AS score
                FROM persona_chunks
                WHERE persona_id = $2 AND source = $3
                ORDER BY embedding <=> $1
                LIMIT $4
                """,
                query_vec, persona_id, source, k,
            )

    return [
        {
            "chunk_id": r["chunk_id"],
            "text": r["text"],
            "metadata": r["metadata"],
            "source": r["source"],
            "score": float(r["score"]),
        }
        for r in rows
    ]


async def search_world(
    query: str,
    k: int = 5,
    query_vec: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Top-k semantic search over the world-bible chunks."""
    if query_vec is None:
        query_vec = np.array(await embed_one(query), dtype="float32")
    else:
        query_vec = np.asarray(query_vec, dtype="float32")

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, text, section_title, subsection_title,
                   chunk_type, topic_tags, canonical_entities, metadata,
                   1 - (embedding <=> $1) AS score
            FROM world_bible_chunks
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            query_vec, k,
        )

    return [
        {
            "chunk_id": r["chunk_id"],
            "text": r["text"],
            "section_title": r["section_title"],
            "subsection_title": r["subsection_title"],
            "chunk_type": r["chunk_type"],
            "topic_tags": r["topic_tags"],
            "canonical_entities": r["canonical_entities"],
            "metadata": r["metadata"],
            "score": float(r["score"]),
            "source": "world_bible",
        }
        for r in rows
    ]
