"""Task 2: the webhook-origin lineage store against a real FalkorDB graph.

Skipped when FalkorDB is unreachable (a skip is unverified, not acceptance).
Uses the dedicated ``slacktest`` tenant graph and deletes only that graph.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.db import graph
from api.notification_messages import RunSnapshot
from api.notification_store import NotificationStore

TENANT = "slacktest"
SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
OTHER_ISSUE_KEY = f"issue:{SANDBOX}#43"
MODULE = "module:acme/keystone-sandbox/src/graph"
HERE = Path(__file__).parent

RUN_KEY = "run:00000000-0000-4000-8000-000000000001"
ACTION_KEY = "action:root"
CHILD_KEY = "run:00000000-0000-4000-8000-000000000020"
CHILD_LOW = "run:00000000-0000-4000-8000-000000000011"
CHILD_HIGH = "run:00000000-0000-4000-8000-000000000019"
OTHER_RUN = "run:00000000-0000-4000-8000-000000000030"
OTHER_ACTION = "action:other"
OTHER_ROOT = "run:00000000-0000-4000-8000-000000000031"
MANUAL = "run:00000000-0000-4000-8000-000000000040"
HIST_RUN = "run:00000000-0000-4000-8000-000000000050"
HIST_ACTION = "action:hist"
FOREIGN_RUN = "run:00000000-0000-4000-8000-000000000060"
FOREIGN_ACTION = "action:foreign"
MISSING_RUN = "run:missing"
BARE_RUN = "run:bare"

UI_FEEDBACK = "feedback:ui:1"
UI_ACCEPT = "feedback:ui:2"
GH_FEEDBACK = "feedback:github:1"
OTHER_FEEDBACK = "feedback:ui:other"
FOREIGN_FEEDBACK = "feedback:ui:foreign"
HIST_FEEDBACK = "feedback:ui:hist"

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
def falkor_clean(falkor):
    falkor.query("MATCH (n) DETACH DELETE n")
    return falkor


@pytest.fixture
def tenant():
    return TENANT


@pytest.fixture
def issue_key():
    return ISSUE_KEY


@pytest.fixture
def run_key():
    return RUN_KEY


@pytest.fixture
def clock(monkeypatch):
    """A deterministic, mutable epoch-milliseconds clock for ``notify_at``."""
    now = {"ms": 0}
    monkeypatch.setattr("api.notification_store._now_ms", lambda: now["ms"])
    return now


def _seed_issue(falkor, key: str = ISSUE_KEY, *, number: int = 42) -> None:
    falkor.query(
        "MERGE (i:Issue:Node {key: $key}) "
        "SET i.number = $number, i.incoming = true, i.title = 'Seed issue', i.url = $url",
        {
            "key": key,
            "number": number,
            "title": "Seed issue",
            "url": f"https://github.com/{SANDBOX}/issues/{number}",
        },
    )


def _seed_run(
    falkor,
    run_key: str,
    *,
    issue_key: str = ISSUE_KEY,
    at: int = 100,
    action_key: str | None = None,
) -> None:
    triage = json.loads(
        (HERE.parent / "api/fixtures/triage_result.json").read_text(encoding="utf-8")
    )
    triage["run_key"] = run_key
    text = (
        "MATCH (i:Issue {key: $issue}) "
        "CREATE (run:AgentRun:Node {key: $run, at: $at, issue_key: $issue, summary: 's', "
        "fallback: false, model: 'm', llm_calls: 0, ms: 1, triage: $triage}) "
        "CREATE (run)-[:HANDLED]->(i)"
    )
    if action_key is not None:
        text += (
            " CREATE (a:Action:Node {key: $action, kind: 'assign', value: 'alice', module: $module, "
            "status: 'proposed'}) "
            "CREATE (run)-[:PROPOSED]->(a)"
        )
    falkor.query(
        text,
        {
            "run": run_key,
            "issue": issue_key,
            "at": at,
            "triage": json.dumps(triage),
            "action": action_key,
            "module": MODULE,
        },
    )


def _seed_feedback(
    falkor, action_key: str, feedback_key: str, *, given_by: str = "ui", verdict: str = "reject"
) -> None:
    falkor.query(
        "MATCH (a:Action {key: $action}) "
        "CREATE (f:Feedback:Node {key: $feedback, verdict: $verdict, given_by: $given_by, at: 1})"
        "-[:ON]->(a)",
        {"action": action_key, "feedback": feedback_key, "verdict": verdict, "given_by": given_by},
    )


def _lineage(falkor, run_key: str):
    rows = falkor.ro_query(
        "MATCH (r:AgentRun {key: $key}) RETURN r.notify_root, r.notify_parent, r.notify_event, "
        "r.notify_cause, r.notify_at",
        {"key": run_key},
    ).result_set
    return rows[0] if rows else None


@pytest.fixture
def store(falkor_clean, tenant, issue_key, run_key):
    _seed_issue(falkor_clean, issue_key)
    _seed_run(falkor_clean, run_key, action_key=ACTION_KEY)
    return NotificationStore(sandbox_repo=SANDBOX)


@pytest.fixture
def new_store(falkor_clean):
    return NotificationStore(sandbox_repo=SANDBOX)


def test_lineage_survives_new_store_instance(store, new_store, tenant, issue_key, run_key):
    assert store.mark_run(
        tenant,
        run_key,
        issue_key=issue_key,
        cause="issue_opened",
        event_id="delivery:d1",
        parent_run_key=None,
    )
    assert new_store.latest_eligible(tenant, issue_key).data.run.run_key == run_key


def test_ui_parent_requires_exact_feedback_and_origin(store, falkor, tenant, issue_key, run_key):
    assert store.mark_run(
        tenant,
        run_key,
        issue_key=issue_key,
        cause="issue_opened",
        event_id="delivery:d1",
        parent_run_key=None,
    )
    _seed_feedback(falkor, ACTION_KEY, UI_FEEDBACK)

    accepted = store.eligible_parent(tenant, issue_key, run_key, ui_feedback_key=UI_FEEDBACK)
    assert accepted is not None and accepted.data.run.run_key == run_key

    # Both persisted verdicts qualify.
    _seed_feedback(falkor, ACTION_KEY, UI_ACCEPT, verdict="accept")
    assert store.eligible_parent(tenant, issue_key, run_key, ui_feedback_key=UI_ACCEPT) is not None

    # Unknown feedback.
    assert (
        store.eligible_parent(tenant, issue_key, run_key, ui_feedback_key="feedback:missing")
        is None
    )
    # Feedback given by GitHub, not the UI.
    _seed_feedback(falkor, ACTION_KEY, GH_FEEDBACK, given_by="github")
    assert store.eligible_parent(tenant, issue_key, run_key, ui_feedback_key=GH_FEEDBACK) is None
    # Feedback on a different parent's Action.
    _seed_run(falkor, OTHER_RUN, at=200, action_key=OTHER_ACTION)
    assert store.mark_run(
        tenant,
        OTHER_RUN,
        issue_key=issue_key,
        cause="issue_opened",
        event_id="delivery:d2",
        parent_run_key=None,
    )
    _seed_feedback(falkor, OTHER_ACTION, OTHER_FEEDBACK)
    assert store.eligible_parent(tenant, issue_key, run_key, ui_feedback_key=OTHER_FEEDBACK) is None
    # A run that handled a different issue cannot be this issue's parent.
    _seed_issue(falkor, OTHER_ISSUE_KEY, number=43)
    _seed_run(falkor, FOREIGN_RUN, issue_key=OTHER_ISSUE_KEY, at=300, action_key=FOREIGN_ACTION)
    assert store.mark_run(
        tenant,
        FOREIGN_RUN,
        issue_key=OTHER_ISSUE_KEY,
        cause="issue_opened",
        event_id="delivery:d3",
        parent_run_key=None,
    )
    _seed_feedback(falkor, FOREIGN_ACTION, FOREIGN_FEEDBACK)
    assert (
        store.eligible_parent(tenant, issue_key, FOREIGN_RUN, ui_feedback_key=FOREIGN_FEEDBACK)
        is None
    )
    # A historic/manual run with no recorded origin is never eligible.
    _seed_run(falkor, HIST_RUN, at=400, action_key=HIST_ACTION)
    _seed_feedback(falkor, HIST_ACTION, HIST_FEEDBACK)
    assert store.eligible_parent(tenant, issue_key, HIST_RUN, ui_feedback_key=HIST_FEEDBACK) is None


def test_mark_does_not_fabricate_nodes_or_reparent_run(
    store, falkor, clock, tenant, issue_key, run_key
):
    # A run that never HANDLED the incoming issue is refused and never created.
    assert (
        store.mark_run(
            tenant,
            MISSING_RUN,
            issue_key=issue_key,
            cause="issue_opened",
            event_id="delivery:missing",
            parent_run_key=None,
        )
        is False
    )
    assert falkor.ro_query(
        "MATCH (r:AgentRun {key: $key}) RETURN count(r)", {"key": MISSING_RUN}
    ).result_set == [[0]]
    # A run with no HANDLED edge is refused too.
    falkor.query("CREATE (r:AgentRun:Node {key: $key})", {"key": BARE_RUN})
    assert (
        store.mark_run(
            tenant,
            BARE_RUN,
            issue_key=issue_key,
            cause="issue_opened",
            event_id="delivery:bare",
            parent_run_key=None,
        )
        is False
    )

    # The first write roots the lineage at its own key and records the time once.
    clock["ms"] = 1000
    assert (
        store.mark_run(
            tenant,
            run_key,
            issue_key=issue_key,
            cause="issue_opened",
            event_id="delivery:d1",
            parent_run_key=None,
        )
        is True
    )
    first = _lineage(falkor, run_key)
    assert first[0] == run_key and first[1] is None and first[2] == "delivery:d1"
    # A repeated write preserves the original event, parent and time even with a later clock.
    clock["ms"] = 9999
    assert (
        store.mark_run(
            tenant,
            run_key,
            issue_key=issue_key,
            cause="issue_opened",
            event_id="delivery:d1",
            parent_run_key=None,
        )
        is True
    )
    assert _lineage(falkor, run_key) == first

    # A descendant inherits the exact parent's root.
    _seed_run(falkor, CHILD_KEY, at=200)
    assert (
        store.mark_run(
            tenant,
            CHILD_KEY,
            issue_key=issue_key,
            cause="issue_edited",
            event_id="delivery:d2",
            parent_run_key=run_key,
        )
        is True
    )
    child = _lineage(falkor, CHILD_KEY)
    assert child[0] == run_key and child[1] == run_key

    # A conflicting reparent is refused and changes nothing.
    _seed_run(falkor, OTHER_ROOT, at=250)
    assert (
        store.mark_run(
            tenant,
            OTHER_ROOT,
            issue_key=issue_key,
            cause="issue_opened",
            event_id="delivery:d3",
            parent_run_key=None,
        )
        is True
    )
    assert (
        store.mark_run(
            tenant,
            CHILD_KEY,
            issue_key=issue_key,
            cause="issue_edited",
            event_id="delivery:d4",
            parent_run_key=OTHER_ROOT,
        )
        is False
    )
    assert _lineage(falkor, CHILD_KEY) == child


def test_latest_eligible_ignores_later_manual_runs(falkor_clean, clock, tenant, issue_key, run_key):
    _seed_issue(falkor_clean)
    store = NotificationStore(sandbox_repo=SANDBOX)
    _seed_run(falkor_clean, run_key, at=100)
    clock["ms"] = 1000
    assert store.mark_run(
        tenant,
        run_key,
        issue_key=issue_key,
        cause="issue_opened",
        event_id="delivery:d1",
        parent_run_key=None,
    )
    # A later manual run carries no lineage and must never be selected.
    _seed_run(falkor_clean, MANUAL, at=300)
    # Two eligible descendants tie on notify_at: the higher run key wins.
    _seed_run(falkor_clean, CHILD_LOW, at=150)
    _seed_run(falkor_clean, CHILD_HIGH, at=150)
    assert CHILD_HIGH > CHILD_LOW
    clock["ms"] = 2000
    assert store.mark_run(
        tenant,
        CHILD_LOW,
        issue_key=issue_key,
        cause="github_feedback",
        event_id="comment:1",
        parent_run_key=run_key,
    )
    assert store.mark_run(
        tenant,
        CHILD_HIGH,
        issue_key=issue_key,
        cause="github_feedback",
        event_id="comment:2",
        parent_run_key=run_key,
    )

    snapshot = store.latest_eligible(tenant, issue_key)

    assert snapshot.data.run.run_key == CHILD_HIGH
    assert snapshot.title == "Seed issue"


def test_latest_eligible_is_none_without_recorded_lineage(falkor_clean, tenant, issue_key):
    _seed_issue(falkor_clean)
    _seed_run(falkor_clean, MANUAL, at=300)

    assert NotificationStore(sandbox_repo=SANDBOX).latest_eligible(tenant, issue_key) is None


def test_snapshot_is_detached(store, falkor, tenant, issue_key, run_key):
    assert store.mark_run(
        tenant,
        run_key,
        issue_key=issue_key,
        cause="issue_opened",
        event_id="delivery:d1",
        parent_run_key=None,
    )
    reserved = store.eligible_parent(tenant, issue_key, run_key)
    assert reserved.data.actions[0].status == "proposed"

    # A later graph mutation cannot change the reserved comparison baseline.
    falkor.query("MATCH (a:Action {key: $key}) SET a.status = 'applied'", {"key": ACTION_KEY})
    assert store.load_run(tenant, run_key).data.actions[0].status == "applied"
    assert reserved.data.actions[0].status == "proposed"

    # The snapshot deep-copies, so mutating the recalled data is harmless.
    source = store.load_run(tenant, run_key).data
    detached = RunSnapshot(data=source, title="Seed issue", replay_of=None)
    source.actions[0].status = "refused"
    assert detached.data.actions[0].status == "applied"
