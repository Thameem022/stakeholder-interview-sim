"""Tests for scripts/load_knowledge.py.

The validation tests work by delta: they read the real authored files, inject
one fault, and assert on the errors that appear which were not there before.
Pinning an absolute error list would make every one of these tests fail the
next time anything else in the corpus changes, and a bare `not v.ok` would pass
for any fault, including one the mutation did not introduce.

The load test runs against a throwaway schema, so it never writes to the real
persona_knowledge / world_sections, and embeds through a counting stub, so it
costs nothing.
"""

from __future__ import annotations

import copy
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import load_knowledge as lk  # noqa: E402

from app.config import settings  # noqa: E402


@pytest.fixture(scope="module")
def sources() -> lk.Sources:
    return lk.read_sources(lk.PERSONA_DIR, lk.WORLD_FILE, lk.SIC_KEY_DIR)


@pytest.fixture(scope="module")
def baseline_errors(sources) -> set[str]:
    return set(lk.validate(sources).errors)


def new_errors(mutated: lk.Sources, baseline_errors: set[str]) -> set[str]:
    """Errors the mutation introduced, ignoring ones the corpus already had."""
    return set(lk.validate(mutated).errors) - baseline_errors


def mutate(sources: lk.Sources, persona_id: str, index: int, **changes) -> lk.Sources:
    copied = copy.deepcopy(sources)
    copied.personas[persona_id]["knowledge"][index].update(changes)
    return copied


def find_entry(sources: lk.Sources, persona_id: str, tier: int) -> tuple[int, dict]:
    for i, e in enumerate(sources.personas[persona_id]["knowledge"]):
        if e["tier"] == tier:
            return i, e
    raise AssertionError(f"{persona_id} has no tier-{tier} entry")


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_tier_1_with_a_deflection_is_rejected(sources, baseline_errors):
    idx, entry = find_entry(sources, "alex_martinez", 1)
    mutated = mutate(sources, "alex_martinez", idx, deflection="I couldn't say.")

    errors = new_errors(mutated, baseline_errors)
    assert errors == {
        f"alex_martinez.knowledge.json: {entry['id']} is tier 1 but carries a deflection"
    }


def test_tier_3_without_a_deflection_is_rejected(sources, baseline_errors):
    idx, entry = find_entry(sources, "alex_martinez", 3)

    for empty in (None, "", "   "):
        mutated = mutate(sources, "alex_martinez", idx, deflection=empty)
        errors = new_errors(mutated, baseline_errors)
        assert errors == {
            f"alex_martinez.knowledge.json: {entry['id']} is tier 3 but has no deflection"
        }, f"deflection={empty!r} was accepted"


def test_topic_outside_the_vocabulary_is_rejected(sources, baseline_errors):
    idx, entry = find_entry(sources, "sarah_donnelly", 2)
    mutated = mutate(sources, "sarah_donnelly", idx, topics=["flood_frequency", "harbor_mysticism"])

    errors = new_errors(mutated, baseline_errors)
    assert errors == {
        f"sarah_donnelly.knowledge.json: {entry['id']} topic 'harbor_mysticism' "
        "is not in topic_vocabulary"
    }


def test_empty_topics_is_rejected(sources, baseline_errors):
    idx, entry = find_entry(sources, "sarah_donnelly", 2)
    mutated = mutate(sources, "sarah_donnelly", idx, topics=[])

    errors = new_errors(mutated, baseline_errors)
    assert errors == {f"sarah_donnelly.knowledge.json: {entry['id']} has no topics"}


def _reglossed(sources: lk.Sources, persona_id: str, glossary) -> lk.Sources:
    copied = copy.deepcopy(sources)
    copied.personas[persona_id]["topic_glossary"] = glossary
    return copied


def test_topic_without_a_gloss_is_rejected(sources, baseline_errors):
    data = sources.personas["sarah_donnelly"]
    glossary = {k: v for k, v in data["topic_glossary"].items() if k != "funding"}
    mutated = _reglossed(sources, "sarah_donnelly", glossary)

    assert (
        "sarah_donnelly.knowledge.json: topic 'funding' has no gloss in topic_glossary"
        in new_errors(mutated, baseline_errors)
    )


