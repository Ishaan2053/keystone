"""B7 Task 2: the incoming-issue store and replay provenance (spec section 13).

``parse_replay`` turns a replay's first line into a canonical upstream issue key,
accepting the URL with or without backticks and rejecting anything that is not a
plain GitHub issue URL for a configured upstream repo. ``WebhookStore.upsert_issue``
writes the incoming sandbox Issue, its human author and the optional ``REPLAY_OF``
edge through one named query whose params are scalars only (never a webhook dict).

These are unit tests against a recording graph: the Cypher itself is exercised
against a real FalkorDB in ``tests/test_webhook_integration.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from api.cypher import query
from api.schemas import RecallRunData
from api.webhook_store import WebhookStore, incoming_issue_vector, parse_replay
from api.webhooks import WebhookJob

SANDBOX = "acme/keystone-sandbox"
UPSTREAM = "FalkorDB/GraphRAG-SDK"
UPSTREAM_KEY = f"issue:{UPSTREAM}#7"
ALLOWED = {UPSTREAM, "acme/upstream"}
ISSUE_KEY = f"issue:{SANDBOX}#42"
RUN_KEY = "run:00000000-0000-4000-8000-000000000001"
MODULE = "module:acme/keystone-sandbox/src/graph"
RECALL_HEADER = [
    ["String", "run_key"],
    ["Integer", "at"],
    ["String", "issue_key"],
    ["String", "summary"],
    ["Boolean", "fallback"],
    ["String", "model"],
    ["Integer", "llm_calls"],
    ["Integer", "ms"],
    ["String", "triage"],
    ["List", "evidence"],
    ["List", "actions"],
]


def make_job(**overrides) -> WebhookJob:
    values = dict(
        delivery_id="d1",
        tenant="falkordb",
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


class FakeResult:
    def __init__(self, rows, header=None):
        self.result_set = rows
        self.header = header or []


class RecordingGraph:
    """A graph stand-in that records the named query and its scalar params."""

    def __init__(self, rows=None, header=None):
        self.calls: list[tuple[str, dict]] = []
        self.rows = rows if rows is not None else [[ISSUE_KEY, False]]
        self.header = header or []

    def query(self, text, params=None):
        self.calls.append((text, params))
        return FakeResult(self.rows, self.header)


def make_store(rows=None, header=None):
    graph = RecordingGraph(rows, header)
    return WebhookStore(sandbox_repo=SANDBOX, graph_factory=lambda tenant: graph), graph


# --- parse_replay -----------------------------------------------------------


def test_parse_replay_accepts_a_plain_replayed_from_line():
    body = f"Replayed from https://github.com/{UPSTREAM}/issues/7\n\nBody text"

    assert parse_replay(body, ALLOWED) == UPSTREAM_KEY


def test_parse_replay_accepts_a_backticked_replayed_from_line():
    body = f"Replayed from `https://github.com/{UPSTREAM}/issues/7`\n\nBody text"

    assert parse_replay(body, ALLOWED) == UPSTREAM_KEY


def test_parse_replay_matches_a_configured_repo_case_insensitively_and_keeps_its_casing():
    body = f"Replayed from https://github.com/{UPSTREAM.lower()}/issues/7"

    assert parse_replay(body, ALLOWED) == UPSTREAM_KEY


def test_parse_replay_ignores_a_replayed_from_line_that_is_not_first():
    body = f"An ordinary body.\n\nReplayed from https://github.com/{UPSTREAM}/issues/7"

    assert parse_replay(body, ALLOWED) is None


def test_parse_replay_rejects_a_malformed_host():
    assert (
        parse_replay(f"Replayed from https://github.com.evil.com/{UPSTREAM}/issues/7", ALLOWED)
        is None
    )
    assert parse_replay(f"Replayed from https://evil.com/{UPSTREAM}/issues/7", ALLOWED) is None
    assert parse_replay(f"Replayed from http://github.com/{UPSTREAM}/issues/7", ALLOWED) is None


def test_parse_replay_rejects_credentials_in_the_url():
    assert (
        parse_replay(f"Replayed from https://user:token@github.com/{UPSTREAM}/issues/7", ALLOWED)
        is None
    )


def test_parse_replay_rejects_a_pull_request_url():
    assert parse_replay(f"Replayed from https://github.com/{UPSTREAM}/pull/7", ALLOWED) is None


def test_parse_replay_rejects_the_sandbox_itself_and_unknown_repos():
    assert parse_replay(f"Replayed from https://github.com/{SANDBOX}/issues/42", ALLOWED) is None
    assert parse_replay("Replayed from https://github.com/someone/else/issues/7", ALLOWED) is None


def test_parse_replay_rejects_a_malformed_number_or_extra_path():
    assert parse_replay(f"Replayed from https://github.com/{UPSTREAM}/issues/0", ALLOWED) is None
    assert parse_replay(f"Replayed from https://github.com/{UPSTREAM}/issues/abc", ALLOWED) is None
    assert (
        parse_replay(f"Replayed from https://github.com/{UPSTREAM}/issues/7/comments/1", ALLOWED)
        is None
    )
    assert (
        parse_replay(f"Replayed from https://github.com/{UPSTREAM}/issues/7?x=1", ALLOWED) is None
    )


def test_parse_replay_rejects_a_body_without_the_marker():
    assert parse_replay("Just an ordinary issue body.", ALLOWED) is None
    assert parse_replay("", ALLOWED) is None
    assert parse_replay(None, ALLOWED) is None


# --- upsert_issue -----------------------------------------------------------


def test_upsert_uses_the_sandbox_own_number_never_the_replay_target():
    store, graph = make_store()

    key = store.upsert_issue("falkordb", make_job(issue_number=42), [0.0] * 384, UPSTREAM_KEY)

    assert key == ISSUE_KEY
    ((text, params),) = graph.calls
    assert text == query("upsert_incoming_issue")
    assert params["key"] == ISSUE_KEY
    assert params["number"] == 42
    assert params["replay_key"] == UPSTREAM_KEY
    # No identity for the write comes from the replay URL.
    assert params["key"].endswith("#42") and not params["key"].endswith("#7")


def test_upsert_author_is_the_issue_user_not_the_editor():
    store, graph = make_store()

    store.upsert_issue(
        "falkordb",
        make_job(issue_author_login="octocat", sender_login="an-editor"),
        [0.0] * 384,
        None,
    )

    ((_, params),) = graph.calls
    assert params["author_login"] == "octocat"
    assert params["author_key"] == "person:octocat"
    assert params["author_url"] == "https://github.com/octocat"
    assert "an-editor" not in params.values()


def test_upsert_gives_a_bot_author_no_person_identity():
    store, graph = make_store()

    store.upsert_issue("falkordb", make_job(issue_author_is_bot=True), [0.0] * 384, None)

    ((_, params),) = graph.calls
    assert params["author_login"] is None
    assert params["author_key"] is None
    assert params["author_url"] is None


def test_upsert_passes_scalar_params_only_and_marks_the_issue_incoming():
    store, graph = make_store()

    store.upsert_issue("falkordb", make_job(), [0.5] * 384, None)

    ((_, params),) = graph.calls
    assert not any(isinstance(value, dict) for value in params.values())
    assert "payload" not in params and "job" not in params
    assert params["incoming"] is True
    assert params["number"] == 42
    assert params["title"] == "Graph traversal is slow"
    assert params["body"] == "It hangs on large graphs."
    assert params["state"] == "open"
    assert params["url"] == f"https://github.com/{SANDBOX}/issues/42"
    assert params["vec"] == [0.5] * 384


def test_upsert_converts_created_at_to_epoch_seconds():
    store, graph = make_store()
    expected = int(datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp())

    store.upsert_issue(
        "falkordb", make_job(issue_created_at="2026-10-16T00:00:00Z"), [0.0] * 384, None
    )

    ((_, params),) = graph.calls
    assert params["created_at"] == expected


def test_upsert_logs_an_absent_replay_target_and_still_returns_the_key(caplog):
    store, graph = make_store(rows=[[ISSUE_KEY, False]])

    with caplog.at_level("WARNING"):
        key = store.upsert_issue("falkordb", make_job(), [0.0] * 384, UPSTREAM_KEY)

    assert key == ISSUE_KEY
    assert any(UPSTREAM_KEY in r.getMessage() for r in caplog.records)


def test_upsert_does_not_log_when_there_is_no_replay_line(caplog):
    store, _ = make_store(rows=[[ISSUE_KEY, False]])

    with caplog.at_level("WARNING"):
        store.upsert_issue("falkordb", make_job(), [0.0] * 384, None)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# --- latest_run -------------------------------------------------------------


def triage_json(run_key: str = RUN_KEY) -> str:
    return json.dumps(
        {
            "run_key": run_key,
            "labels": [],
            "reviewers": [],
            "risks": [],
            "decisions": [],
            "comment_markdown": "x",
            "trace": [],
        }
    )


def recall_row(*, run_key: str = RUN_KEY, actions=None):
    return [
        run_key,
        1792000000,
        ISSUE_KEY,
        "summary",
        False,
        "model",
        0,
        5,
        triage_json(run_key),
        ["issue:x"],
        actions if actions is not None else [],
    ]


def test_incoming_issue_key_matches_the_upsert_identity():
    store, _ = make_store()

    assert store.incoming_issue_key(42) == ISSUE_KEY


def test_latest_run_maps_the_row_to_recall_run_data():
    action = {
        "action_key": "action:a",
        "kind": "assign",
        "value": "alice",
        "module": MODULE,
        "status": "proposed",
        "note": None,
        "feedback": [],
    }
    store, graph = make_store(rows=[recall_row(actions=[action])], header=RECALL_HEADER)

    data = store.latest_run("falkordb", ISSUE_KEY)

    assert isinstance(data, RecallRunData)
    assert data.run.run_key == RUN_KEY and data.run.issue_key == ISSUE_KEY
    assert data.actions[0].module == MODULE and data.actions[0].value == "alice"
    ((text, params),) = graph.calls
    assert text == query("webhook_latest_run")
    assert params == {"issue": ISSUE_KEY}


def test_latest_run_returns_none_for_an_unknown_issue():
    store, graph = make_store(rows=[], header=RECALL_HEADER)

    assert store.latest_run("falkordb", ISSUE_KEY) is None
    assert len(graph.calls) == 1


def test_latest_run_returns_none_when_the_run_has_no_triage():
    row = recall_row()
    row[8] = None  # run.triage is absent
    store, _ = make_store(rows=[row], header=RECALL_HEADER)

    assert store.latest_run("falkordb", ISSUE_KEY) is None


def test_latest_run_query_skips_runs_without_at_or_triage_before_ordering():
    # A null `at` sorts first under ORDER BY DESC; it and a run without a stored
    # TriageResult must be filtered before the LIMIT 1, or they hide an older valid run.
    text = " ".join(query("webhook_latest_run").split())
    guard = "WHERE run.at IS NOT NULL AND run.triage IS NOT NULL"
    assert guard in text
    assert text.index(guard) < text.index("ORDER BY run.at DESC") < text.index("LIMIT 1")


# --- recall_run -------------------------------------------------------------


def test_recall_run_uses_existing_named_query_and_tenant_graph():
    action = {
        "action_key": "action:a",
        "kind": "assign",
        "value": "alice",
        "module": MODULE,
        "status": "rejected",
        "note": None,
        "feedback": [
            {
                "feedback_key": "feedback:a",
                "action_key": "action:a",
                "verdict": "reject",
                "rule_key": "rule:a",
            }
        ],
    }
    graph = RecordingGraph([recall_row(actions=[action])], RECALL_HEADER)
    tenants = []

    def factory(tenant):
        tenants.append(tenant)
        return graph

    store = WebhookStore(sandbox_repo=SANDBOX, graph_factory=factory)
    data = store.recall_run("trusted", RUN_KEY)
    assert tenants == ["trusted"]
    assert graph.calls == [(query("recall_run"), {"run": RUN_KEY})]
    assert data.run.run_key == RUN_KEY
    assert data.actions[0].feedback[0].rule_key == "rule:a"


def test_recall_run_missing_or_incomplete_returns_none():
    store, _ = make_store(rows=[], header=RECALL_HEADER)
    assert store.recall_run("trusted", RUN_KEY) is None
    row = recall_row()
    row[8] = None
    store, _ = make_store(rows=[row], header=RECALL_HEADER)
    assert store.recall_run("trusted", RUN_KEY) is None


# --- node_links -------------------------------------------------------------


LINK_HEADER = [["String", "key"], ["String", "url"]]


def test_node_links_resolves_only_the_given_keys_stored_urls():
    url = f"https://github.com/{SANDBOX}/issues/42"
    store, graph = make_store(rows=[[ISSUE_KEY, url]], header=LINK_HEADER)

    links = store.node_links("falkordb", [ISSUE_KEY, "person:alice"])

    assert links == {ISSUE_KEY: url}
    ((text, params),) = graph.calls
    assert text == query("webhook_node_links")
    # Exactly the returned keys are passed; no source URL is synthesized in Python.
    assert params == {"keys": [ISSUE_KEY, "person:alice"]}


def test_node_links_with_no_keys_runs_no_query():
    store, graph = make_store()

    assert store.node_links("falkordb", []) == {}
    assert graph.calls == []


def test_incoming_issue_vector_uses_the_loader_text_rule(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr("api.webhook_store.issue_text", lambda title, body: f"{title}|{body}")
    monkeypatch.setattr(
        "api.webhook_store.encode",
        lambda texts: seen.update(texts=texts) or [[1.0, 2.0]],
    )

    vector = incoming_issue_vector(make_job(title="T", body="B"))

    assert seen["texts"] == ["T|B"]
    assert vector == [1.0, 2.0]
