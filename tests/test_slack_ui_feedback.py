"""Task 6: binding the UI's explicit Re-triage to persisted feedback.

The triage route turns a validated ``feedback_parent_run``/``feedback_key`` pair into
one best-effort notification attempt: it asks the coordinator to prove the parent
*before* running the agent, reports a core triage failure only for an eligible
attempt, and records lineage with no apply once the run is persisted.

Saving feedback never re-triages by itself, an ordinary manual triage of a
webhook-owned issue stays silent, and a syntactically valid pair the store cannot
prove (unknown, foreign, historical, another issue, or GitHub-authored) falls back
to ordinary silent triage rather than a new API failure. The core ``TriageResult``
never changes because of a notification-only failure.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api import agent, memory, schemas
from api.config import Settings
from api.db import graph
from api.main import create_app
from api.notification_messages import RECOVERY_NOTE, STAGE_FAILURE_COPY, RunSnapshot
from api.notification_store import NotificationStore
from api.notifications import SlackNotifications

FIXTURES = Path(__file__).resolve().parent.parent / "api" / "fixtures"

TENANT = "falkordb"
SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
OTHER_ISSUE_KEY = f"issue:{SANDBOX}#43"
MODULE = f"module:{SANDBOX}/src/graph"
BASE_URL = "https://keystone.example"

PARENT_UUID = "00000000-0000-4000-8000-000000000009"
PARENT_RUN = f"run:{PARENT_UUID}"
CHILD_RUN = "run:00000000-0000-4000-8000-000000000002"
CHANGED_RUN = "run:00000000-0000-4000-8000-000000000003"
UI_FEEDBACK = "feedback:ui:1"
UI_FEEDBACK_2 = "feedback:ui:2"
ACTION_KEY = "action:parent"


class ExplodingStore:
    """Live mode must never fall back to fixtures."""

    def load(self, *args, **kwargs):
        raise AssertionError("live mode must not serve fixtures")


class RecordingSender:
    """Records every attempted payload, even when it raises (best-effort sender)."""

    def __init__(self, result=True, error=None):
        self.payloads: list[dict] = []
        self.result = result
        self.error = error

    def send(self, payload):
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


class FakeStore:
    """A notification-store double that proves a parent only from persisted UI feedback.

    ``feedback`` maps ``(tenant, issue_key, parent_run_key, feedback_key)`` to the
    origin that persisted it (``"ui"`` or ``"github"``); ``lineage`` is the set of runs
    the graph has marked with a proven origin. Together they emulate the real store's
    query semantics: an unregistered, foreign, other-issue or GitHub-authored
    reference proves nothing.
    """

    def __init__(self, *, snapshots=None, feedback=None, lineage=(), mark=True):
        self.snapshots = dict(snapshots or {})
        self.feedback = dict(feedback or {})
        self.lineage = set(lineage)
        self.mark_result = mark
        self.errors: dict[str, Exception] = {}
        self.calls: list[tuple] = []

    def _fail(self, name: str) -> None:
        error = self.errors.get(name)
        if error is not None:
            raise error

    def eligible_parent(self, tenant, issue_key, parent_run_key, *, ui_feedback_key=None):
        self.calls.append(("eligible_parent", tenant, issue_key, parent_run_key, ui_feedback_key))
        self._fail("parent")
        if ui_feedback_key is None or parent_run_key not in self.lineage:
            return None
        if self.feedback.get((tenant, issue_key, parent_run_key, ui_feedback_key)) != "ui":
            return None
        return self.snapshots.get(parent_run_key)

    def load_run(self, tenant, run_key):
        self.calls.append(("load_run", tenant, run_key))
        self._fail("load")
        return self.snapshots.get(run_key)

    def mark_run(self, tenant, run_key, *, issue_key, cause, event_id, parent_run_key):
        self.calls.append(("mark_run", tenant, run_key, issue_key, cause, event_id, parent_run_key))
        self._fail("mark")
        return self.mark_result

    def node_links(self, tenant, keys):
        self.calls.append(("node_links", tenant, tuple(keys)))
        self._fail("links")
        return {}

    def kinds(self) -> list[str]:
        return [call[0] for call in self.calls]


# --- fixtures and doubles ---------------------------------------------------


def fixture_result(run_key: str = CHILD_RUN) -> schemas.TriageResult:
    raw = json.loads((FIXTURES / "triage_result.json").read_text(encoding="utf-8"))
    raw["run_key"] = run_key
    return schemas.TriageResult.model_validate(raw)


def run_snapshot(*, run_key: str, label: str = "urgent") -> RunSnapshot:
    """One persisted run whose identity is the single recorded label."""
    return RunSnapshot(
        data=schemas.RecallRunData(
            run=schemas.RunSummary(
                run_key=run_key, at=1, issue_key=ISSUE_KEY, summary="A short summary.",
                fallback=False, model="test-model", llm_calls=0, ms=1,
            ),
            evidence=[],
            actions=[
                schemas.RecordedAction(
                    action_key=ACTION_KEY, kind="label", value=label, module=None,
                    status="proposed", note=None, feedback=[],
                )
            ],
            triage=schemas.TriageResult(
                run_key=run_key,
                labels=[schemas.Claim(text=label, citations=[MODULE])],
                reviewers=[], risks=[], decisions=[],
                comment_markdown="composed comment", trace=[],
            ),
        ),
        title="Seed issue",
        replay_of=None,
    )


def proven_store() -> FakeStore:
    """A store where the parent is proven for the two UI feedback keys.

    The child run has the parent's own recommendation; the third run differs.
    """
    return FakeStore(
        snapshots={
            PARENT_RUN: run_snapshot(run_key=PARENT_RUN),
            CHILD_RUN: run_snapshot(run_key=CHILD_RUN),
            CHANGED_RUN: run_snapshot(run_key=CHANGED_RUN, label="blocker"),
        },
        feedback={
            (TENANT, ISSUE_KEY, PARENT_RUN, UI_FEEDBACK): "ui",
            (TENANT, ISSUE_KEY, PARENT_RUN, UI_FEEDBACK_2): "ui",
        },
        lineage={PARENT_RUN},
    )


def coordinator(store: FakeStore, sender: RecordingSender) -> SlackNotifications:
    return SlackNotifications(store, sender, public_base_url=BASE_URL)


def live_client(tenant: str, notifications) -> TestClient:
    """A live app whose ``app.state.notifications`` is set after construction."""
    settings = Settings(tenants=(tenant,), cors_origins=("http://localhost:3001",), api_mode="live")
    app = create_app(settings=settings, fixture_store=ExplodingStore())
    app.state.notifications = notifications
    return TestClient(app)


def recording_agent(monkeypatch, result: schemas.TriageResult) -> dict:
    """Stub the agent and return the requests it was handed."""
    seen: dict = {}

    async def run_triage(tenant, request):
        seen.setdefault("requests", []).append(request)
        seen["tenant"] = tenant
        return result

    monkeypatch.setattr(agent, "run_triage", run_triage)
    return seen


def feedback_body(feedback_key: str, parent_uuid: str = PARENT_UUID) -> dict:
    return {"issue_key": ISSUE_KEY, "feedback_parent_run": parent_uuid, "feedback_key": feedback_key}


# --- the validated follow-up -------------------------------------------------


def test_validated_retriage_notifies_only_for_changed_result(monkeypatch):
    """A proven pair marks lineage, stays silent when unchanged, and notifies when the result differs."""
    store = proven_store()
    sender = RecordingSender()
    client = live_client(TENANT, coordinator(store, sender))

    unchanged = fixture_result(CHILD_RUN)
    agent_spy = recording_agent(monkeypatch, unchanged)
    first = client.post(f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))

    assert first.status_code == 200
    # The core contract is returned untouched, and the lineage fields reached the agent as validated.
    assert first.json() == unchanged.model_dump(mode="json")
    assert agent_spy["requests"][0].feedback_parent_run == UUID(PARENT_UUID)
    assert agent_spy["requests"][0].feedback_key == UI_FEEDBACK
    # The exact same issue and parent are what the coordinator was asked to prove.
    assert ("eligible_parent", TENANT, ISSUE_KEY, PARENT_RUN, UI_FEEDBACK) in store.calls
    assert sender.payloads == []
    # An unchanged follow-up still records its lineage; it just has nothing to report.
    unchanged_mark = next(call for call in store.calls if call[0] == "mark_run")
    assert unchanged_mark[1:] == (
        TENANT, CHILD_RUN, ISSUE_KEY, "ui_feedback", f"ui:{PARENT_RUN}:{UI_FEEDBACK}", PARENT_RUN,
    )

    changed = fixture_result(CHANGED_RUN)
    recording_agent(monkeypatch, changed)
    second = client.post(f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK_2))

    assert second.status_code == 200
    assert second.json() == changed.model_dump(mode="json")
    rendered = json.dumps(sender.payloads[0], ensure_ascii=False)
    assert "Changed since the parent run" in rendered
    # The message links the new run (as its run UUID) and its proven parent.
    assert f"run={CHANGED_RUN[len('run:'):]}" in rendered
    assert PARENT_RUN in rendered
    # A UI follow-up applies nothing, so there is no GitHub outcome to report.
    assert "No GitHub actions were applied by this re-triage." in rendered
    mark = [call for call in store.calls if call[0] == "mark_run"][-1]
    assert mark[1:] == (
        TENANT, CHANGED_RUN, ISSUE_KEY, "ui_feedback", f"ui:{PARENT_RUN}:{UI_FEEDBACK_2}", PARENT_RUN,
    )


@pytest.mark.parametrize("scenario", [
    "unknown parent",
    "foreign tenant",
    "historical parent",
    "feedback on another issue",
    "github-authored feedback",
])
def test_invalid_lineage_remains_silent_manual_triage(monkeypatch, scenario):
    """A well-formed but unprovable pair is ordinary triage: no Slack, no provenance, no new failure."""
    store = FakeStore(
        snapshots={PARENT_RUN: run_snapshot(run_key=PARENT_RUN), CHILD_RUN: run_snapshot(run_key=CHILD_RUN, label="blocker")},
        feedback={},
        lineage={PARENT_RUN},
    )
    if scenario == "foreign tenant":
        store.feedback[("other-tenant", ISSUE_KEY, PARENT_RUN, UI_FEEDBACK)] = "ui"
    elif scenario == "historical parent":
        store.feedback[(TENANT, ISSUE_KEY, PARENT_RUN, UI_FEEDBACK)] = "ui"
        store.lineage = set()
    elif scenario == "feedback on another issue":
        store.feedback[(TENANT, OTHER_ISSUE_KEY, PARENT_RUN, UI_FEEDBACK)] = "ui"
    elif scenario == "github-authored feedback":
        store.feedback[(TENANT, ISSUE_KEY, PARENT_RUN, UI_FEEDBACK)] = "github"

    sender = RecordingSender()
    client = live_client(TENANT, coordinator(store, sender))
    result = fixture_result(CHILD_RUN)
    recording_agent(monkeypatch, result)

    response = client.post(f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))

    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert ("eligible_parent", TENANT, ISSUE_KEY, PARENT_RUN, UI_FEEDBACK) in store.calls
    assert sender.payloads == []
    assert "mark_run" not in store.kinds()


def test_ui_save_feedback_is_silent(monkeypatch):
    """Saving feedback records the Feedback only: it never triages and never notifies."""
    store = proven_store()
    sender = RecordingSender()
    client = live_client(TENANT, coordinator(store, sender))
    monkeypatch.setattr(memory, "record_feedback", lambda *args, **kwargs: schemas.FeedbackResult(
        feedback_key=UI_FEEDBACK, action_key=ACTION_KEY, verdict="reject", rule_key="rule:1"))

    response = client.post(
        f"/tenants/{TENANT}/runs/{PARENT_UUID}/feedback",
        json={"action_key": ACTION_KEY, "verdict": "reject", "reason": "on leave"},
    )

    assert response.status_code == 200
    assert response.json()["feedback_key"] == UI_FEEDBACK
    assert store.calls == []
    assert sender.payloads == []


def test_manual_run_with_webhook_issue_is_silent(monkeypatch):
    """An ordinary manual triage of the same webhook-owned issue is never eligible."""
    store = proven_store()
    sender = RecordingSender()
    client = live_client(TENANT, coordinator(store, sender))
    result = fixture_result(CHILD_RUN)
    recording_agent(monkeypatch, result)

    response = client.post(f"/tenants/{TENANT}/triage", json={"issue_key": ISSUE_KEY})

    assert response.status_code == 200
    assert store.calls == []  # no lineage lookup at all
    assert sender.payloads == []


def test_ui_followup_failure_is_sanitized_and_preserves_503(monkeypatch):
    """Only a proven follow-up reports a triage failure; a manual one stays silent."""
    store = proven_store()
    sender = RecordingSender()
    client = live_client(TENANT, coordinator(store, sender))

    async def run_triage(tenant, request):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "run_triage", run_triage)

    failed = client.post(f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))
    assert failed.status_code == 503
    assert "boom" not in failed.text
    assert len(sender.payloads) == 1
    assert STAGE_FAILURE_COPY["triage"] in json.dumps(sender.payloads[0], ensure_ascii=False)
    assert "mark_run" not in store.kinds()

    manual = client.post(f"/tenants/{TENANT}/triage", json={"issue_key": ISSUE_KEY})
    assert manual.status_code == 503
    assert len(sender.payloads) == 1  # the manual failure sent nothing


@pytest.mark.parametrize("error,status", [
    (agent.IssueNotFound(ISSUE_KEY), 404),
    (agent.AmbiguousIssue(42, [ISSUE_KEY, OTHER_ISSUE_KEY]), 409),
])
def test_ui_followup_client_error_is_not_a_processing_failure(monkeypatch, error, status):
    """A 404/409 sends no failure alert, so a later success is not labelled Recovered."""
    store = proven_store()
    sender = RecordingSender()
    client = live_client(TENANT, coordinator(store, sender))

    async def run_triage(tenant, request):
        raise error

    monkeypatch.setattr(agent, "run_triage", run_triage)
    rejected = client.post(f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))
    assert rejected.status_code == status
    assert sender.payloads == []

    recording_agent(monkeypatch, fixture_result(CHANGED_RUN))
    ok = client.post(f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))
    assert ok.status_code == 200
    assert len(sender.payloads) == 1
    assert RECOVERY_NOTE not in json.dumps(sender.payloads[0], ensure_ascii=False)


def test_ui_followup_notification_runs_after_the_response(monkeypatch):
    """Completion and failure are queued as background tasks, not run before the route returns."""
    from types import SimpleNamespace

    from fastapi import BackgroundTasks

    from api.routes.agent import triage as triage_route

    store = proven_store()
    sender = RecordingSender()
    request_stub = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(notifications=coordinator(store, sender))))
    settings = Settings(tenants=(TENANT,), cors_origins=("http://localhost:3001",), api_mode="live")
    body = schemas.TriageRequest.model_validate(feedback_body(UI_FEEDBACK))

    changed = fixture_result(CHANGED_RUN)
    recording_agent(monkeypatch, changed)
    background = BackgroundTasks()
    result = triage_route(TENANT, body, settings, ExplodingStore(), request_stub, background)
    assert result == changed
    assert "mark_run" not in store.kinds() and sender.payloads == []
    asyncio.run(background())
    assert "mark_run" in store.kinds() and len(sender.payloads) == 1

    async def run_triage(tenant, request):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "run_triage", run_triage)
    background = BackgroundTasks()
    failed = triage_route(TENANT, body, settings, ExplodingStore(), request_stub, background)
    assert failed.status_code == 503
    assert json.loads(failed.body) == {"detail": "Live agent or graph call failed"}
    assert len(sender.payloads) == 1
    asyncio.run(failed.background())
    assert len(sender.payloads) == 2
    assert STAGE_FAILURE_COPY["triage"] in json.dumps(sender.payloads[1], ensure_ascii=False)


def test_notification_failures_cannot_change_ui_result(monkeypatch):
    """A store or formatter failure is sanitized; the core triage result never changes."""
    result = fixture_result(CHANGED_RUN)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    # A failure while validating lineage falls back to ordinary silent triage.
    broken = proven_store()
    broken.errors["parent"] = RuntimeError("store down")
    sender = RecordingSender()
    recording_agent(monkeypatch, result)
    ok = live_client(TENANT, coordinator(broken, sender)).post(
        f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))

    assert ok.status_code == 200
    assert ok.json() == result.model_dump(mode="json")
    assert sender.payloads == []

    # A formatter failure after persistence is sanitized, and lineage is still recorded.
    store = proven_store()
    sender = RecordingSender()
    monkeypatch.setattr("api.notifications.render_message", explode)
    persisted = live_client(TENANT, coordinator(store, sender)).post(
        f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))

    assert persisted.status_code == 200
    assert persisted.json() == result.model_dump(mode="json")
    assert "mark_run" in store.kinds()
    assert sender.payloads == []


def test_raw_title_feedback_remains_manual(monkeypatch):
    """A raw-title run has no issue anchor: its triage and Re-triage never carry lineage."""
    store = proven_store()
    sender = RecordingSender()
    client = live_client(TENANT, coordinator(store, sender))
    result = fixture_result(CHILD_RUN)
    agent_spy = recording_agent(monkeypatch, result)

    response = client.post(f"/tenants/{TENANT}/triage", json={"title": "Widget crashes on resize", "body": ""})

    assert response.status_code == 200
    assert agent_spy["requests"][0].title == "Widget crashes on resize"
    assert store.calls == []
    assert sender.payloads == []

    # And a raw-title request carrying context is refused outright.
    refused = client.post(f"/tenants/{TENANT}/triage", json={
        "title": "Widget crashes on resize", **feedback_body(UI_FEEDBACK)})
    assert refused.status_code == 422
    assert len(agent_spy["requests"]) == 1


class InjectedNotifications:
    """Custom adapter: unlike SlackNotifications, methods may raise directly."""

    def __init__(self, *, broken=None, eligible=True):
        self.broken = broken
        self.attempt = object() if eligible else None
        self.calls = []

    def _call(self, method):
        self.calls.append(method)
        if method == self.broken:
            raise RuntimeError("private adapter failure xoxb-secret-token")

    def begin_ui_feedback(self, **kwargs):
        self._call("begin")
        assert kwargs == {
            "tenant": TENANT, "issue_key": ISSUE_KEY,
            "parent_run_key": PARENT_RUN, "feedback_key": UI_FEEDBACK,
        }
        return self.attempt

    def complete(self, attempt, *, run_key, applied):
        assert attempt is self.attempt and run_key == CHILD_RUN and applied is None
        self._call("complete")

    def failed(self, attempt, *, stage):
        assert attempt is self.attempt and stage == "triage"
        self._call("failed")


def assert_sanitized_adapter_warning(caplog):
    warnings = [record for record in caplog.records if record.name == "api.routes.agent"
                and record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == "Slack notification step failed; triage is unaffected"
    assert warnings[0].exc_info is None
    assert "private adapter failure" not in caplog.text
    assert "xoxb-secret-token" not in caplog.text


@pytest.mark.parametrize("broken", ["begin", "complete"])
def test_injected_adapter_failure_preserves_success(monkeypatch, caplog, broken):
    """An adapter exception before or after persistence cannot change the core result."""
    notifications = InjectedNotifications(broken=broken)
    result = fixture_result()
    recording_agent(monkeypatch, result)

    response = live_client(TENANT, notifications).post(
        f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))

    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert notifications.calls == (["begin"] if broken == "begin" else ["begin", "complete"])
    assert_sanitized_adapter_warning(caplog)


@pytest.mark.parametrize("broken", ["begin", "failed"])
@pytest.mark.parametrize("error,status,detail", [
    (agent.IssueNotFound("missing"), 404, "Unknown issue"),
    (agent.AmbiguousIssue(42, [ISSUE_KEY, "issue:other/repo#42"]), 409,
     f"issue number 42 matches {ISSUE_KEY}, issue:other/repo#42"),
    (RuntimeError("core unavailable"), 503, "Live agent or graph call failed"),
])
def test_injected_adapter_failure_preserves_core_error(monkeypatch, caplog, broken, error, status, detail):
    """Notification begin/failed exceptions must not mask the original error translation."""
    notifications = InjectedNotifications(broken=broken)

    async def run_triage(tenant, request):
        raise error

    monkeypatch.setattr(agent, "run_triage", run_triage)
    response = live_client(TENANT, notifications).post(
        f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert notifications.calls == (["begin"] if broken == "begin" else ["begin", "failed"])
    assert_sanitized_adapter_warning(caplog)


@pytest.mark.parametrize("fails", [False, True])
def test_ineligible_injected_adapter_has_no_terminal_callbacks(monkeypatch, caplog, fails):
    """No proven attempt means ordinary silent triage, even with a custom adapter."""
    notifications = InjectedNotifications(eligible=False)
    result = fixture_result()

    async def run_triage(tenant, request):
        if fails:
            raise agent.IssueNotFound("missing")
        return result

    monkeypatch.setattr(agent, "run_triage", run_triage)
    response = live_client(TENANT, notifications).post(
        f"/tenants/{TENANT}/triage", json=feedback_body(UI_FEEDBACK))

    assert response.status_code == (404 if fails else 200)
    assert response.json() == ({"detail": "Unknown issue"} if fails else result.model_dump(mode="json"))
    assert notifications.calls == ["begin"]
    assert not caplog.records


# --- the same path against a real graph -------------------------------------

INTEGRATION_TENANT = "slack_ui"
INTEGRATION_PARENT = "run:00000000-0000-4000-8000-0000000000a1"
INTEGRATION_CHILD = "run:00000000-0000-4000-8000-0000000000a2"
INTEGRATION_ACTION = "action:integration-parent"
INTEGRATION_FEEDBACK = "feedback:ui:integration"


@pytest.fixture
def falkor_ui():
    """The dedicated ``slack_ui`` tenant graph; a skip is unverified, not acceptance."""
    try:
        tenant_graph = graph(INTEGRATION_TENANT)
        tenant_graph.query("RETURN 1")  # a write query creates the dedicated graph
    except Exception as exc:  # noqa: BLE001 - no server, wrong password: nothing here can run
        pytest.skip(f"FalkorDB not reachable: {exc}")
    tenant_graph.query("MATCH (n) DETACH DELETE n")
    try:
        yield tenant_graph
    finally:
        tenant_graph.delete()  # remove only the dedicated test graph


def _seed_issue(tenant_graph, *, given_by: str = "ui") -> None:
    number = 42
    tenant_graph.query(
        "MERGE (i:Issue:Node {key: $key}) "
        "SET i.number = $number, i.incoming = true, i.title = 'Seed issue', i.url = $url",
        {"key": ISSUE_KEY, "number": number,
         "url": f"https://github.com/{SANDBOX}/issues/{number}"},
    )
    triage = json.loads((FIXTURES / "triage_result.json").read_text(encoding="utf-8"))
    triage["run_key"] = INTEGRATION_PARENT
    tenant_graph.query(
        "MATCH (i:Issue {key: $issue}) "
        "CREATE (run:AgentRun:Node {key: $run, at: 100, issue_key: $issue, summary: 's', "
        "fallback: false, model: 'm', llm_calls: 0, ms: 1, triage: $triage}) "
        "CREATE (run)-[:HANDLED]->(i) "
        "CREATE (a:Action:Node {key: $action, kind: 'label', value: 'urgent', module: $module, status: 'proposed'}) "
        "CREATE (run)-[:PROPOSED]->(a) "
        "CREATE (f:Feedback:Node {key: $feedback, verdict: 'reject', given_by: $given_by, at: 1})-[:ON]->(a)",
        {"issue": ISSUE_KEY, "run": INTEGRATION_PARENT, "triage": json.dumps(triage),
         "action": INTEGRATION_ACTION, "module": MODULE, "feedback": INTEGRATION_FEEDBACK,
         "given_by": given_by},
    )


def _seed_child(tenant_graph, run_key: str = INTEGRATION_CHILD) -> None:
    """Persist the child exactly as a live triage would, before the notification completes."""
    triage = json.loads((FIXTURES / "triage_result.json").read_text(encoding="utf-8"))
    triage["run_key"] = run_key
    tenant_graph.query(
        "MATCH (i:Issue {key: $issue}) "
        "CREATE (run:AgentRun:Node {key: $run, at: 200, issue_key: $issue, summary: 's', "
        "fallback: false, model: 'm', llm_calls: 0, ms: 1, triage: $triage}) "
        "CREATE (run)-[:HANDLED]->(i) "
        "CREATE (a:Action:Node {key: $action, kind: 'label', value: 'blocker', module: $module, status: 'proposed'}) "
        "CREATE (run)-[:PROPOSED]->(a)",
        {"issue": ISSUE_KEY, "run": run_key, "triage": json.dumps(triage),
         "action": f"action:integration-child:{run_key}", "module": MODULE},
    )


def _lineage(tenant_graph, run_key: str):
    rows = tenant_graph.ro_query(
        "MATCH (r:AgentRun {key: $key}) RETURN r.notify_root, r.notify_parent, r.notify_event, "
        "r.notify_cause, r.notify_at",
        {"key": run_key},
    ).result_set
    return rows[0] if rows else None


def _integration_client(notifications, monkeypatch, child_run: str = INTEGRATION_CHILD) -> TestClient:
    def run_triage(tenant, request):
        _seed_child(graph(tenant), child_run)
        return fixture_result(child_run)

    async def agent_run_triage(tenant, request):
        return run_triage(tenant, request)

    monkeypatch.setattr(agent, "run_triage", agent_run_triage)
    return live_client(INTEGRATION_TENANT, notifications)


def test_real_graph_ui_followup_notifies_and_records_validated_lineage(falkor_ui, monkeypatch):
    _seed_issue(falkor_ui)
    store = NotificationStore(sandbox_repo=SANDBOX)
    assert store.mark_run(INTEGRATION_TENANT, INTEGRATION_PARENT, issue_key=ISSUE_KEY,
                          cause="issue_edited", event_id="delivery:d1", parent_run_key=None)
    sender = RecordingSender()
    client = _integration_client(SlackNotifications(store, sender, public_base_url=BASE_URL), monkeypatch)

    response = client.post(f"/tenants/{INTEGRATION_TENANT}/triage", json={
        "issue_key": ISSUE_KEY,
        "feedback_parent_run": INTEGRATION_PARENT[len("run:"):],
        "feedback_key": INTEGRATION_FEEDBACK,
    })

    assert response.status_code == 200
    assert len(sender.payloads) == 1
    assert "Changed since the parent run" in json.dumps(sender.payloads[0], ensure_ascii=False)
    root, parent, event, cause, at = _lineage(falkor_ui, INTEGRATION_CHILD)
    assert root == INTEGRATION_PARENT and parent == INTEGRATION_PARENT
    assert event == f"ui:{INTEGRATION_PARENT}:{INTEGRATION_FEEDBACK}" and cause == "ui_feedback"
    assert at is not None


def test_real_graph_github_authored_feedback_is_never_notified(falkor_ui, monkeypatch):
    _seed_issue(falkor_ui, given_by="github")
    store = NotificationStore(sandbox_repo=SANDBOX)
    assert store.mark_run(INTEGRATION_TENANT, INTEGRATION_PARENT, issue_key=ISSUE_KEY,
                          cause="issue_edited", event_id="delivery:d2", parent_run_key=None)
    sender = RecordingSender()
    client = _integration_client(SlackNotifications(store, sender, public_base_url=BASE_URL), monkeypatch)

    response = client.post(f"/tenants/{INTEGRATION_TENANT}/triage", json={
        "issue_key": ISSUE_KEY,
        "feedback_parent_run": INTEGRATION_PARENT[len("run:"):],
        "feedback_key": INTEGRATION_FEEDBACK,
    })

    assert response.status_code == 200
    assert sender.payloads == []
    # Feedback given by GitHub proves nothing here, so no provenance is recorded either.
    assert all(value is None for value in _lineage(falkor_ui, INTEGRATION_CHILD))
