"""/api/realtime/world_lookup — ownership, path selection, and known_by scoping.

Only the two free paths (entity, fts) are exercised here. The vector path and
the empty "none" result both embed the query at OpenAI, and this suite does not
make network calls — see the note on the `owned_session` fixture. Those two are
verified by hand against the live corpus; the distance floor they depend on is
documented with its measurements in app/realtime/world_lookup.py.
"""

from __future__ import annotations

import uuid

from tests.conftest import GOOD_PASSWORD, register, set_password, sign_in_as
from tests.db import scalar

PERSONA = "alex_martinez"  # the persona the owned_session fixture creates


def _lookup(client, session_id, query):
    return client.post(
        "/api/realtime/world_lookup",
        json={"session_id": str(session_id), "query": query},
    )


def test_requires_auth(client, logged_in_client, owned_session):
    _, user_id = logged_in_client
    sid = owned_session(user_id)
    client.cookies.clear()
    assert _lookup(client, sid, "Pier 7").status_code == 401


def test_entity_path_matches_a_canonical_name(client, logged_in_client, owned_session):
    c, user_id = logged_in_client
    sid = owned_session(user_id)

    name = scalar(
        "SELECT unnest(entities) FROM world_sections "
        "WHERE known_by @> ARRAY[%s]::text[] AND entities <> '{}' LIMIT 1",
        (PERSONA,),
    )
    r = _lookup(c, sid, f"Tell me about {name} please")
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["path"] == "entity"
    assert body["items"]
    assert len(body["items"]) <= 2
    for item in body["items"]:
        assert set(item) == {"id", "heading", "text"}
        assert item["text"]


def test_entity_path_tolerates_a_straight_apostrophe(client, logged_in_client, owned_session):
    """Two canonical names carry U+2019; a model writes ASCII."""
    c, user_id = logged_in_client
    sid = owned_session(user_id)

    curly = scalar(
        "SELECT unnest(entities) AS e FROM world_sections "
        "WHERE known_by @> ARRAY[%s]::text[] AND array_to_string(entities, '|') LIKE %s "
        "LIMIT 1",
        (PERSONA, "%’%"),
    )
    assert curly and "’" in curly, "corpus has no curly-apostrophe entity to test"

    r = _lookup(c, sid, f"Tell me about the {curly.replace(chr(0x2019), chr(39))}")
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "entity"


def test_fts_path_when_no_entity_matches(client, logged_in_client, owned_session):
    c, user_id = logged_in_client
    sid = owned_session(user_id)

    r = _lookup(c, sid, "dredging the navigation channel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == "fts"
    assert 0 < len(body["items"]) <= 2


def test_results_are_scoped_to_what_the_persona_knows(client, logged_in_client, owned_session):
    c, user_id = logged_in_client
    sid = owned_session(user_id)

    body = _lookup(c, sid, "dredging the navigation channel").json()
    assert body["items"]
    for item in body["items"]:
        known = scalar(
            "SELECT %s = ANY(known_by) FROM world_sections WHERE id = %s",
            (PERSONA, item["id"]),
        )
        assert known is True, f"{item['id']} is not known_by {PERSONA}"


def test_other_users_session_is_404_not_403(client, logged_in_client, owned_session):
    c, owner_id = logged_in_client
    sid = owned_session(owner_id)

    other = f"sis-test-{uuid.uuid4().hex[:12]}@wpi.edu"
    register(c, other)
    set_password(c, other)
    sign_in_as(c, other, GOOD_PASSWORD)

    assert _lookup(c, sid, "Pier 7").status_code == 404


def test_unknown_session_is_404(client, logged_in_client):
    c, _ = logged_in_client
    assert _lookup(c, uuid.uuid4(), "Pier 7").status_code == 404


def test_empty_query_is_400(client, logged_in_client, owned_session):
    c, user_id = logged_in_client
    sid = owned_session(user_id)
    assert _lookup(c, sid, "   ").status_code == 400


def test_alias_expansion_covers_dropped_prefixes_and_acronyms():
    """A speaker drops the prefix and uses the acronym; both must still match."""
    from app.realtime.world_lookup import _aliases

    a = _aliases("Harbortown Department of Public Works (DPW)")
    assert "Harbortown Department of Public Works (DPW)" in a
    assert "Harbortown Department of Public Works" in a
    assert "Department of Public Works" in a
    assert "DPW" in a

    # A parenthetical qualifier is stripped but is not itself an alias.
    b = _aliases("Bayline Ferry Terminal (Seasonal)")
    assert "Bayline Ferry Terminal" in b
    assert "Seasonal" not in b

    # Prefixes stack, so expansion has to reach a fixpoint.
    c = _aliases("The Harbortown Marsh Migration and Living Shoreline Assessment")
    assert "Marsh Migration and Living Shoreline Assessment" in c

    # A single generic word is too blunt to be an alias.
    assert not any(" " not in x and not x.isupper() for x in a | b | c)


def test_longest_alias_wins_and_matches_on_word_boundaries():
    from app.realtime.world_lookup import _match_aliases

    by_entity = {
        "town of harbortown": {"Town of Harbortown"},
        "harbortown town council": {"Harbortown Town Council"},
        "town council": {"Harbortown Town Council"},
        "dpw": {"Harbortown Department of Public Works (DPW)"},
    }
    by_section = {
        "downtown waterfront resilience feasibility study": {"world__10.3"},
    }

    ents, secs = _match_aliases(
        "what does the Harbortown Town Council think", by_entity, by_section
    )
    assert ents == ["Harbortown Town Council"] and secs == []

    # A study named in the query resolves to its section, not to an entity.
    ents, secs = _match_aliases(
        "tell me about the Downtown Waterfront Resilience Feasibility Study",
        by_entity,
        by_section,
    )
    assert secs == ["world__10.3"]

    # An acronym must be the whole word, not letters inside another.
    assert _match_aliases("the dpw handles that", by_entity, by_section)[0] == [
        "Harbortown Department of Public Works (DPW)"
    ]
    assert _match_aliases("he was dpwatched", by_entity, by_section) == ([], [])
    assert _match_aliases("nothing relevant here", by_entity, by_section) == ([], [])
