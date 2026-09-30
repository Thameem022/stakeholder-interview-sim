"""
load_knowledge.py — load the authored knowledge JSON into persona_knowledge
and world_sections.

Replaces the embed_and_load.py path for these two tables only. persona_chunks
and world_bible_chunks hold the old corpus and are not touched here.

Nothing is written unless every check passes. A partial load is worse than no
load: the tables are the persona's memory, and half a memory is a persona that
confidently omits things. Validation therefore collects every problem and exits
non-zero with the full list rather than failing on the first one.

Only world_sections gets embeddings. persona_knowledge is looked up by
persona_id/tier/topic, never by similarity, so embedding it would cost money to
produce a column no query reads.

Usage:
    uv run python scripts/load_knowledge.py --dry-run
    uv run python scripts/load_knowledge.py [--verbose]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402

# The authored JSON lives under app/knowledge/. Note the directory is spelled
# "presonas" on disk; renaming it is a separate change from loading it.
KNOWLEDGE_DIR = BACKEND_DIR / "app" / "knowledge"
PERSONA_DIR = KNOWLEDGE_DIR / "presonas"
WORLD_FILE = KNOWLEDGE_DIR / "world" / "world_harbortown.json"
SIC_KEY_DIR = BACKEND_DIR / "app" / "evaluation" / "sic_keys"

# Where app.config reads corpus_version from when stamping interview_sessions.
CORPUS_VERSION_FILE = KNOWLEDGE_DIR / "corpus_version.json"

# Outside the package so it never ships in the wheel.
CACHE_FILE = BACKEND_DIR / ".knowledge_cache" / "embeddings.json"

# Authored counts. These are asserted, not discovered: a file that silently
# loses an entry is exactly the failure this script exists to catch.
EXPECTED_COUNTS = {
    "alex_martinez": 15,
    "michael_mike_alvarez": 11,
    "sarah_donnelly": 12,
    "thomas_tom_caldwell": 12,
}
EXPECTED_TOTAL = 50
PERSONA_IDS = frozenset(EXPECTED_COUNTS)
VALID_TIERS = frozenset({1, 2, 3})

EMBEDDING_BATCH = 100
EMBEDDING_DIMS = 1536

EmbedFn = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------


@dataclass
class Sources:
    """The five authored files, parsed, plus where each came from."""

    personas: dict[str, dict[str, Any]]  # persona_id -> file contents
    world: dict[str, Any]
    sic_keys: dict[str, list[str]]  # persona_id -> ids in the SIC key
    # filename -> parsed JSON, for the corpus_version hash.
    by_filename: dict[str, Any] = field(default_factory=dict)


def persona_file(persona_dir: Path, persona_id: str) -> Path:
    return persona_dir / f"{persona_id}.knowledge.json"


def read_sources(persona_dir: Path, world_file: Path, sic_key_dir: Path) -> Sources:
    """Read all five files plus the SIC keys they are checked against.

    Raises rather than collecting errors: a file that will not parse leaves
    nothing to validate, so there is no point continuing to build a report.
    """
    personas: dict[str, dict[str, Any]] = {}
    by_filename: dict[str, Any] = {}

    for persona_id in sorted(EXPECTED_COUNTS):
        path = persona_file(persona_dir, persona_id)
        if not path.is_file():
            raise FileNotFoundError(f"persona knowledge file missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        personas[persona_id] = data
        by_filename[path.name] = data

    if not world_file.is_file():
        raise FileNotFoundError(f"world file missing: {world_file}")
    world = json.loads(world_file.read_text(encoding="utf-8"))
    by_filename[world_file.name] = world

    sic_keys: dict[str, list[str]] = {}
    for persona_id in sorted(EXPECTED_COUNTS):
        path = sic_key_dir / f"{persona_id}_sic_key.json"
        if not path.is_file():
            raise FileNotFoundError(f"SIC key missing: {path}")
        key = json.loads(path.read_text(encoding="utf-8"))
        sic_keys[persona_id] = [item.get("chunk_id") for item in key.get("sic_catalog", [])]

    return Sources(personas=personas, world=world, sic_keys=sic_keys, by_filename=by_filename)


def compute_corpus_version(by_filename: dict[str, Any]) -> str:
    """sha256 over canonical JSON of all five files, in filename order.

    Deliberately content-derived: mtime changes when a file is touched, copied
    or checked out, none of which mean the corpus changed.
    """
    h = hashlib.sha256()
    for name in sorted(by_filename):
        canonical = json.dumps(
            by_filename[name], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        h.update(canonical.encode("utf-8"))
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # persona_id -> tier -> count
    per_persona_tier: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    persona_rows: list[tuple] = field(default_factory=list)
    world_rows: list[dict[str, Any]] = field(default_factory=list)
    glossary_rows: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(sources: Sources, verbose: bool = False) -> Validation:
    v = Validation()

    total = 0
    # persona_id -> its topic_glossary, compared across files once the loop ends.
    glossaries: dict[str, dict[str, str]] = {}
    for persona_id in sorted(EXPECTED_COUNTS):
        data = sources.personas[persona_id]
        where = f"{persona_id}.knowledge.json"

        declared = data.get("persona_id")
        if declared != persona_id:
            v.errors.append(f"{where}: persona_id is {declared!r}, expected {persona_id!r}")

        entries = data.get("knowledge")
        if not isinstance(entries, list):
            v.errors.append(f"{where}: 'knowledge' is missing or not a list")
            continue

        expected = EXPECTED_COUNTS[persona_id]
        if len(entries) != expected:
            v.errors.append(f"{where}: {len(entries)} entries, expected {expected}")
        total += len(entries)

        vocabulary = set(data.get("topic_vocabulary") or [])
        if not vocabulary:
            v.errors.append(f"{where}: topic_vocabulary is empty, so no topic can be checked")

        # The glossary defines the vocabulary for the model in the recall tool
        # description, so a tag without one is a tag the model has to guess the
        # meaning of, and a gloss without a tag is dead text nothing will show.
        glossary = data.get("topic_glossary")
        if not isinstance(glossary, dict):
            v.errors.append(f"{where}: 'topic_glossary' is missing or not an object")
        else:
            glossaries[persona_id] = glossary
            for t in sorted(vocabulary - set(glossary)):
                v.errors.append(f"{where}: topic {t!r} has no gloss in topic_glossary")
            for t in sorted(set(glossary) - vocabulary):
                v.errors.append(
                    f"{where}: topic_glossary has {t!r}, which is not in topic_vocabulary"
                )
            for t, gloss in sorted(glossary.items()):
                if not _nonempty(gloss):
                    v.errors.append(f"{where}: gloss for {t!r} is empty")

        seen_ids: set[str] = set()
        for idx, e in enumerate(entries):
            eid = e.get("id")
            label = eid if _nonempty(eid) else f"entry[{idx}]"

            if not _nonempty(eid):
                v.errors.append(f"{where}: {label} has no id")
            elif eid in seen_ids:
                v.errors.append(f"{where}: duplicate id {eid!r}")
            else:
                seen_ids.add(eid)

            if e.get("sic_item") != eid:
                v.errors.append(
                    f"{where}: {label} sic_item is {e.get('sic_item')!r}, expected id {eid!r}"
                )

            tier = e.get("tier")
            if tier not in VALID_TIERS:
                v.errors.append(f"{where}: {label} tier is {tier!r}, expected one of 1, 2, 3")
            else:
                v.per_persona_tier[persona_id][tier] += 1

            # A tier-1 item is always open, so it has nothing to deflect with;
            # a tier-2/3 item that stays closed needs a line to close it with.
            deflection = e.get("deflection")
            if tier == 1 and deflection is not None:
                v.errors.append(f"{where}: {label} is tier 1 but carries a deflection")
            elif tier in (2, 3) and not _nonempty(deflection):
                v.errors.append(f"{where}: {label} is tier {tier} but has no deflection")

            if not _nonempty(e.get("in_voice")):
                v.errors.append(f"{where}: {label} has an empty in_voice")

            if not _nonempty(e.get("claim")):
                v.errors.append(f"{where}: {label} has an empty claim")

            topics = e.get("topics")
            if not topics:
                v.errors.append(f"{where}: {label} has no topics")
            else:
                for t in topics:
                    if t not in vocabulary:
                        v.errors.append(
                            f"{where}: {label} topic {t!r} is not in topic_vocabulary"
                        )

            if _nonempty(eid) and tier in VALID_TIERS:
                v.persona_rows.append(
                    (
                        eid,
                        persona_id,
                        tier,
                        list(topics or []),
                        e.get("claim") or "",
                        e.get("in_voice") or "",
                        deflection,
                    )
                )

        # The SIC key is the grader's view of the same catalogue. If the two
        # drift, the grader scores items the persona cannot say, or the persona
        # holds items no one scores.
        sic_ids = {i for i in sources.sic_keys.get(persona_id, []) if i}
        missing_from_key = sorted(seen_ids - sic_ids)
        missing_from_knowledge = sorted(sic_ids - seen_ids)
        if missing_from_key:
            v.errors.append(
                f"{where}: ids absent from {persona_id}_sic_key.json: "
                + ", ".join(missing_from_key)
            )
        if missing_from_knowledge:
            v.errors.append(
                f"{where}: ids in {persona_id}_sic_key.json but not in the knowledge file: "
                + ", ".join(missing_from_knowledge)
            )

        for w in data.get("_warnings") or []:
            v.warnings.append(f"{where}: {w}")

    if total != EXPECTED_TOTAL:
        v.errors.append(f"persona entries total {total}, expected {EXPECTED_TOTAL}")

    # topic_glossary is authored once per persona file but describes a shared
    # vocabulary, so the four copies have to agree. topic_glossary is keyed on
    # the topic alone, and two files disagreeing would make the row that wins
    # depend on load order.
    if glossaries:
        reference_id = sorted(glossaries)[0]
        reference = glossaries[reference_id]
        for persona_id in sorted(glossaries)[1:]:
            other = glossaries[persona_id]
            for topic in sorted(set(reference) | set(other)):
                if reference.get(topic) != other.get(topic):
                    v.errors.append(
                        f"topic_glossary for {topic!r} differs between "
                        f"{reference_id} and {persona_id}; the glossary is shared "
                        f"and must be identical in every persona file"
                    )
        if v.ok:
            v.glossary_rows = [(t, reference[t]) for t in sorted(reference)]

    # ---- world ----
    sections = sources.world.get("sections")
    if not isinstance(sections, list):
        v.errors.append("world_harbortown.json: 'sections' is missing or not a list")
        return v

    id_counts = Counter(s.get("id") for s in sections)
    for sid, n in sorted(id_counts.items(), key=lambda kv: str(kv[0])):
        if n > 1:
            headings = [
                (s.get("heading") or "")[:60] for s in sections if s.get("id") == sid
            ]
            v.errors.append(
                f"world_harbortown.json: id {sid!r} used by {n} sections "
                f"(headings: {'; '.join(repr(h) for h in headings)})"
            )

    unreviewed = 0
    for idx, s in enumerate(sections):
        sid = s.get("id")
        label = sid if _nonempty(sid) else f"sections[{idx}]"

        if not _nonempty(sid):
            v.errors.append(f"world_harbortown.json: {label} has no id")

        # The column is `body`; the authored key is `text`.
        if not _nonempty(s.get("text")):
            v.errors.append(f"world_harbortown.json: {label} has an empty text/body")

        for p in s.get("known_by") or []:
            if p not in PERSONA_IDS:
                v.errors.append(
                    f"world_harbortown.json: {label} known_by contains unknown persona {p!r}"
                )

        if s.get("known_by_reviewed") is False:
            unreviewed += 1
            if verbose:
                v.warnings.append(f"world_harbortown.json: {label} known_by_reviewed is false")

        if _nonempty(sid):
            v.world_rows.append(
                {
                    "id": sid,
                    "section": s.get("section"),
                    "heading": s.get("heading"),
                    "body": s.get("text") or "",
                    "entities": list(s.get("entities") or []),
                    "known_by": list(s.get("known_by") or []),
                }
            )

    if unreviewed and not verbose:
        v.warnings.append(
            f"world_harbortown.json: {unreviewed} of {len(sections)} sections have "
            "known_by_reviewed=false (re-run with --verbose to list them)"
        )

    return v


# --------------------------------------------------------------------------
# embeddings
# --------------------------------------------------------------------------


class EmbeddingCache:
    """Body-text sha256 -> vector, persisted to one JSON file.

    Keyed by content, so editing one section re-embeds that section and nothing
    else. The model name is stored alongside: vectors from a different model are
    not comparable, so a model change invalidates the whole file rather than
    silently mixing two vector spaces.
    """

    def __init__(self, path: Path, model: str) -> None:
        self.path = path
        self.model = model
        self.entries: dict[str, list[float]] = {}
        self.hits = 0
        self.misses = 0
        if path.is_file():
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                blob = {}
            if blob.get("model") == model and blob.get("dims") == EMBEDDING_DIMS:
                self.entries = blob.get("entries") or {}

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> list[float] | None:
        return self.entries.get(self.key(text))

    def put(self, text: str, vector: list[float]) -> None:
        self.entries[self.key(text)] = vector

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"model": self.model, "dims": EMBEDDING_DIMS, "entries": self.entries},
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def openai_embed_fn(model: str) -> EmbedFn:
    """Build the real embedder. Imported lazily so --dry-run needs no API key."""
    from openai import APIStatusError, AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key or None)

    async def embed(texts: Sequence[str]) -> list[list[float]]:
        delay = 1.0
        for attempt in range(6):
            try:
                resp = await client.embeddings.create(model=model, input=list(texts))
                return [item.embedding for item in resp.data]
            except APIStatusError as e:
                # 400 means the input is wrong, not the moment. Retrying an
                # oversized or malformed batch just buys the same error later.
                if e.status_code == 400 or attempt == 5:
                    raise
                print(f"  embedding attempt {attempt + 1} failed ({e.status_code}); retry in {delay}s")
                await asyncio.sleep(delay)
                delay *= 2
            except Exception as e:  # noqa: BLE001 - transient network/SDK errors
                if attempt == 5:
                    raise
                print(f"  embedding attempt {attempt + 1} failed ({e}); retry in {delay}s")
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    return embed


async def embed_world(
    rows: list[dict[str, Any]],
    cache: EmbeddingCache,
    embed_fn: EmbedFn,
    verbose: bool = False,
) -> tuple[list[list[float]], int]:
    """Return one vector per row, plus the number of API requests made."""
    vectors: list[list[float] | None] = []
    pending: list[tuple[int, str]] = []

    for i, row in enumerate(rows):
        cached = cache.get(row["body"])
        if cached is None:
            cache.misses += 1
            pending.append((i, row["body"]))
            vectors.append(None)
        else:
            cache.hits += 1
            vectors.append(cached)

    requests = 0
    for start in range(0, len(pending), EMBEDDING_BATCH):
        batch = pending[start : start + EMBEDDING_BATCH]
        if verbose:
            print(f"  embedding {len(batch)} section(s) [{start + 1}-{start + len(batch)} of {len(pending)}]")
        result = await embed_fn([text for _, text in batch])
        requests += 1
        if len(result) != len(batch):
            raise RuntimeError(f"embedder returned {len(result)} vectors for {len(batch)} inputs")
        for (idx, text), vector in zip(batch, result):
            if len(vector) != EMBEDDING_DIMS:
                raise RuntimeError(
                    f"{rows[idx]['id']}: embedding has {len(vector)} dims, expected {EMBEDDING_DIMS}"
                )
            cache.put(text, vector)
            vectors[idx] = vector

    missing = [rows[i]["id"] for i, vec in enumerate(vectors) if vec is None]
    if missing:
        raise RuntimeError(f"no embedding produced for: {', '.join(missing)}")
    return [v for v in vectors if v is not None], requests


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------

PERSONA_UPSERT = """
INSERT INTO persona_knowledge (id, persona_id, tier, topics, claim, in_voice, deflection)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (id) DO UPDATE SET
    persona_id = EXCLUDED.persona_id,
    tier       = EXCLUDED.tier,
    topics     = EXCLUDED.topics,
    claim      = EXCLUDED.claim,
    in_voice   = EXCLUDED.in_voice,
    deflection = EXCLUDED.deflection
