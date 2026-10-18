"""Task 2: the tenant-scoped webhook-origin lineage store (Slack notification eligibility).

These are unit tests against a recording graph: every method must go through one
named query in ``cypher/`` with scalar parameters only, and map the row with the
shared ``recall_run_data`` mapper. The Cypher itself is exercised against a real
FalkorDB in ``tests/test_notification_store_integration.py``.
"""
from __future__ import annotations

import json

from api.cypher import query
from api.notification_store import NotificationStore
from api.schemas import RecallRunData

SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
UPSTREAM_KEY = "issue:acme/upstream#7"
RUN_KEY = "run:00000000-0000-4000-8000-000000000001"
OTHER_RUN = "run:00000000-0000-4000-8000-000000000002"
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
# notification_latest_run returns the recall row plus the issue title and replay key.
LATEST_HEADER = RECALL_HEADER + [["String", "title"], ["String", "replay_of"]]
META_HEADER = [["String", "title"], ["String", "replay_of"], ["String", "notify_root"]]
UI_META_HEADER = [["String", "title"], ["String", "replay_of"]]
MARK_HEADER = [
    ["String", "root"],
    ["String", "parent"],
    ["String", "event"],
    ["String", "cause"],
    ["Integer", "at"],
]
LINK_HEADER = [["String", "key"], ["String", "url"]]


class FakeResult:
    def __init__(self, rows, header=None):
        self.result_set = rows
        self.header = header or []


class RecordingGraph:
    """A graph stand-in that records each named query and its parameters.

    ``responses`` maps an exact query text to ``(header, rows)``; anything else
    falls back to ``default``.
    """

    def __init__(self, responses=None, default=None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}
        self.default = default if default is not None else ([], [])

    def query(self, text, params=None):
        self.calls.append((text, params))
        header, rows = self.responses.get(text, self.default)
        return FakeResult(rows, header)


def make_store(responses=None, default=None):
    graph = RecordingGraph(responses, default)
    store = NotificationStore(sandbox_repo=SANDBOX, graph_factory=lambda tenant: graph)
    return store, graph


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


def action_row(*, key: str = "action:a", value: str = "alice"):
    return {
        "action_key": key,
        "kind": "assign",
        "value": value,
        "module": MODULE,
        "status": "proposed",
        "note": None,
        "feedback": [],
    }


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


# --- latest_eligible --------------------------------------------------------


def test_latest_eligible_uses_the_named_query_and_maps_the_row():
    row = recall_row(actions=[action_row()]) + ["Seed issue", UPSTREAM_KEY]
    store, graph = make_store(default=(LATEST_HEADER, [row]))

    snapshot = store.latest_eligible("falkordb", ISSUE_KEY)

    assert isinstance(snapshot.data, RecallRunData)
    assert snapshot.data.run.run_key == RUN_KEY
    assert snapshot.title == "Seed issue"
    assert snapshot.replay_of == UPSTREAM_KEY
    assert snapshot.data.actions[0].value == "alice"
    (text, params), = graph.calls
    assert text == query("notification_latest_run")
    assert params == {"issue": ISSUE_KEY}


def test_latest_eligible_returns_none_without_a_row():
    store, graph = make_store(default=(LATEST_HEADER, []))

    assert store.latest_eligible("falkordb", ISSUE_KEY) is None
    assert len(graph.calls) == 1


# --- load_run ---------------------------------------------------------------


def test_load_run_wraps_recall_run_and_resolves_run_meta():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_run_meta"): (META_HEADER, [["Seed issue", UPSTREAM_KEY, None]]),
    }
    store, graph = make_store(responses)

    snapshot = store.load_run("falkordb", RUN_KEY)

    assert snapshot.data.run.run_key == RUN_KEY
    assert snapshot.title == "Seed issue"
    assert snapshot.replay_of == UPSTREAM_KEY
    assert [text for text, _ in graph.calls] == [query("recall_run"), query("notification_run_meta")]
    assert graph.calls[0][1] == {"run": RUN_KEY}
    assert graph.calls[1][1] == {"run": RUN_KEY, "issue": None}


def test_load_run_returns_none_when_the_run_is_unknown():
    store, graph = make_store(responses={query("recall_run"): (RECALL_HEADER, [])})

    assert store.load_run("falkordb", RUN_KEY) is None
    assert [text for text, _ in graph.calls] == [query("recall_run")]


def test_load_run_returns_none_for_a_non_issue_node():
    # recall_run finds the run, but a raw/adhoc run has no HANDLED edge, so run meta is empty.
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_run_meta"): (META_HEADER, []),
    }
    store, _ = make_store(responses)

    assert store.load_run("falkordb", RUN_KEY) is None


# --- eligible_parent --------------------------------------------------------


def test_eligible_parent_requires_recorded_lineage():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        # The run handled the issue but was never marked with an origin root.
        query("notification_run_meta"): (META_HEADER, [["Seed issue", None, None]]),
    }
    store, _ = make_store(responses)

    assert store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY) is None


def test_eligible_parent_returns_a_proven_parent():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_run_meta"): (META_HEADER, [["Seed issue", None, RUN_KEY]]),
    }
    store, graph = make_store(responses)

    snapshot = store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY)

    assert snapshot.data.run.run_key == RUN_KEY
    assert graph.calls[1][1] == {"run": RUN_KEY, "issue": ISSUE_KEY}


