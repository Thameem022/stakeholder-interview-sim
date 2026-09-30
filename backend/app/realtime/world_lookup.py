"""
Realtime support endpoint — fulfillment for the `world_lookup` tool.

Public information about Harbortown, as opposed to `recall`, which is what this
particular persona knows and thinks. The split matters at the prompt level: a
persona may describe the ferry terminal's schedule without that being a
disclosure it had to earn.

Three lookup strategies, tried in order and stopping at the first hit:

  entity  an exact-ish handle on a named thing — the surest signal we have, and
          free, so it goes first
  fts     the query's words appear in a section; still free
  vector  only when both miss, because it is the one path that costs an
          embedding round trip mid-turn

Every path is scoped by `known_by`: a section this persona has no plausible
awareness of is not theirs to recite, however well it matches.
"""

from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Annotated, Dict, List, Literal, Optional, Set, Tuple
from uuid import UUID

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, rate_limited, require_user
from app.db import get_pool
from app.realtime.events import record_recall_event
from app.realtime.session import InterviewSession
from app.vector_store import embed_one

logger = logging.getLogger(__name__)

router = APIRouter()

# Only the vector path spends anything, and only when the two free paths miss.
_WORLD_LIMIT = ("realtime-world-lookup", 300, 3600)

# Two sections, not four. A world section is a ~200-word block of prose and all
# of it is read aloud; two is already a long speaking turn.
_LIMIT = 2

# Cosine distance, so lower is closer. Above this the nearest section is not
# about the question at all, and returning it produces a persona confidently
# reciting something unrelated — worse than saying nothing.
#
# Measured against the live 91-section corpus rather than guessed. Ten probe
# queries: six about Harbortown landed 0.365-0.593 (their second-best results
# reached 0.622), four deliberately off-topic — sourdough, tire rotation, QCD,
# the 1998 World Cup — landed 0.837-0.924. This sits mid-gap, roughly 0.12 clear
# on either side. Worth re-measuring if the corpus or embedding model changes.
_MAX_DISTANCE = 0.72

# ts_rank floor for the OR-semantics FTS query below. OR matching alone puts
# most of the corpus in the result set, so the floor — not the match — is what
# makes this path selective.
#
# Measured over 17 real town questions and 8 deliberately off-topic ones, after
# migration 0009 put the heading into the tsvector at weight A (before it, rank
# clustered in 0.015-0.061 and separated nothing). Lowest genuine question:
# 0.1520. Highest noise: 0.1422 — "how do I rotate the tires on a 2014 Honda
# Civic", which scores only because a bare year is a common token in a corpus
# full of study dates.
#
# The two failure modes are not symmetric, which is what sets the value. A
# question wrongly rejected here falls through to the vector path and is still
# answered correctly, just ~200ms slower. A junk match wrongly ACCEPTED is
# terminal — this path returns first, so vector never runs, and the persona
# recites an unrelated section with confidence. So the floor sits above the
# noise rather than below the weakest real question.
#
# The margin over noise is 0.0098 — real but thin, on 25 samples.
#
# The value is 0.18 rather than 0.1520 because rejecting junk is not the only
# job. A question can be genuinely about the town, clear the noise floor, and
# still be answered better by embeddings: "what happens when there's a big
# storm - who responds?" scrapes in at 0.152 and returns Coastal Ecosystems,
# where the vector path returns Emergency Mutual Aid and Emergency Management.
# Barely-above-floor lexical matches are matches on incidental words.
#
# 0.18 is the knee. It sheds those weak matches while keeping ~76% of questions
# on a sub-5ms path; raising it further to 0.25 costs three more questions and
# buys no additional precision. One known case survives it: "what's the school
# situation like in town?" ranks 0.306 on a generic town-overview section, and
# no floor that keeps a useful share excludes it.
#
# Re-measure if the corpus grows or the questions change character.
_MIN_RANK = 0.18

_SELECT = "SELECT id, heading, body FROM world_sections"
_KNOWN_BY = "known_by @> ARRAY[$1]::text[]"


class WorldLookupRequest(BaseModel):
    session_id: UUID
    query: str


class WorldItem(BaseModel):
    id: str
    heading: str
    text: str


class WorldLookupResponse(BaseModel):
    items: List[WorldItem]
    # "none" when all three paths came back empty — distinct from a vector miss,
    # and the difference is the whole point of recording the path.
    path: Literal["entity", "fts", "vector", "none"]


# Alias index, built once per process. The corpus changes only when
# load_knowledge.py runs, which is a manual step followed by a restart.
_alias_cache: Optional[Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]] = None

# A parenthetical that is an acronym ("(DPW)") becomes its own alias; one that
# is a qualifier ("(Seasonal)") is only stripped.
_PAREN = re.compile(r"\s*\(([^)]*)\)")
_ACRONYM = re.compile(r"^[A-Z][A-Z&]+$")
# Section numbering ("10.3 ") and the year a study is known by ("(2018)").
_LEADING_NUMBER = re.compile(r"^\d+(?:\.\d+)*\s+")
_TRAILING_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")


