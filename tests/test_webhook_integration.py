"""B7 Task 2: the incoming-issue upsert against a real FalkorDB graph.

Skipped when FalkorDB is unreachable (ruling R5: a skip is unverified, not
acceptance). Uses a dedicated tenant graph and deletes only that graph.
"""
from __future__ import annotations

import logging

import pytest

from api.db import graph
from api.webhook_store import WebhookStore
from api.webhooks import WebhookJob

TENANT = "b7test"
SANDBOX = "acme/keystone-sandbox"
UPSTREAM = "acme/upstream"
UPSTREAM_KEY = f"issue:{UPSTREAM}#7"
MISSING_KEY = f"issue:{UPSTREAM}#999"
ISSUE_KEY = f"issue:{SANDBOX}#42"
VECTOR = [0.1] * 384

try:
    graph(TENANT).query("RETURN 1")  # a write query creates the dedicated graph
except Exception as exc:  # no server, wrong password: nothing here can run
    pytest.skip(f"FalkorDB not reachable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def falkor():
    g = graph(TENANT)
    try:
        yield g
    finally:
        g.delete()  # remove only the dedicated test graph


@pytest.fixture
def store(falkor):
    falkor.query("MATCH (n) DETACH DELETE n")
    falkor.query(
        "MERGE (u:Issue:Node {key: $key}) SET u.number = 7, u.incoming = false, u.title = 'upstream'",
        {"key": UPSTREAM_KEY},
    )
    return WebhookStore(sandbox_repo=SANDBOX)


def make_job(**overrides) -> WebhookJob:
    values = dict(
        delivery_id="d1",
        tenant=TENANT,
        event="issues",
        action="opened",
        issue_number=42,
        title="Graph traversal is slow",
        body="It hangs on large graphs.",
        issue_state="open",
        issue_author_login="octocat",
        issue_author_url="https://github.com/octocat",
        issue_author_is_bot=False,
        issue_created_at="2026-10-16T00:00:00Z",
        sender_login="octocat",
        comment_id=None,
        comment_body=None,
    )
    values.update(overrides)
    return WebhookJob(**values)


def test_upsert_writes_an_incoming_issue_author_and_replay_edge(store, falkor):
    key = store.upsert_issue(TENANT, make_job(), VECTOR, UPSTREAM_KEY)

    assert key == ISSUE_KEY
    (labels, number, incoming, title, state, url, created_at) = falkor.ro_query(
        "MATCH (i:Issue:Node {key: $key}) RETURN labels(i), i.number, i.incoming, i.title, i.state, i.url, i.created_at",
        {"key": ISSUE_KEY},
    ).result_set[0]
    assert "Issue" in labels and "Node" in labels
    assert (number, incoming, title, state) == (42, True, "Graph traversal is slow", "open")
    assert url == f"https://github.com/{SANDBOX}/issues/42"
    assert created_at == 1_792_108_800  # 2026-10-16T00:00:00Z

    # AUTHORED points from the human issue author to the issue, never the reverse.
    authored = falkor.ro_query(
        "MATCH (p:Person {key: 'person:octocat'})-[:AUTHORED]->(i:Issue {key: $key}) RETURN p.login",
        {"key": ISSUE_KEY},
    ).result_set
    assert authored == [["octocat"]]
    reverse = falkor.ro_query(
        "MATCH (i:Issue {key: $key})-[:AUTHORED]->(:Person) RETURN count(*)",
        {"key": ISSUE_KEY},
    ).result_set
    assert reverse == [[0]]

    # REPLAY_OF points from the incoming issue to the loaded upstream issue.
    replay = falkor.ro_query(
        "MATCH (i:Issue {key: $key})-[:REPLAY_OF]->(u:Issue) RETURN u.key",
        {"key": ISSUE_KEY},
    ).result_set
    assert replay == [[UPSTREAM_KEY]]

    # The 384-d vector is retrievable through vecf32 (distance 0 to itself).
    distance = falkor.ro_query(
        "MATCH (i:Issue {key: $key}) RETURN vec.euclideanDistance(i.embedding, vecf32($vec))",
        {"key": ISSUE_KEY, "vec": VECTOR},
    ).result_set[0][0]
    assert distance == pytest.approx(0.0, abs=1e-6)


def test_an_edit_preserves_created_at_and_reconciles_the_edges(store, falkor):
    store.upsert_issue(TENANT, make_job(title="first"), VECTOR, UPSTREAM_KEY)
    (created_at,) = falkor.ro_query(
        "MATCH (i:Issue {key: $key}) RETURN i.created_at", {"key": ISSUE_KEY}
    ).result_set[0]

    # An edit that drops the replay line and changes the title.
    store.upsert_issue(TENANT, make_job(title="second", body="edited"), VECTOR, None)

    assert falkor.ro_query(
        "MATCH (i:Issue {key: $key}) RETURN count(i)", {"key": ISSUE_KEY}
    ).result_set == [[1]]
    (title, kept_created_at) = falkor.ro_query(
        "MATCH (i:Issue {key: $key}) RETURN i.title, i.created_at", {"key": ISSUE_KEY}
    ).result_set[0]
    assert title == "second"
    assert kept_created_at == created_at
    assert falkor.ro_query(
        "MATCH (i:Issue {key: $key})-[:REPLAY_OF]->(:Issue) RETURN count(*)", {"key": ISSUE_KEY}
    ).result_set == [[0]]
    assert falkor.ro_query(
        "MATCH (:Person)-[a:AUTHORED]->(i:Issue {key: $key}) RETURN count(a)", {"key": ISSUE_KEY}
    ).result_set == [[1]]


def test_an_absent_replay_target_is_logged_and_never_fabricated(store, falkor, caplog):
    with caplog.at_level(logging.WARNING):
        store.upsert_issue(TENANT, make_job(), VECTOR, MISSING_KEY)

    assert any(MISSING_KEY in r.getMessage() for r in caplog.records)
    assert falkor.ro_query(
        "MATCH (i:Issue {key: $key})-[:REPLAY_OF]->(:Issue) RETURN count(*)", {"key": ISSUE_KEY}
    ).result_set == [[0]]
    # The missing upstream Issue is not fabricated as an empty node.
    assert falkor.ro_query(
        "MATCH (u:Issue {key: $key}) RETURN count(u)", {"key": MISSING_KEY}
    ).result_set == [[0]]


def test_a_bot_issue_author_gets_no_person_edge(store, falkor):
    store.upsert_issue(TENANT, make_job(issue_author_is_bot=True), VECTOR, None)

    assert falkor.ro_query(
        "MATCH (p:Person) RETURN count(p)"
    ).result_set == [[0]]
    assert falkor.ro_query(
        "MATCH (i:Issue {key: $key})-[:AUTHORED]-(:Person) RETURN count(*)", {"key": ISSUE_KEY}
    ).result_set == [[0]]