def test_eligible_parent_ui_uses_the_ui_query_with_the_feedback_key():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_ui_parent"): (UI_META_HEADER, [["Seed issue", None]]),
    }
    store, graph = make_store(responses)

    snapshot = store.eligible_parent(
        "falkordb", ISSUE_KEY, RUN_KEY, ui_feedback_key="feedback:f1"
    )

    assert snapshot.data.run.run_key == RUN_KEY
    assert graph.calls[1][0] == query("notification_ui_parent")
    assert graph.calls[1][1] == {"run": RUN_KEY, "issue": ISSUE_KEY, "feedback": "feedback:f1"}


def test_eligible_parent_ui_returns_none_when_the_feedback_check_fails():
    responses = {
        query("recall_run"): (RECALL_HEADER, [recall_row()]),
        query("notification_ui_parent"): (UI_META_HEADER, []),
    }
    store, _ = make_store(responses)

    assert store.eligible_parent("falkordb", ISSUE_KEY, RUN_KEY, ui_feedback_key="feedback:x") is None


# --- mark_run ---------------------------------------------------------------


def test_mark_run_uses_the_named_query_and_pins_scalar_params(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 1234)
    store, graph = make_store(
        default=(MARK_HEADER, [[RUN_KEY, None, "delivery:d1", "issue_opened", 1234]])
    )

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="issue_opened",
        event_id="delivery:d1", parent_run_key=None,
    ) is True

    (text, params), = graph.calls
    assert text == query("notification_mark_run")
    assert params == {
        "run": RUN_KEY,
        "issue": ISSUE_KEY,
        "parent": None,
        "event": "delivery:d1",
        "cause": "issue_opened",
        "now": 1234,
    }
    # The run key is a parameter, never interpolated into the query text.
    assert RUN_KEY not in text


def test_mark_run_returns_false_without_a_row(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 1)
    store, _ = make_store(default=(MARK_HEADER, []))

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="issue_opened",
        event_id="delivery:d1", parent_run_key=None,
    ) is False


def test_mark_run_rejects_a_feedback_cause_that_tries_to_create_a_root():
    store, graph = make_store()

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="ui_feedback",
        event_id="ui:x:y", parent_run_key=None,
    ) is False
    assert graph.calls == []


def test_mark_run_rejects_a_foreign_or_malformed_issue_key():
    store, graph = make_store()

    for issue_key in ("issue:other/repo#1", f"issue:{SANDBOX}#0", f"issue:{SANDBOX}#abc", "not-an-issue"):
        assert store.mark_run(
            "falkordb", RUN_KEY, issue_key=issue_key, cause="issue_opened",
            event_id="delivery:d1", parent_run_key=None,
        ) is False
    assert graph.calls == []


def test_mark_run_is_idempotent_for_the_same_write(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 5)
    store, _ = make_store(
        default=(MARK_HEADER, [[RUN_KEY, None, "delivery:d1", "issue_opened", 1]])
    )

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="issue_opened",
        event_id="delivery:d1", parent_run_key=None,
    ) is True


def test_mark_run_rejects_a_conflicting_reparent(monkeypatch):
    monkeypatch.setattr("api.notification_store._now_ms", lambda: 5)
    # The stored parent is RUN_KEY, but the caller asks to reparent under OTHER_RUN.
    store, _ = make_store(
        default=(MARK_HEADER, [[RUN_KEY, RUN_KEY, "delivery:d1", "issue_opened", 1]])
    )

    assert store.mark_run(
        "falkordb", RUN_KEY, issue_key=ISSUE_KEY, cause="github_feedback",
        event_id="comment:1", parent_run_key=OTHER_RUN,
    ) is False


# --- node_links -------------------------------------------------------------


def test_node_links_delegates_to_the_existing_store():
    url = f"https://github.com/{SANDBOX}/issues/42"
    store, graph = make_store(default=(LINK_HEADER, [[ISSUE_KEY, url]]))

    assert store.node_links("falkordb", [ISSUE_KEY, "person:alice"]) == {ISSUE_KEY: url}
    (text, params), = graph.calls
    assert text == query("webhook_node_links")
    assert params == {"keys": [ISSUE_KEY, "person:alice"]}


def test_node_links_with_no_keys_runs_no_query():
    store, graph = make_store()

    assert store.node_links("falkordb", []) == {}
    assert graph.calls == []


# --- tenant scoping ---------------------------------------------------------


def test_every_call_uses_the_requested_tenant_graph():
    seen: list[str] = []
    graph = RecordingGraph(
        responses={query("recall_run"): (RECALL_HEADER, [recall_row()]),
                   query("notification_run_meta"): (META_HEADER, [["t", None, None]])}
    )

    def factory(tenant: str):
        seen.append(tenant)
        return graph

    store = NotificationStore(sandbox_repo=SANDBOX, graph_factory=factory)
    store.latest_eligible("tenant-a", ISSUE_KEY)
    store.load_run("tenant-b", RUN_KEY)

    assert seen == ["tenant-a", "tenant-b", "tenant-b"]
