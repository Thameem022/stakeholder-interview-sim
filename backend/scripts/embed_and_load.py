"""
embed_and_load.py — one-shot seed for pgvector store.

Loads three chunk sources, embeds each with OpenAI text-embedding-3-small,
and inserts into the persona_chunks and world_bible_chunks tables.

Sources:
  1. Corpus chunks       (scripts/seed_data/corpus_*.json — 4 personas, ~1,820 chunks)
  2. Dossier chunks      (built at runtime from persona configs — ~13 per persona)
  3. Persona facts       (built at runtime from persona configs — ~80 per persona)
  4. World bible chunks  (scripts/seed_data/world_bible_chunks.json — 114 chunks)

Idempotent: TRUNCATEs both tables before insert.

Usage:
    uv run python scripts/embed_and_load.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

import asyncpg
import tiktoken
from openai import APIStatusError, AsyncOpenAI
from pgvector.asyncpg import register_vector

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.rag.context import build_persona_dossier_chunks, build_persona_facts, resolve_persona_record

SEED_DIR = Path(__file__).parent / "seed_data"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BATCH = 100
# text-embedding-3-small accepts up to 8192 tokens. Leave headroom for safety.
MAX_TOKENS = 8000

_encoder = tiktoken.get_encoding("cl100k_base")


def _truncate_to_tokens(text: str, max_tokens: int = MAX_TOKENS) -> str:
    tokens = _encoder.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _encoder.decode(tokens[:max_tokens])

CORPUS_FILES = {
    "climate skeptics": "corpus_climate skeptics.json",
    "climate_risk_analyst": "corpus_climate_risk_analyst.json",
    "real_estate_persona": "corpus_real_estate_persona.json",
    "urban_planner": "corpus_urban_planner.json",
}

DOSSIER_PERSONAS = ["developer", "municipal_planner", "small_business_owner", "waterfront_resident"]


async def embed_batch(client: AsyncOpenAI, texts: Sequence[str]) -> List[List[float]]:
    """Embed a batch. Retry only on transient errors (429, 5xx); fail fast on 400."""
    delay = 1.0
    for attempt in range(6):
        try:
            resp = await client.embeddings.create(model=EMBEDDING_MODEL, input=list(texts))
            return [item.embedding for item in resp.data]
        except APIStatusError as e:
            # 400 = bad request (oversized input, malformed). Don't retry.
            if e.status_code == 400:
                raise
            if attempt == 5:
                raise
            print(f"  Embedding attempt {attempt + 1} failed ({e.status_code}): {e}; retrying in {delay}s")
            await asyncio.sleep(delay)
            delay *= 2
        except Exception as e:
            if attempt == 5:
                raise
            print(f"  Embedding attempt {attempt + 1} failed: {e}; retrying in {delay}s")
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


async def embed_all(client: AsyncOpenAI, texts: List[str]) -> List[List[float]]:
    # Pre-truncate any oversized texts before batching (OpenAI hard limit 8192 tokens).
    truncated = []
    n_truncated = 0
    for t in texts:
        before = len(_encoder.encode(t))
        if before > MAX_TOKENS:
            n_truncated += 1
            truncated.append(_truncate_to_tokens(t))
        else:
            truncated.append(t)
    if n_truncated:
        print(f"  Truncated {n_truncated} oversized chunk(s) to {MAX_TOKENS} tokens")

    out: List[List[float]] = []
    total_batches = (len(truncated) + EMBEDDING_BATCH - 1) // EMBEDDING_BATCH
    for i in range(0, len(truncated), EMBEDDING_BATCH):
        batch = truncated[i : i + EMBEDDING_BATCH]
        print(f"  Embedding batch {i // EMBEDDING_BATCH + 1}/{total_batches} ({len(batch)} items)")
        out.extend(await embed_batch(client, batch))
    return out


async def seed_corpus(conn: asyncpg.Connection, client: AsyncOpenAI) -> int:
    total = 0
    for persona_slug, filename in CORPUS_FILES.items():
        path = SEED_DIR / filename
        if not path.exists():
            print(f"  Skip {filename}: not found")
            continue

        records = json.loads(path.read_text(encoding="utf-8"))
        print(f"  Loading {persona_slug}: {len(records)} chunks from {filename}")

        texts = [r.get("page_content", "") for r in records]
        embeddings = await embed_all(client, texts)

        rows = []
        for idx, (record, vec) in enumerate(zip(records, embeddings)):
            metadata = record.get("metadata", {}) or {}
            chunk_id = metadata.get("chunk_id") or f"{persona_slug}:corpus:{idx}"
            rows.append((persona_slug, "corpus", chunk_id, record.get("page_content", ""), json.dumps(metadata), vec))

        await conn.executemany(
            """
            INSERT INTO persona_chunks (persona_id, source, chunk_id, text, metadata, embedding)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            rows,
        )
        total += len(rows)
    return total