def _normalize(s: str) -> str:
    """Fold case and curly apostrophes.

    Two canonical names carry U+2019 ("Harbor Master's Office", "St. Brendan's
    Community Church") and a model writes ASCII. Without this the entity path
    silently misses both, and the miss looks like a corpus gap rather than a
    punctuation mismatch.
    """
    return s.lower().replace("\u2019", "'")


def _aliases(name: str) -> Set[str]:
    """The forms of `name` a speaker might actually use.

    Matching the full canonical name inside the query fails the moment someone
    drops a prefix: "Department of Public Works" does not contain "Harbortown
    Department of Public Works (DPW)". So the name is expanded instead —
    parenthetical stripped, acronym promoted, leading "Harbortown"/"The"
    removed — and any form may match.

    Expansion runs to a fixpoint because the prefixes stack: "The Harbortown
    Marsh Migration Assessment" has to shed both to reach the form a student
    would say.
    """
    forms = {name, _PAREN.sub("", name).strip()}
    for inner in _PAREN.findall(name):
        inner = inner.strip()
        if _ACRONYM.match(inner):
            forms.add(inner)

    while True:
        grown = set(forms)
        for v in forms:
            for prefix in ("Harbortown ", "The "):
                if v.lower().startswith(prefix.lower()):
                    grown.add(v[len(prefix):])
        if grown == forms:
            break
        forms = grown

    # A one-word alias is too blunt to match on — except an acronym, which is
    # precisely the distinctive form worth keeping.
    return {
        f
        for f in forms
        if (_ACRONYM.match(f) and len(f) >= 3) or (len(f) >= 8 and " " in f)
    }


async def _alias_index() -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """(alias -> canonical entity names, alias -> section ids).

    Two maps because the two kinds of name are filtered differently: entities
    live in a tagged array column, studies are only ever a heading. The studies
    are harvested here rather than tagged in the corpus because nothing tags
    them — entity tagging covers Section 9 (organizations) and two rows of
    Section 10, so the five named studies, which are among the most quotable
    things in the world bible, were unreachable by name.
    """
    global _alias_cache
    if _alias_cache is None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            entities = await conn.fetch(
                "SELECT DISTINCT unnest(entities) AS e FROM world_sections"
            )
            # A trailing (YYYY) is what distinguishes the five named studies
            # from the framing sections around them (10.1, 10.7, 10.8 carry no
            # year), and nothing else in the corpus has one.
            studies = await conn.fetch(
                r"SELECT id, heading FROM world_sections "
                r"WHERE heading ~ '\(\d{4}\)\s*$'"
            )

        by_entity: Dict[str, Set[str]] = {}
        for r in entities:
            for a in _aliases(r["e"]):
                by_entity.setdefault(_normalize(a), set()).add(r["e"])

        by_section: Dict[str, Set[str]] = {}
        for r in studies:
            name = _TRAILING_YEAR.sub("", _LEADING_NUMBER.sub("", r["heading"])).strip()
            for a in _aliases(name):
                by_section.setdefault(_normalize(a), set()).add(r["id"])

        _alias_cache = (by_entity, by_section)
    return _alias_cache


def _match_aliases(
    query: str,
    by_entity: Dict[str, Set[str]],
    by_section: Dict[str, Set[str]],
) -> Tuple[List[str], List[str]]:
    """Canonical entities and section ids named in the query, longest alias first.

    Longest-first is not cosmetic: the aliases nest. "Town Council" is inside
    "Harbortown Town Council", and "Harbor & Estuary Alliance" inside its own
    fuller form. Once a longer alias matches, shorter ones contained in it are
    skipped, so a question about one institution does not drag in every section
    tagged with the town.

    Matching is on word boundaries rather than raw substring: "OEM" must be the
    word OEM, not three letters inside another one.
    """
    q = _normalize(query)
    entities: List[str] = []
    sections: List[str] = []
    claimed: List[str] = []

    for alias in sorted(set(by_entity) | set(by_section), key=len, reverse=True):
        if not re.search(rf"\b{re.escape(alias)}\b", q):
            continue
        if any(alias in c for c in claimed):
            continue
        claimed.append(alias)
        for e in by_entity.get(alias, ()):
            if e not in entities:
                entities.append(e)
        for sid in by_section.get(alias, ()):
            if sid not in sections:
                sections.append(sid)

    return entities, sections


