"""/api/realtime/recall — ownership, shape, and the claim boundary.

Uses the `owned_session` fixture rather than /api/realtime/token so none of
this needs a network call to OpenAI. The fixture's persona is alex_martinez,
which is the persona these topics belong to.
"""

from __future__ import annotations

import uuid

from tests.conftest import GOOD_PASSWORD, register, set_password, sign_in_as
from tests.db import scalar


def _recall(client, session_id, topics):
    return client.post(
        "/api/realtime/recall",
        json={"session_id": str(session_id), "topics": topics},
    )


def test_requires_auth(client, logged_in_client, owned_session):
    _, user_id = logged_in_client
    sid = owned_session(user_id)
    client.cookies.clear()
    assert _recall(client, sid, ["budget"]).status_code == 401


def test_returns_items_for_owned_session(client, logged_in_client, owned_session):
    c, user_id = logged_in_client
    sid = owned_session(user_id)

    topic = scalar(
        "SELECT unnest(topics) FROM persona_knowledge "
        "WHERE persona_id = 'alex_martinez' AND tier = 1 LIMIT 1"
    )
    r = _recall(c, sid, [topic])
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["posture"] == "open"  # mode is hardcoded to 1
    assert body["items"], f"no items for topic {topic!r}"
    assert len(body["items"]) <= 4
    for item in body["items"]:
        assert set(item) == {"id", "tier", "text", "earned"}
        assert item["text"]
    # Tier ordering, per the ORDER BY.
    assert [i["tier"] for i in body["items"]] == sorted(
        i["tier"] for i in body["items"]
    )


def test_tier_one_is_earned_and_tier_two_is_not(client, logged_in_client, owned_session):
    """At mode 1, Tier 1 returns in_voice; anything higher returns deflection."""
    c, user_id = logged_in_client
    sid = owned_session(user_id)

    topic = scalar(
        "SELECT unnest(topics) FROM persona_knowledge "
        "WHERE persona_id = 'alex_martinez' AND tier >= 2 LIMIT 1"
    )
    items = _recall(c, sid, [topic]).json()["items"]
    assert items
    for item in items:
        assert item["earned"] is (item["tier"] <= 1)
        expected_col = "in_voice" if item["earned"] else "deflection"
        assert item["text"] == scalar(
            f"SELECT {expected_col} FROM persona_knowledge WHERE id = %s",
            (item["id"],),
        )


def test_claim_never_appears_in_the_response(client, logged_in_client, owned_session):
    """The grader's column must not reach the model by any route.

    Asserted against every claim the persona holds, not just the matched rows:
    a leak through a neighbouring column would still be a leak.
    """
    c, user_id = logged_in_client
    sid = owned_session(user_id)

    body = _recall(c, sid, _distinct_topics()).text
    for claim in _all_claims():
        assert claim not in body, f"claim leaked into the recall response: {claim[:60]}"


def test_other_users_session_is_404_not_403(client, logged_in_client, owned_session, email):
    c, owner_id = logged_in_client
    sid = owned_session(owner_id)

    other = f"sis-test-{uuid.uuid4().hex[:12]}@wpi.edu"
    register(c, other)
    set_password(c, other)
    sign_in_as(c, other, GOOD_PASSWORD)

    r = _recall(c, sid, ["budget"])
    assert r.status_code == 404, r.text


def test_unknown_session_is_404(client, logged_in_client):
    c, _ = logged_in_client
    assert _recall(c, uuid.uuid4(), ["budget"]).status_code == 404


def test_empty_topics_is_400(client, logged_in_client, owned_session):
    c, user_id = logged_in_client
    sid = owned_session(user_id)
    assert _recall(c, sid, []).status_code == 400
    assert _recall(c, sid, ["  "]).status_code == 400


def _distinct_topics() -> list[str]:
    import psycopg2

    from app.config import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT unnest(topics) FROM persona_knowledge "
            "WHERE persona_id = 'alex_martinez'"
        )
        return [r[0] for r in cur.fetchall()]


def _all_claims() -> list[str]:
    import psycopg2

    from app.config import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT claim FROM persona_knowledge WHERE persona_id = 'alex_martinez'"
        )
        return [r[0] for r in cur.fetchall()]


def test_recall_tool_glosses_each_topic_in_the_parameter():
    """The enum says what may be passed; the glosses say what it means."""
    from app.realtime.token import _build_recall_tool

    tool = _build_recall_tool(
        [("capacity", "staff and technical capability"), ("funding", "where money comes from")]
    )
    params = tool["parameters"]["properties"]["topics"]

    assert params["items"]["enum"] == ["capacity", "funding"]
    assert "- capacity: staff and technical capability" in params["description"]
    assert "- funding: where money comes from" in params["description"]


def test_a_topic_without_a_gloss_still_reaches_the_enum():
    """The glossary loads into a different table and can lag by one load.

    An undescribed tag is worse than a described one, but dropping it would
    silently shrink what the persona can be asked about.
    """
    from app.realtime.token import _build_recall_tool

    tool = _build_recall_tool([("capacity", "staff and technical capability"), ("funding", "")])
    params = tool["parameters"]["properties"]["topics"]

    assert params["items"]["enum"] == ["capacity", "funding"]
    assert "- funding:" not in params["description"]


def test_every_authored_topic_has_a_gloss_loaded():
    """End to end against the loaded corpus, not a hand-built fixture.

    The tool's enum comes from persona_knowledge and the glosses from
    topic_glossary; they load from the same files but into different tables, so
    this is the assertion that catches them drifting apart.
    """
    missing = scalar(
        """
        SELECT count(*) FROM (
            SELECT DISTINCT unnest(topics) AS topic FROM persona_knowledge
        ) t
        LEFT JOIN topic_glossary g ON g.topic = t.topic
        WHERE g.gloss IS NULL
        """
    )
    assert missing == 0, f"{missing} authored topic(s) have no gloss"