async def seed_dossier_and_facts(conn: asyncpg.Connection, client: AsyncOpenAI) -> tuple[int, int]:
    dossier_total = 0
    facts_total = 0

    for persona in DOSSIER_PERSONAS:
        try:
            record = resolve_persona_record(persona)
        except RuntimeError as e:
            print(f"  Skip {persona}: {e}")
            continue
        persona_key = record["canonical_key"]

        dossier_chunks = build_persona_dossier_chunks(persona)
        fact_chunks = build_persona_facts(persona)
        print(f"  {persona_key}: {len(dossier_chunks)} dossier + {len(fact_chunks)} facts")

        if dossier_chunks:
            texts = [c["text"] for c in dossier_chunks]
            embeddings = await embed_all(client, texts)
            rows = [
                (
                    persona_key,
                    "dossier",
                    c["metadata"]["chunk_id"],
                    c["text"],
                    json.dumps(c["metadata"]),
                    vec,
                )
                for c, vec in zip(dossier_chunks, embeddings)
            ]
            await conn.executemany(
                """
                INSERT INTO persona_chunks (persona_id, source, chunk_id, text, metadata, embedding)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                """,
                rows,
            )
            dossier_total += len(rows)

        if fact_chunks:
            texts = [c["text"] for c in fact_chunks]
            embeddings = await embed_all(client, texts)
            rows = [
                (
                    persona_key,
                    "facts",
                    c["metadata"]["chunk_id"],
                    c["text"],
                    json.dumps(c["metadata"]),
                    vec,
                )
                for c, vec in zip(fact_chunks, embeddings)
            ]
            await conn.executemany(
                """
                INSERT INTO persona_chunks (persona_id, source, chunk_id, text, metadata, embedding)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                """,
                rows,
            )
            facts_total += len(rows)

    return dossier_total, facts_total


async def seed_world_bible(conn: asyncpg.Connection, client: AsyncOpenAI) -> int:
    path = SEED_DIR / "world_bible_chunks.json"
    if not path.exists():
        print(f"  Skip world bible: {path} not found")
        return 0

    chunks: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    print(f"  Loading world bible: {len(chunks)} chunks")

    texts = [c.get("text", "") for c in chunks]
    embeddings = await embed_all(client, texts)

    rows = []
    for c, vec in zip(chunks, embeddings):
        rows.append(
            (
                c.get("chunk_id"),
                c.get("text", ""),
                c.get("section_title"),
                c.get("subsection_title"),
                c.get("chunk_type", "section_body"),
                json.dumps(c.get("topic_tags", [])),
                json.dumps(c.get("canonical_entities", [])),
                json.dumps(c),
                vec,
            )
        )

    await conn.executemany(
        """
        INSERT INTO world_bible_chunks
          (chunk_id, text, section_title, subsection_title, chunk_type,
           topic_tags, canonical_entities, metadata, embedding)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb, $9)
        """,
        rows,
    )
    return len(rows)


async def main() -> None:
    dsn = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/sis")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable required")

    client = AsyncOpenAI(api_key=api_key)
    conn = await asyncpg.connect(dsn)

    # Safety net: ensure pgvector extension exists before registering type codec.
    # In a fresh DB, this script may be run before `alembic upgrade head`; without
    # this line, register_vector() fails with "unknown type: public.vector".
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await register_vector(conn)

    try:
        start = time.time()
        print("Truncating tables...")
        await conn.execute("TRUNCATE persona_chunks RESTART IDENTITY")
        await conn.execute("TRUNCATE world_bible_chunks RESTART IDENTITY")

        print("\n[1/3] Seeding corpus chunks...")
        corpus_count = await seed_corpus(conn, client)

        print("\n[2/3] Seeding persona dossier + facts chunks...")
        dossier_count, facts_count = await seed_dossier_and_facts(conn, client)

        print("\n[3/3] Seeding world bible chunks...")
        world_count = await seed_world_bible(conn, client)

        elapsed = time.time() - start
        print("\n=== Seed complete ===")
        print(f"  corpus chunks:  {corpus_count}")
        print(f"  dossier chunks: {dossier_count}")
        print(f"  facts chunks:   {facts_count}")
        print(f"  world chunks:   {world_count}")
        print(f"  total:          {corpus_count + dossier_count + facts_count + world_count}")
        print(f"  elapsed:        {elapsed:.1f}s")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