def test_gloss_for_an_unknown_topic_is_rejected(sources, baseline_errors):
    glossary = dict(sources.personas["sarah_donnelly"]["topic_glossary"])
    glossary["harbor_mysticism"] = "not a real topic"
    mutated = _reglossed(sources, "sarah_donnelly", glossary)

    assert (
        "sarah_donnelly.knowledge.json: topic_glossary has 'harbor_mysticism', "
        "which is not in topic_vocabulary" in new_errors(mutated, baseline_errors)
    )


def test_empty_gloss_is_rejected(sources, baseline_errors):
    glossary = dict(sources.personas["sarah_donnelly"]["topic_glossary"])
    glossary["funding"] = "   "
    mutated = _reglossed(sources, "sarah_donnelly", glossary)

    assert (
        "sarah_donnelly.knowledge.json: gloss for 'funding' is empty"
        in new_errors(mutated, baseline_errors)
    )


def test_glossaries_that_disagree_between_files_are_rejected(sources, baseline_errors):
    """The glossary is shared, so the four copies drifting is a real hazard.

    topic_glossary is keyed on the topic alone, so if two files disagreed the
    row that won would depend on which file loaded last.
    """
    glossary = dict(sources.personas["sarah_donnelly"]["topic_glossary"])
    glossary["funding"] = "something else entirely"
    mutated = _reglossed(sources, "sarah_donnelly", glossary)

    errors = new_errors(mutated, baseline_errors)
    assert any("topic_glossary for 'funding' differs between" in e for e in errors), errors


def test_id_set_diverging_from_the_sic_key_is_rejected(sources, baseline_errors):
    idx, entry = find_entry(sources, "michael_mike_alvarez", 2)
    original = entry["id"]
    renamed = "michael_m_t2_not_in_the_sic_key"
    mutated = mutate(sources, "michael_mike_alvarez", idx, id=renamed, sic_item=renamed)

    errors = new_errors(mutated, baseline_errors)
    assert errors == {
        "michael_mike_alvarez.knowledge.json: ids absent from "
        f"michael_mike_alvarez_sic_key.json: {renamed}",
        "michael_mike_alvarez.knowledge.json: ids in michael_mike_alvarez_sic_key.json "
        f"but not in the knowledge file: {original}",
    }


def test_id_not_matching_sic_item_is_rejected(sources, baseline_errors):
    idx, entry = find_entry(sources, "thomas_tom_caldwell", 1)
    mutated = mutate(sources, "thomas_tom_caldwell", idx, sic_item="something_else")

    errors = new_errors(mutated, baseline_errors)
    assert errors == {
        f"thomas_tom_caldwell.knowledge.json: {entry['id']} sic_item is 'something_else', "
        f"expected id {entry['id']!r}"
    }


def test_the_authored_corpus_validates(sources):
    """The committed files must load as they stand; every other test mutates a copy."""
    v = lk.validate(sources)
    assert v.ok, "authored corpus no longer validates: " + "; ".join(v.errors)


def test_duplicate_world_ids_are_rejected(sources, baseline_errors):
    mutated = copy.deepcopy(sources)
    first, second = mutated.world["sections"][0], mutated.world["sections"][3]
    stolen = first["id"]
    second["id"] = stolen

    errors = new_errors(mutated, baseline_errors)
    assert errors == {
        f"world_harbortown.json: id {stolen!r} used by 2 sections "
        f"(headings: {first['heading'][:60]!r}; {second['heading'][:60]!r})"
    }


def test_corpus_version_is_content_derived(sources):
    first = lk.compute_corpus_version(sources.by_filename)
    again = lk.compute_corpus_version(copy.deepcopy(sources.by_filename))
    assert first == again
    assert len(first) == 12

    changed = copy.deepcopy(sources.by_filename)
    changed["alex_martinez.knowledge.json"]["knowledge"][0]["in_voice"] += " "
    assert lk.compute_corpus_version(changed) != first


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------

TEST_SCHEMA = "sis_test_load_knowledge"