RETURNING (xmax = 0) AS inserted
"""

GLOSSARY_UPSERT = """
INSERT INTO topic_glossary (topic, gloss)
VALUES ($1, $2)
ON CONFLICT (topic) DO UPDATE SET gloss = EXCLUDED.gloss
RETURNING (xmax = 0) AS inserted
"""

WORLD_UPSERT = """
INSERT INTO world_sections (id, section, heading, body, entities, known_by, embedding)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (id) DO UPDATE SET
    section   = EXCLUDED.section,
    heading   = EXCLUDED.heading,
    body      = EXCLUDED.body,
    entities  = EXCLUDED.entities,
    known_by  = EXCLUDED.known_by,
    embedding = EXCLUDED.embedding
RETURNING (xmax = 0) AS inserted
"""


async def load_into_db(
    conn,
    persona_rows: list[tuple],
    world_rows: list[dict[str, Any]],
    vectors: list[list[float]],
    glossary_rows: list[tuple[str, str]] | None = None,
) -> dict[str, dict[str, int]]:
    """Upsert both tables and drop rows the source files no longer carry.

    One transaction: a crash between the two tables would leave the world
    describing facts the personas no longer hold.
    """
    stats = {
        "persona_knowledge": {"inserted": 0, "updated": 0, "deleted": 0},
        "world_sections": {"inserted": 0, "updated": 0, "deleted": 0},
        "topic_glossary": {"inserted": 0, "updated": 0, "deleted": 0},
    }
    glossary_rows = glossary_rows or []

    async with conn.transaction():
        for topic, gloss in glossary_rows:
            inserted = await conn.fetchval(GLOSSARY_UPSERT, topic, gloss)
            stats["topic_glossary"]["inserted" if inserted else "updated"] += 1

        for row in persona_rows:
            inserted = await conn.fetchval(PERSONA_UPSERT, *row)
            stats["persona_knowledge"]["inserted" if inserted else "updated"] += 1

        for row, vector in zip(world_rows, vectors):
            inserted = await conn.fetchval(
                WORLD_UPSERT,
                row["id"],
                row["section"],
                row["heading"],
                row["body"],
                row["entities"],
                row["known_by"],
                vector,
            )
            stats["world_sections"]["inserted" if inserted else "updated"] += 1

        # Delete rather than TRUNCATE: TRUNCATE would empty the table for the
        # duration of the load, and drops rows before knowing the new ones are
        # valid.
        gone = await conn.fetch(
            "DELETE FROM persona_knowledge WHERE NOT (id = ANY($1::text[])) RETURNING id",
            [r[0] for r in persona_rows],
        )
        stats["persona_knowledge"]["deleted"] = len(gone)

        gone = await conn.fetch(
            "DELETE FROM world_sections WHERE NOT (id = ANY($1::text[])) RETURNING id",
            [r["id"] for r in world_rows],
        )
        stats["world_sections"]["deleted"] = len(gone)

        gone = await conn.fetch(
            "DELETE FROM topic_glossary WHERE NOT (topic = ANY($1::text[])) RETURNING topic",
            [t for t, _ in glossary_rows],
        )
        stats["topic_glossary"]["deleted"] = len(gone)

    return stats


def write_corpus_version(path: Path, corpus_version: str, by_filename: dict[str, Any]) -> bool:
    """Stamp the version app.config reads. Returns True if the file changed.

    Contains no timestamp on purpose: identical inputs produce an identical
    file, so a re-run of an unchanged corpus leaves nothing in `git diff`.
    """
    payload = {
        "corpus_version": corpus_version,
        "files": {
            name: hashlib.sha256(
                json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            for name, data in sorted(by_filename.items())
        },
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    if previous == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def print_validation(v: Validation) -> None:
    print("=" * 72)
    print("VALIDATION")
    print("=" * 72)

    if v.errors:
        print(f"\n{len(v.errors)} ERROR(S):")
        for e in v.errors:
            print(f"  ✗ {e}")
    else:
        print("\n  ✓ no errors")

    if v.warnings:
        print(f"\n{len(v.warnings)} WARNING(S):")
        for w in v.warnings:
            print(f"  ! {w}")
    else:
        print("  ✓ no warnings")


def print_counts(v: Validation, corpus_version: str) -> None:
    print("\n" + "=" * 72)
    print("COUNTS")
    print("=" * 72)
    print("\n  persona_knowledge entries per persona per tier")
    print(f"    {'persona':<24} {'t1':>4} {'t2':>4} {'t3':>4} {'total':>6}")
    for persona_id in sorted(EXPECTED_COUNTS):
        tiers = v.per_persona_tier.get(persona_id, Counter())
        total = sum(tiers.values())
        flag = "" if total == EXPECTED_COUNTS[persona_id] else f"  (expected {EXPECTED_COUNTS[persona_id]})"
        print(f"    {persona_id:<24} {tiers[1]:>4} {tiers[2]:>4} {tiers[3]:>4} {total:>6}{flag}")
    grand = sum(sum(c.values()) for c in v.per_persona_tier.values())
    print(f"    {'TOTAL':<24} {'':>4} {'':>4} {'':>4} {grand:>6}  (expected {EXPECTED_TOTAL})")
    print(f"\n  world_sections rows: {len(v.world_rows)}")
    print(f"\n  corpus_version: {corpus_version}")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    sources = read_sources(args.persona_dir, args.world_file, args.sic_key_dir)
    corpus_version = compute_corpus_version(sources.by_filename)

    v = validate(sources, verbose=args.verbose)
    print_validation(v)
    print_counts(v, corpus_version)

    if not v.ok:
        print("\nRefusing to load: fix the errors above. Nothing was written.")
        return 1

    cache = EmbeddingCache(args.cache_file, settings.embedding_model)

    if args.dry_run:
        # Count what the cache would cover without calling anything.
        hits = sum(1 for r in v.world_rows if cache.get(r["body"]) is not None)
        print("\n" + "=" * 72)
        print("DRY RUN — nothing written, no embedding calls made")
        print("=" * 72)
        print(f"  would upsert {len(v.persona_rows)} persona_knowledge rows")
        print(f"  would upsert {len(v.world_rows)} world_sections rows")
        print(f"  would upsert {len(v.glossary_rows)} topic_glossary rows")
        print(f"  embeddings: {hits} cached, {len(v.world_rows) - hits} would need the API")
        print(f"  corpus_version: {corpus_version}")
        print(f"  would stamp: {args.corpus_version_file}")
        return 0

    embed_fn = getattr(args, "embed_fn", None) or openai_embed_fn(settings.embedding_model)
    print("\nEmbedding world sections...")
    vectors, requests = await embed_world(v.world_rows, cache, embed_fn, verbose=args.verbose)
    cache.save()

    import asyncpg
    from pgvector.asyncpg import register_vector

    # Tests hand in a factory pointing at a throwaway schema; the CLI connects
    # to DATABASE_URL.
    conn_factory = getattr(args, "conn_factory", None)
    if conn_factory is None:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
    else:
        conn = await conn_factory()
    try:
        await register_vector(conn)
        print("Loading...")
        stats = await load_into_db(
            conn, v.persona_rows, v.world_rows, vectors, v.glossary_rows
        )
    finally:
        await conn.close()

    stamped = write_corpus_version(args.corpus_version_file, corpus_version, sources.by_filename)

    print("\n" + "=" * 72)
    print("LOADED")
    print("=" * 72)
    for table, s in stats.items():
        print(
            f"  {table:<20} inserted {s['inserted']:>4}   updated {s['updated']:>4}   "
            f"deleted {s['deleted']:>4}"
        )
    print(f"\n  embedding cache:     {cache.hits} hit(s), {cache.misses} miss(es), {requests} API request(s)")
    print(f"  cache file:          {args.cache_file}")
    print(f"  corpus_version:      {corpus_version} ({'written' if stamped else 'unchanged'})")
    print(f"  stamp file:          {args.corpus_version_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="validate and report; write nothing, embed nothing")
    p.add_argument("--verbose", action="store_true", help="per-section detail and the full warning list")
    p.add_argument("--persona-dir", type=Path, default=PERSONA_DIR, dest="persona_dir")
    p.add_argument("--world-file", type=Path, default=WORLD_FILE, dest="world_file")
    p.add_argument("--sic-key-dir", type=Path, default=SIC_KEY_DIR, dest="sic_key_dir")
    p.add_argument("--cache-file", type=Path, default=CACHE_FILE, dest="cache_file")
    p.add_argument(
        "--corpus-version-file", type=Path, default=CORPUS_VERSION_FILE, dest="corpus_version_file"
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Tests inject a counting stub and a schema-scoped connection here; the CLI
    # sets neither.
    args.embed_fn = None
    args.conn_factory = None
    try:
        return asyncio.run(run(args))
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
