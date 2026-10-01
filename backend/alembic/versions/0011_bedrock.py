"""Bedrock: Titan embedding dimension; single-use stream tokens for live voice

SR-2026-052 item 1.1 (SEC-AI-001).

persona_chunks / world_bible_chunks: embeddings move from OpenAI
text-embedding-3-small (1536 dimensions) to Amazon Titan Text Embeddings V2
(1024). Vectors from different models are not comparable, so the chunk tables
are EMPTIED and their HNSW indexes rebuilt for the new dimension. Re-load the
corpora afterwards with `scripts/embed_and_load.py` (needs Bedrock access).
Retrieval returns no context until that has run.

realtime_stream_tokens: the browser no longer receives any AI-provider
credential. Starting an interview issues a short-lived, single-use token,
bound to the session and its participant, that opens the backend's audio
stream (/api/realtime/stream). Only its hash is stored.

Downgrade restores the OpenAI dimension and likewise empties the chunk tables.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011_bedrock"
down_revision: Union[str, None] = "0010_entra_sso"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _set_dimension(dim: int) -> None:
    op.execute("DROP INDEX IF EXISTS persona_chunks_embedding_idx")
    op.execute("DROP INDEX IF EXISTS world_bible_embedding_idx")
    op.execute("TRUNCATE persona_chunks, world_bible_chunks")
    op.execute(f"ALTER TABLE persona_chunks ALTER COLUMN embedding TYPE vector({dim})")
    op.execute(f"ALTER TABLE world_bible_chunks ALTER COLUMN embedding TYPE vector({dim})")
    op.execute(
        "CREATE INDEX persona_chunks_embedding_idx ON persona_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX world_bible_embedding_idx ON world_bible_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def upgrade() -> None:
    _set_dimension(1024)
    op.execute("""
        CREATE TABLE realtime_stream_tokens (
            token_hash      text        PRIMARY KEY,
            session_id      uuid        NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
            participant_id  uuid        NOT NULL,
            expires_at      timestamptz NOT NULL,
            used_at         timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX realtime_stream_tokens_expires_idx ON realtime_stream_tokens(expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE realtime_stream_tokens")
    _set_dimension(1536)