async def _by_entity(
    conn, persona_id: str, entities: List[str], sections: List[str]
):
    """Sections tagged with a named entity, or that *are* a named study.

    The two are unioned in one statement rather than tried in sequence: a
    question can name both a study and an organization, and ordering by how
    many matched names a row carries is only meaningful across the whole
    candidate set.
    """
    return await conn.fetch(
        f"""
        {_SELECT}
        WHERE {_KNOWN_BY}
          AND (entities && $2::text[] OR id = ANY($3::text[]))
        ORDER BY (
                   cardinality(ARRAY(SELECT unnest(entities)
                                     INTERSECT SELECT unnest($2::text[])))
                   + CASE WHEN id = ANY($3::text[]) THEN 1 ELSE 0 END
                 ) DESC,
                 id
        LIMIT {_LIMIT}
        """,
        persona_id,
        entities,
        sections,
    )


async def _by_fts(conn, persona_id: str, query: str):
    """Lexical match, OR across terms, floored by rank.

    plainto_tsquery ANDs every surviving term, and conversational filler is not
    a stopword: "What kind of businesses are downtown?" parses to
    'kind' & 'busi' & 'downtown', and "kind" appears in no section, so the whole
    query returns nothing while "businesses downtown" alone matches 16 rows.
    Natural questions carry filler content words, so AND semantics threw away
    most of this path's value.

    ORing the terms recovers those matches but is far too permissive on its own
    — it puts most of the corpus in the result set — so selectivity comes from
    the ts_rank floor instead of from the match. That floor only separates
    because migration 0009 weights the heading above the body.

    The tsquery is built by rewriting plainto_tsquery's own output rather than
    from the raw string: it is already parsed, stemmed and escaped, so there is
    nothing left to inject. An all-stopword query yields an empty tsquery,
    which matches nothing and falls through, which is correct.
    """
    return await conn.fetch(
        f"""
        WITH q AS (
            SELECT replace(plainto_tsquery('english', $2)::text, ' & ', ' | ')::tsquery AS tq
        )
        {_SELECT}, q
        WHERE {_KNOWN_BY}
          AND q.tq IS NOT NULL
          AND tsv @@ q.tq
          AND ts_rank(tsv, q.tq) >= $3
        ORDER BY ts_rank(tsv, q.tq) DESC, id
        LIMIT {_LIMIT}
        """,
        persona_id,
        query,
        _MIN_RANK,
    )


async def _by_vector(conn, persona_id: str, query_vec):
    return await conn.fetch(
        f"""
        {_SELECT}
        WHERE {_KNOWN_BY} AND (embedding <=> $2) < $3
        ORDER BY embedding <=> $2
        LIMIT {_LIMIT}
        """,
        persona_id,
        query_vec,
        _MAX_DISTANCE,
    )


@router.post(
    "/realtime/world_lookup",
    response_model=WorldLookupResponse,
    dependencies=[Depends(rate_limited(*_WORLD_LIMIT))],
)
async def world_lookup(
    req: WorldLookupRequest, user: Annotated[CurrentUser, Depends(require_user)]
) -> WorldLookupResponse:
    started = perf_counter()

    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query required")

    # Someone else's session is indistinguishable from a nonexistent one — 404,
    # not 403, matching /realtime/recall and /realtime/transcript.
    session = await InterviewSession.load(req.session_id, user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    persona_id = session.persona_id

    # TODO: read from interview_sessions (column `disclosure_mode`, added in
    # migration 0007). Recorded on the event row so world calls and recall
    # calls can be read on the same axis.
    mode = 1

    pool = await get_pool()
    rows: list = []
    path: str = "none"

    async with pool.acquire() as conn:
        by_entity, by_section = await _alias_index()
        entities, sections = _match_aliases(query, by_entity, by_section)
        if entities or sections:
            rows = await _by_entity(conn, persona_id, entities, sections)
            if rows:
                path = "entity"

        if not rows:
            rows = await _by_fts(conn, persona_id, query)
            if rows:
                path = "fts"

        if not rows:
            # The only path that costs anything, so it runs last and only on a
            # double miss. A failed embedding returns empty rather than raising:
            # the persona improvising beats the turn dying.
            try:
                query_vec = np.array(await embed_one(query), dtype="float32")
            except Exception as e:
                logger.warning(f"world_lookup embed failed: {e}")
                query_vec = None
            if query_vec is not None:
                rows = await _by_vector(conn, persona_id, query_vec)
                if rows:
                    path = "vector"

    items = [
        WorldItem(id=r["id"], heading=r["heading"] or "", text=r["body"] or "")
        for r in rows
    ]

    record_recall_event(
        session_id=req.session_id,
        tool="world_lookup",
        query=query,
        mode=mode,
        ids_returned=[i.id for i in items],
        # World sections carry no disclosure gate — they are public facts about
        # the town, not something the interviewer has to earn. Filled rather
        # than left empty so `earned` is uniform across both tools and a query
        # over the column needs no special case.
        earned=[True] * len(items),
        path=path,
        latency_ms=(perf_counter() - started) * 1000,
    )

    return WorldLookupResponse(items=items, path=path)  # type: ignore[arg-type]