SCHEMA_DDL = f"""
DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;
CREATE SCHEMA {TEST_SCHEMA};
CREATE TABLE {TEST_SCHEMA}.persona_knowledge (
    id         text     PRIMARY KEY,
    persona_id text     NOT NULL,
    tier       smallint NOT NULL,
    topics     text[]   NOT NULL,
    claim      text     NOT NULL,
    in_voice   text     NOT NULL,
    deflection text,
    UNIQUE (persona_id, id)
);
CREATE TABLE {TEST_SCHEMA}.world_sections (
    id        text PRIMARY KEY,
    section   text,
    heading   text,
    body      text,
    entities  text[],
    known_by  text[],
    embedding vector(1536),
    -- Mirrors migration 0009: heading at weight A, body at B.
    tsv       tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(body, '')), 'B')
    ) STORED
);
-- Without this the loader's glossary upsert falls through search_path to
-- public.topic_glossary and writes to the real table.
CREATE TABLE {TEST_SCHEMA}.topic_glossary (
    topic text PRIMARY KEY,
    gloss text NOT NULL
);
"""


def _dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


class CountingEmbedder:
    """Stands in for OpenAI. Deterministic, so re-runs produce equal vectors."""

    def __init__(self) -> None:
        self.requests = 0
        self.texts = 0

    async def __call__(self, texts):
        self.requests += 1
        self.texts += len(texts)
        out = []
        for t in texts:
            seed = sum(t.encode("utf-8")) or 1
            out.append([((seed + i) % 1000) / 1000.0 for i in range(lk.EMBEDDING_DIMS)])
        return out


