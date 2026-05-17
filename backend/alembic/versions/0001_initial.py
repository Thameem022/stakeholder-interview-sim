"""initial schema with pgvector

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE personas (
            id text PRIMARY KEY,
            display_name text NOT NULL,
            config jsonb NOT NULL,
            raw_config jsonb,
            voice_id text,
            created_at timestamptz DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE persona_chunks (
            id bigserial PRIMARY KEY,
            persona_id text NOT NULL,
            source text NOT NULL,
            chunk_id text NOT NULL,
            text text NOT NULL,
            metadata jsonb NOT NULL,
            embedding vector(1536) NOT NULL
        )
    """)
    op.execute("CREATE INDEX persona_chunks_embedding_idx ON persona_chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX persona_chunks_persona_idx ON persona_chunks(persona_id)")
    op.execute("CREATE INDEX persona_chunks_persona_source_idx ON persona_chunks(persona_id, source)")

    op.execute("""
        CREATE TABLE world_bible_chunks (
            id bigserial PRIMARY KEY,
            chunk_id text NOT NULL,
            text text NOT NULL,
            section_title text,
            subsection_title text,
            chunk_type text NOT NULL DEFAULT 'section_body',
            topic_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
            canonical_entities jsonb NOT NULL DEFAULT '[]'::jsonb,
            metadata jsonb NOT NULL,
            embedding vector(1536) NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX world_bible_embedding_idx ON world_bible_chunks USING hnsw (embedding vector_cosine_ops)"
    )

    op.execute("""
        CREATE TABLE interview_sessions (
            id uuid PRIMARY KEY,
            persona_id text,
            voice_id text,
            started_at timestamptz DEFAULT now(),
            ended_at timestamptz,
            transcript jsonb,
            metadata jsonb
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS interview_sessions")
    op.execute("DROP TABLE IF EXISTS world_bible_chunks")
    op.execute("DROP TABLE IF EXISTS persona_chunks")
    op.execute("DROP TABLE IF EXISTS personas")