@pytest.fixture
def loadable_dir(tmp_path):
    """A copy of the five source files, read from outside the package."""
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    for persona_id in lk.EXPECTED_COUNTS:
        src = lk.persona_file(lk.PERSONA_DIR, persona_id)
        (persona_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    world_file = tmp_path / lk.WORLD_FILE.name
    world_file.write_text(lk.WORLD_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    return persona_dir, world_file


@pytest.fixture
def schema():
    """A throwaway schema holding the two tables, dropped afterwards."""
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        conn = psycopg2.connect(_dsn())
    except psycopg2.OperationalError as e:
        pytest.skip(f"database unavailable: {e}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if cur.fetchone() is None:
            conn.close()
            pytest.skip("pgvector extension not installed")
        cur.execute(SCHEMA_DDL)
    yield conn
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.close()


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {TEST_SCHEMA}.{table}")
        return cur.fetchone()[0]


async def test_loading_twice_is_a_no_op(schema, loadable_dir, tmp_path):
    import asyncpg

    persona_dir, world_file = loadable_dir
    embedder = CountingEmbedder()
    stamp = tmp_path / "corpus_version.json"
    cache = tmp_path / "embeddings.json"

    async def conn_factory():
        conn = await asyncpg.connect(_dsn())
        await conn.execute(f"SET search_path TO {TEST_SCHEMA}, public")
        return conn

    def args():
        return Namespace(
            dry_run=False,
            verbose=False,
            persona_dir=persona_dir,
            world_file=world_file,
            sic_key_dir=lk.SIC_KEY_DIR,
            cache_file=cache,
            corpus_version_file=stamp,
            embed_fn=embedder,
            conn_factory=conn_factory,
        )

    assert await lk.run(args()) == 0

    first_counts = (
        _count(schema, "persona_knowledge"),
        _count(schema, "world_sections"),
        _count(schema, "topic_glossary"),
    )
    first_version = json.loads(stamp.read_text())["corpus_version"]
    first_requests = embedder.requests

    assert first_counts == (lk.EXPECTED_TOTAL, 91, 16)
    assert first_requests == 1, "91 sections should be one batched request"
    assert embedder.texts == 91

    stamp_bytes = stamp.read_bytes()

    assert await lk.run(args()) == 0

    assert (
        _count(schema, "persona_knowledge"),
        _count(schema, "world_sections"),
        _count(schema, "topic_glossary"),
    ) == first_counts
    assert json.loads(stamp.read_text())["corpus_version"] == first_version
    assert stamp.read_bytes() == stamp_bytes, "an unchanged corpus should not rewrite the stamp"
    assert embedder.requests == first_requests, "second run should hit the cache, not the API"


async def test_rows_no_longer_in_the_source_are_deleted(schema, loadable_dir, tmp_path):
    import asyncpg

    persona_dir, world_file = loadable_dir
    embedder = CountingEmbedder()

    async def conn_factory():
        conn = await asyncpg.connect(_dsn())
        await conn.execute(f"SET search_path TO {TEST_SCHEMA}, public")
        return conn

    args = Namespace(
        dry_run=False,
        verbose=False,
        persona_dir=persona_dir,
        world_file=world_file,
        sic_key_dir=lk.SIC_KEY_DIR,
        cache_file=tmp_path / "embeddings.json",
        corpus_version_file=tmp_path / "corpus_version.json",
        embed_fn=embedder,
        conn_factory=conn_factory,
    )
    assert await lk.run(args) == 0

    with schema.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TEST_SCHEMA}.persona_knowledge "
            "(id, persona_id, tier, topics, claim, in_voice) "
            "VALUES ('stale_item', 'alex_martinez', 1, ARRAY['funding'], 'c', 'v')"
        )
        cur.execute(
            f"INSERT INTO {TEST_SCHEMA}.world_sections (id, body) VALUES ('stale_section', 'x')"
        )

    assert _count(schema, "persona_knowledge") == lk.EXPECTED_TOTAL + 1
    assert _count(schema, "world_sections") == 92

    assert await lk.run(args) == 0

    assert _count(schema, "persona_knowledge") == lk.EXPECTED_TOTAL
    assert _count(schema, "world_sections") == 91
    with schema.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {TEST_SCHEMA}.persona_knowledge WHERE id = 'stale_item'")
        assert cur.fetchone()[0] == 0


async def test_dry_run_writes_nothing(schema, loadable_dir, tmp_path):
    persona_dir, world_file = loadable_dir
    embedder = CountingEmbedder()
    stamp = tmp_path / "corpus_version.json"

    args = Namespace(
        dry_run=True,
        verbose=False,
        persona_dir=persona_dir,
        world_file=world_file,
        sic_key_dir=lk.SIC_KEY_DIR,
        cache_file=tmp_path / "embeddings.json",
        corpus_version_file=stamp,
        embed_fn=embedder,
        conn_factory=None,
    )
    assert await lk.run(args) == 0

    assert embedder.requests == 0
    assert not stamp.exists()
    assert _count(schema, "persona_knowledge") == 0
    assert _count(schema, "world_sections") == 0


async def test_validation_failure_blocks_the_load(schema, loadable_dir, tmp_path):
    """A world file that fails validation must stop short of the write path."""
    import asyncpg

    persona_dir, world_file = loadable_dir
    embedder = CountingEmbedder()
    stamp = tmp_path / "corpus_version.json"

    # One duplicated id is enough to refuse the whole load, including the
    # persona rows, which are themselves fine.
    world = json.loads(world_file.read_text(encoding="utf-8"))
    world["sections"][3]["id"] = world["sections"][0]["id"]
    broken_world = tmp_path / "broken_world.json"
    broken_world.write_text(json.dumps(world), encoding="utf-8")

    async def conn_factory():
        # Scoped to the throwaway schema: if this test ever stops failing
        # validation, it must not reach the real tables.
        conn = await asyncpg.connect(_dsn())
        await conn.execute(f"SET search_path TO {TEST_SCHEMA}, public")
        return conn

    args = Namespace(
        dry_run=False,
        verbose=False,
        persona_dir=persona_dir,
        world_file=broken_world,
        sic_key_dir=lk.SIC_KEY_DIR,
        cache_file=tmp_path / "embeddings.json",
        corpus_version_file=stamp,
        embed_fn=embedder,
        conn_factory=conn_factory,
    )
    assert await lk.run(args) == 1

    assert embedder.requests == 0
    assert not stamp.exists()
    assert _count(schema, "persona_knowledge") == 0
    assert _count(schema, "world_sections") == 0
