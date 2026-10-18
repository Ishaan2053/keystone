"""Task 4: verified issue jobs notify only on their real outcome.

The webhook pipeline hooks a best-effort notification coordinator into the real
upsert -> triage -> apply order: an attempt is established from the
construction-time canonical issue key before ingest, each existing core stage is
wrapped with ``failed(stage)`` plus a bare re-raise, and ``complete`` fires only
after apply returns. Slack can never change a core result: a throwing injected
notifier is isolated at the call site.

These tests use the Task 3 recording notification store/sender (never a graph, an
LLM or the network) and the Task 3 pipeline doubles.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_notifications import FakeStore as NotifStore
from test_notifications import RecordingSender, run_snapshot
from test_webhook_pipeline import (
    ISSUE_KEY,
    RUN_KEY,
    TENANT,
    FakeApply,
    FakeStore,
    make_job,
    make_pipeline,
    make_triage,
    wait_until,
)

from api import agent, memory, schemas
from api.config import Settings
from api.db import graph
from api.fixture_store import FixtureStore
from api.github import SandboxGitHub
from api.main import create_app
from api.notification_messages import RECOVERY_NOTE, STAGE_FAILURE_COPY, RunSnapshot
from api.notifications import SlackNotifications
from api.webhooks import WebhookWorker, build_job, verify_signature

SECRET = "webhook-secret"
BOT = "keystone-bot"
SANDBOX = "acme/keystone-sandbox"
BASE_URL = "https://keystone.example"
PARENT_KEY = "run:00000000-0000-4000-8000-000000000009"


def webhook_settings() -> Settings:
    return Settings(
        tenants=(TENANT,),
        api_mode="live",
        sandbox_repo=SANDBOX,
        bot_login=BOT,
        webhook_secret=SECRET,
        public_base_url=BASE_URL,
    )


def opened_payload(**overrides) -> dict:
    payload = {
        "action": "opened",
        "repository": {"full_name": SANDBOX},
        "sender": {"login": "octocat"},
        "issue": {
            "number": 42,
            "title": "Graph traversal is slow",
            "body": "It hangs on large graphs.",
            "state": "open",
            "created_at": "2026-10-16T00:00:00Z",
            "user": {"login": "octocat", "html_url": "https://github.com/octocat", "type": "User"},
        },
    }
    payload.update(overrides)
    return payload


def sign(raw: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def post(client, payload, *, event="issues", delivery="d1", signature="auto"):
    body = json.dumps(payload).encode("utf-8")
    headers = {"X-GitHub-Event": event}
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    if signature == "auto":
        headers["X-Hub-Signature-256"] = sign(body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhooks/github", content=body, headers=headers)


def applied_action() -> schemas.ActionResult:
    return schemas.ActionResult(
        action_key="action:1", kind="label", value="graph", status="applied"
    )


def fallback_snapshot(run_key: str) -> RunSnapshot:
    base = run_snapshot(run_key=run_key)
    data = base.data.model_copy(update={"run": base.data.run.model_copy(update={"fallback": True})})
    return RunSnapshot(data=data, title=base.title, replay_of=base.replay_of)


def coordinator(store, sender) -> SlackNotifications:
    return SlackNotifications(store, sender, public_base_url=BASE_URL)


class RecordingNotifier:
    """A minimal adapter double: records each call and can raise on selected methods."""

    def __init__(self, *, raise_on=()):
        self._raise_on = set(raise_on)
        self.begins: list[dict] = []
        self.completes: list[tuple] = []
        self.failures: list[tuple] = []

    def begin_issue(self, **kwargs):
        self.begins.append(kwargs)
        if "begin" in self._raise_on:
            raise RuntimeError("adapter boom")
        return "attempt"

    def complete(self, attempt, *, run_key, applied):
        self.completes.append((attempt, run_key, applied))
        if "complete" in self._raise_on:
            raise RuntimeError("adapter boom")

    def failed(self, attempt, *, stage, run_key=None):
        self.failures.append((attempt, stage, run_key))
        if "failed" in self._raise_on:
            raise RuntimeError("adapter boom")


def raising(exc: Exception):
    def _raise(*_args, **_kwargs):
        raise exc

    return _raise


def test_signed_opened_job_notifies_after_apply(monkeypatch):
    settings = webhook_settings()
    payload = opened_payload()
    raw = json.dumps(payload).encode("utf-8")
    assert verify_signature(raw, sign(raw), SECRET) is True
    job = build_job(settings, payload, event="issues", delivery_id="delivery-1")

    store = NotifStore(latest=None, runs={RUN_KEY: run_snapshot(run_key=RUN_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)
    apply = FakeApply(actions=[applied_action()])
    pipeline = make_pipeline(monkeypatch, apply=apply, notifications=notifier)

    pipeline(job)

    assert len(sender.payloads) == 1
    rendered = json.dumps(sender.payloads[0], ensure_ascii=False)
    # The message reflects the actual persisted apply outcome.
    assert "GitHub apply outcome: 1 applied." in rendered
    assert f"https://github.com/{SANDBOX}/issues/42" in rendered
    mark = next(call for call in store.calls if call[0] == "mark_run")
    assert mark[1:] == (TENANT, RUN_KEY, ISSUE_KEY, "issue_opened", "delivery:delivery-1", None)


def test_rejected_duplicate_and_upsert_only_jobs_are_silent(monkeypatch):
    notifier = RecordingNotifier()
    pipeline = make_pipeline(monkeypatch, notifications=notifier)
    worker = WebhookWorker(pipeline)
    app = create_app(
        webhook_settings(),
        fixture_store=FixtureStore.default(),
        webhook_worker=worker,
        notifications=notifier,
    )

    with TestClient(app) as client:
        assert post(client, opened_payload(), signature="sha256=deadbeef").status_code == 401
        assert post(client, {"zen": "x"}, event="ping").status_code == 202  # unsupported event
        assert post(client, opened_payload(sender={"login": BOT})).status_code == 202  # bot sender
        assert (
            post(client, opened_payload(repository={"full_name": "other/repo"})).status_code == 202
        )  # out of sandbox
        assert notifier.begins == []

        # A verified, admitted job notifies exactly once.
        assert post(client, opened_payload(), delivery="d1").status_code == 202
        wait_until(lambda: len(notifier.completes) == 1)
        assert len(notifier.begins) == 1

        # A duplicate delivery is acknowledged but never re-run.
        assert post(client, opened_payload(), delivery="d1").status_code == 202

        # An upsert-only edit refreshes the issue but prepares no notification.
        assert worker.enqueue(make_job(action="edited", delivery_id="edit-1", triage=False))
        wait_until(lambda: len(pipeline._store.calls) == 2)
        assert len(notifier.begins) == 1


def test_upsert_only_edit_does_not_prepare_a_notification(monkeypatch):
    notifier = RecordingNotifier()
    apply = FakeApply()
    pipeline = make_pipeline(monkeypatch, apply=apply, notifications=notifier)

    pipeline(make_job(action="edited", delivery_id="edit-only", triage=False))

    assert notifier.begins == [] and notifier.completes == [] and notifier.failures == []
    assert pipeline._apply_service.calls == []


def test_processing_stage_failures_preserve_exception(monkeypatch):
    # Ingest/embedding failure: the trusted issue identity exists before ingest.
    notifier = RecordingNotifier()
    boom = RuntimeError("embedding down")
    pipeline = make_pipeline(monkeypatch, notifications=notifier)
    monkeypatch.setattr("api.webhook_pipeline.incoming_issue_vector", raising(boom))
    with pytest.raises(RuntimeError) as raised:
        pipeline(make_job(delivery_id="ingest"))
    assert raised.value is boom
    assert [(stage, run_key) for _, stage, run_key in notifier.failures] == [("ingest", None)]
    assert pipeline._store.calls == [] and pipeline._apply_service.calls == []

    # Triage failure: apply is never reached and no run key is persisted.
    notifier = RecordingNotifier()
    triage_boom = RuntimeError("agent down")
    store, apply = FakeStore(), FakeApply()
    pipeline = make_pipeline(
        monkeypatch,
        store=store,
        apply=apply,
        triage=make_triage(error=triage_boom),
        notifications=notifier,
    )
    with pytest.raises(RuntimeError) as raised:
        pipeline(make_job(delivery_id="triage"))
    assert raised.value is triage_boom
    assert [(stage, run_key) for _, stage, run_key in notifier.failures] == [("triage", None)]
    assert len(store.calls) == 1 and apply.calls == []

    # Apply failure: the persisted run key is reported and never released early.
    notifier = RecordingNotifier()
    apply_boom = RuntimeError("apply down")
    store, apply = FakeStore(), FakeApply(error=apply_boom)
    pipeline = make_pipeline(monkeypatch, store=store, apply=apply, notifications=notifier)
    with pytest.raises(RuntimeError) as raised:
        pipeline(make_job(delivery_id="apply"))
    assert raised.value is apply_boom
    assert [(stage, run_key) for _, stage, run_key in notifier.failures] == [("apply", RUN_KEY)]
    assert len(store.calls) == 1 and apply.calls == [(TENANT, RUN_KEY, False)]


def test_issue_edit_changed_vs_unchanged(monkeypatch):
    parent = run_snapshot(run_key=PARENT_KEY, label="urgent")
    store = NotifStore(
        latest=parent, runs={RUN_KEY: run_snapshot(run_key=RUN_KEY, label="blocker")}
    )
    sender = RecordingSender()
    pipeline = make_pipeline(monkeypatch, notifications=coordinator(store, sender))

    # A meaningful change sends a standalone follow-up.
    pipeline(make_job(action="edited", delivery_id="edit-changed"))
    assert len(sender.payloads) == 1
    assert "Changed since the parent run" in json.dumps(sender.payloads[0])

    # An unchanged recommendation is silent.
    store.runs = {RUN_KEY: run_snapshot(run_key=RUN_KEY, label="urgent")}
    pipeline(make_job(action="edited", delivery_id="edit-same"))
    assert len(sender.payloads) == 1

    # No proven parent treats the edit as the issue's initial triage (no parent
    # link, no before/after comparison), labeled by its actual cause.
    store.latest = None
    store.runs = {RUN_KEY: run_snapshot(run_key=RUN_KEY)}
    pipeline(make_job(action="edited", delivery_id="edit-initial"))
    assert len(sender.payloads) == 2
    initial = json.dumps(sender.payloads[1])
    assert "Issue edited" in initial
    assert "Changed since the parent run" not in initial


def test_partial_refusal_and_fallback_render_truthfully(monkeypatch):
    store = NotifStore(latest=None, runs={RUN_KEY: run_snapshot(run_key=RUN_KEY)})
    sender = RecordingSender()
    apply = FakeApply(
        actions=[
            applied_action(),
            schemas.ActionResult(
                action_key="action:2", kind="comment", value="comment", status="refused"
            ),
        ]
    )
    pipeline = make_pipeline(monkeypatch, apply=apply, notifications=coordinator(store, sender))

    pipeline(make_job(delivery_id="refused"))
    rendered = json.dumps(sender.payloads[0])
    assert "GitHub apply outcome: 1 applied, 1 refused." in rendered
    assert "full success" not in rendered.lower()

    # A completed fallback triage is labeled as degraded, never as a failure.
    store.runs = {RUN_KEY: fallback_snapshot(RUN_KEY)}
    pipeline(make_job(delivery_id="fallback"))
    fallback = json.dumps(sender.payloads[1])
    assert "Fallback (degraded) triage" in fallback
    assert "Processing failed" not in fallback


def test_injected_notifier_exception_does_not_fail_job(monkeypatch):
    # A throwing adapter is isolated at every call site: the core job still runs
    # and any core exception still escapes unchanged.
    for method in ("begin", "complete"):
        notifier = RecordingNotifier(raise_on={method})
        apply = FakeApply(actions=[applied_action()])
        pipeline = make_pipeline(monkeypatch, apply=apply, notifications=notifier)

        pipeline(make_job(delivery_id=f"job-{method}"))  # must not raise

        assert notifier.begins
        assert apply.calls == [(TENANT, RUN_KEY, False)]

    notifier = RecordingNotifier(raise_on={"failed"})
    apply_boom = RuntimeError("apply down")
    pipeline = make_pipeline(monkeypatch, apply=FakeApply(error=apply_boom), notifications=notifier)
    with pytest.raises(RuntimeError) as raised:
        pipeline(make_job(delivery_id="job-failed"))
    assert raised.value is apply_boom
    assert [stage for _, stage, _ in notifier.failures] == ["apply"]


# --- composed acceptance against a real, disposable graph --------------------
#
# The tests above drive the coordinator and pipeline through recording doubles.
# These compose the real ``create_app`` wiring instead: a real worker, the real
# ``NotificationStore``/``WebhookStore`` and ``SlackNotifications``, the real
# signed webhook route, the real feedback writes, and a stub agent/apply service.
# The only network is a MockTransport Slack client and a MockTransport GitHub
# writer; the graph is a dedicated tenant that is deleted afterwards. A skip is
# unverified, not acceptance.

FLOW_TENANT = "slack_flow"
FLOW_ISSUE_KEY = f"issue:{SANDBOX}#42"
FLOW_PARENT = "run:00000000-0000-4000-8000-0000000000b1"
FLOW_GITHUB_RUN = "run:00000000-0000-4000-8000-0000000000b2"
FLOW_UI_RUN = "run:00000000-0000-4000-8000-0000000000b3"
FLOW_MODULE = f"module:{SANDBOX}/src/graph"
FLOW_LOGIN = "alice"
FLOW_PARENT_ASSIGN = "action:flow-parent-assign"
FLOW_GITHUB_ASSIGN = "action:flow-github-assign"
FLOW_UI_ASSIGN = "action:flow-ui-assign"
FLOW_SLACK_URL = "https://hooks.slack.com/services/TFLOW/BFLOW/tokenflow"
FLOW_COMMENT_ID = 777


@pytest.fixture
def flow_graph(monkeypatch):
    """The dedicated ``slack_flow`` tenant graph; a skip is unverified, not acceptance."""
    # ``memory.record_feedback`` validates the tenant against TENANTS before touching
    # the graph, so the dedicated tenant must be registered for this test.
    monkeypatch.setenv("TENANTS", FLOW_TENANT)
    try:
        tenant_graph = graph(FLOW_TENANT)
        tenant_graph.query("RETURN 1")  # a write query creates the dedicated graph
    except Exception as exc:  # noqa: BLE001 - no server, wrong password: nothing here can run
        pytest.skip(f"FalkorDB not reachable: {exc}")
    tenant_graph.query("MATCH (n) DETACH DELETE n")
    try:
        yield tenant_graph
    finally:
        tenant_graph.delete()  # remove only the dedicated test graph


def flow_settings() -> Settings:
    return Settings(
        tenants=(FLOW_TENANT,),
        api_mode="live",
        sandbox_repo=SANDBOX,
        bot_login=BOT,
        webhook_secret=SECRET,
        public_base_url=BASE_URL,
        slack_webhook_url=FLOW_SLACK_URL,
    )


def comment_payload(**overrides) -> dict:
    payload = {
        "action": "created",
        "repository": {"full_name": SANDBOX},
        "sender": {"login": "octocat"},
        "issue": {
            "number": 42,
            "title": "Graph traversal is slow",
            "body": "It hangs on large graphs.",
            "state": "open",
            "created_at": "2026-10-16T00:00:00Z",
            "user": {"login": "octocat", "html_url": "https://github.com/octocat", "type": "User"},
        },
        "comment": {"id": FLOW_COMMENT_ID, "body": "/keystone reject alice she is on leave"},
    }
    payload.update(overrides)
    return payload


class FlowSlack:
    """The MockTransport Slack transport the composed app delivers through."""

    def __init__(self):
        self.payloads: list[dict] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request):
        self.payloads.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    def close(self):
        self.client.close()


class FlowGitHub:
    """The MockTransport sandbox writer hub: collaborator checks and comment posts/lookups."""

    def __init__(self):
        self.comments: list[str] = []
        self.client = httpx.Client(
            transport=httpx.MockTransport(self._handle),
            headers={"Authorization": "Bearer offline-test-token"},
        )
        self.writer = SandboxGitHub(SANDBOX, self.client)

    def _handle(self, request):
        path = request.url.path
        if request.method == "GET" and "/collaborators/" in path:
            return httpx.Response(204)
        if path.endswith("/comments"):
            if request.method == "POST":
                self.comments.append(json.loads(request.content)["body"])
                return httpx.Response(201, json={"id": len(self.comments)})
            return httpx.Response(200, json=[{"body": body} for body in self.comments])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    def close(self):
        self.client.close()


class FlowApply:
    """A stub apply service bound to the MockTransport writer, with fixed action outcomes."""

    def __init__(self, writer, *, statuses=(("label", "applied"),)):
        self.writer = writer
        self.public_base_url = BASE_URL
        self.statuses = list(statuses)
        self.calls: list[tuple] = []

    def apply_triage(self, tenant, run_key, dry_run=True):
        self.calls.append((tenant, run_key, bool(dry_run)))
        actions = [
            schemas.ActionResult(
                action_key=f"{run_key}:{index}", kind=kind, value=kind, status=status
            )
            for index, (kind, status) in enumerate(self.statuses)
        ]
        return schemas.ApplyResult(run_key=run_key, dry_run=dry_run, actions=actions)


def flow_triage_result(run_key: str, label: str) -> schemas.TriageResult:
    return schemas.TriageResult(
        run_key=run_key,
        labels=[schemas.Claim(text=label, citations=[FLOW_MODULE])],
        reviewers=[schemas.Claim(text=FLOW_LOGIN, citations=[FLOW_MODULE])],
        risks=[],
        decisions=[],
        comment_markdown="composed comment",
        trace=[],
    )


def write_flow_run(tenant_graph, run_key, *, at, label, action_key):
    """Persist one run exactly as a live triage would, before its notification completes."""
    triage = flow_triage_result(run_key, label)
    tenant_graph.query(
        "MATCH (i:Issue {key: $issue}) "
        "MERGE (p:Person:Node {key: $person}) SET p.login = $login "
        "CREATE (run:AgentRun:Node {key: $run, at: $at, issue_key: $issue, summary: $summary, "
        "fallback: false, model: 'm', llm_calls: 0, ms: 1, triage: $triage}) "
        "CREATE (run)-[:HANDLED]->(i) "
        "CREATE (a:Action:Node {key: $action, kind: 'assign', value: $login, module: $module, "
        "status: 'proposed'}) "
        "CREATE (run)-[:PROPOSED]->(a) "
        "CREATE (a)-[:TARGETS]->(p) "
        "CREATE (l:Action:Node {key: $label_action, kind: 'label', value: $label, "
        "status: 'proposed'}) "
        "CREATE (run)-[:PROPOSED]->(l)",
        {
            "issue": FLOW_ISSUE_KEY,
            "run": run_key,
            "at": at,
            "summary": "A short summary.",
            "triage": triage.model_dump_json(),
            "action": action_key,
            "label_action": f"{action_key}:label",
            "module": FLOW_MODULE,
            "person": f"person:{FLOW_LOGIN}",
            "login": FLOW_LOGIN,
            "label": label,
        },
    )


class FlowAgent:
    """A triage seam that persists each run into the real graph, in call order."""

    def __init__(self, tenant_graph, plan):
        self._graph = tenant_graph
        self._plan = list(plan)  # [(run_key, label, action_key, at), ...]
        self.calls: list[tuple] = []

    async def __call__(self, tenant, issue_key):
        run_key, label, action_key, at = self._plan.pop(0)
        self.calls.append((tenant, issue_key, run_key))
        write_flow_run(self._graph, run_key, at=at, label=label, action_key=action_key)
        return flow_triage_result(run_key, label)


def compose_flow_app(tenant_graph, monkeypatch, github, slack, plan, *, statuses=None):
    """Compose the real app around stub agent/apply and the MockTransport transports."""
    # The vector itself is not under test; the real encoder would load the model.
    monkeypatch.setattr("api.webhook_pipeline.incoming_issue_vector", lambda job: [0.0] * 384)
    agent_triage = FlowAgent(tenant_graph, plan)
    apply = FlowApply(github.writer, statuses=statuses or (("label", "applied"),))
    app = create_app(
        flow_settings(),
        agent_triage=agent_triage,
        apply_service=apply,
        record_feedback=memory.record_feedback,
        slack_client=slack.client,
    )
    return app, agent_triage, apply


def _flow_lineage(tenant_graph, run_key):
    rows = tenant_graph.ro_query(
        "MATCH (r:AgentRun {key: $key}) RETURN r.notify_root, r.notify_parent, r.notify_event, "
        "r.notify_cause, r.notify_at",
        {"key": run_key},
    ).result_set
    return rows[0] if rows else None


def test_signed_issue_to_feedback_round_trip(flow_graph, monkeypatch):
    """One signed issue, then authorized GitHub feedback and a UI Re-triage, notify truthfully."""
    slack = FlowSlack()
    github = FlowGitHub()
    plan = [
        (FLOW_PARENT, "urgent", FLOW_PARENT_ASSIGN, 100),
        (FLOW_GITHUB_RUN, "blocker", FLOW_GITHUB_ASSIGN, 200),
    ]
    app, _, apply = compose_flow_app(
        flow_graph,
        monkeypatch,
        github,
        slack,
        plan,
        statuses=(("label", "applied"), ("comment", "refused")),
    )
    try:
        with TestClient(app) as client:
            # Phase 1: a signed opened job triages once and sends the initial message.
            assert post(client, opened_payload(), delivery="flow-open").status_code == 202
            wait_until(lambda: len(slack.payloads) == 1)

            initial = json.dumps(slack.payloads[0], ensure_ascii=False)
            assert "New issue triage" in initial
            assert "GitHub apply outcome: 1 applied, 1 refused." in initial
            assert f"https://github.com/{SANDBOX}/issues/42" in initial
            assert f"run={FLOW_PARENT[len('run:') :]}" in initial
            assert "Follow-up to:" not in initial

            # Phase 2: an authorized collaborator reject re-triages and reports the change.
            assert (
                post(
                    client, comment_payload(), event="issue_comment", delivery="flow-comment"
                ).status_code
                == 202
            )
            wait_until(lambda: len(slack.payloads) == 2)

            replayed = json.dumps(slack.payloads[1], ensure_ascii=False)
            assert "GitHub feedback" in replayed
            assert "Changed since the parent run" in replayed
            assert f"Follow-up to: {FLOW_PARENT}" in replayed
            assert "GitHub apply outcome: 1 applied, 1 refused." in replayed
            assert github.comments  # the confirmation comment was posted

            # Phase 3: saved UI feedback plus an explicit Re-triage of the same issue.
            parent_uuid = FLOW_PARENT[len("run:") :]
            saved = client.post(
                f"/tenants/{FLOW_TENANT}/runs/{parent_uuid}/feedback",
                json={"action_key": FLOW_PARENT_ASSIGN, "verdict": "reject", "reason": "on leave"},
            )
            assert saved.status_code == 200
            feedback_key = saved.json()["feedback_key"]

            async def ui_run_triage(tenant, request):
                write_flow_run(
                    flow_graph, FLOW_UI_RUN, at=300, label="critical", action_key=FLOW_UI_ASSIGN
                )
                return flow_triage_result(FLOW_UI_RUN, "critical")

            monkeypatch.setattr(agent, "run_triage", ui_run_triage)
            triaged = client.post(
                f"/tenants/{FLOW_TENANT}/triage",
                json={
                    "issue_key": FLOW_ISSUE_KEY,
                    "feedback_parent_run": parent_uuid,
                    "feedback_key": feedback_key,
                },
            )
            assert triaged.status_code == 200
            assert triaged.json()["run_key"] == FLOW_UI_RUN
            wait_until(lambda: len(slack.payloads) == 3)

            followup = json.dumps(slack.payloads[2], ensure_ascii=False)
            assert "UI feedback (read-only)" in followup
            assert "No GitHub actions were applied by this re-triage." in followup
            assert "Changed since the parent run" in followup
            assert f"run={FLOW_UI_RUN[len('run:') :]}" in followup

        # The real apply stub drove the composed pipeline (never a dry run).
        assert apply.calls == [
            (FLOW_TENANT, FLOW_PARENT, False),
            (FLOW_TENANT, FLOW_GITHUB_RUN, False),
        ]

        # Persisted origin: the lineage is on the graph, not in process memory.
        root, parent, event, cause, at = _flow_lineage(flow_graph, FLOW_PARENT)
        assert (root, parent, cause, event) == (
            FLOW_PARENT,
            None,
            "issue_opened",
            "delivery:flow-open",
        )
        assert at is not None
        root, parent, event, cause, _ = _flow_lineage(flow_graph, FLOW_GITHUB_RUN)
        assert (root, parent, cause, event) == (
            FLOW_PARENT,
            FLOW_PARENT,
            "github_feedback",
            f"comment:{FLOW_COMMENT_ID}",
        )
        root, parent, event, cause, _ = _flow_lineage(flow_graph, FLOW_UI_RUN)
        assert (root, parent, cause) == (FLOW_PARENT, FLOW_PARENT, "ui_feedback")
        assert event == f"ui:{FLOW_PARENT}:{feedback_key}"
    finally:
        slack.close()
        github.close()


def test_restart_restores_lineage_not_delivery_guarantees(flow_graph, monkeypatch):
    """A new app/store over the same graph still finds the parent; transport dedup is
    not promised."""
    first_slack = FlowSlack()
    github = FlowGitHub()
    app, _, _ = compose_flow_app(
        flow_graph,
        monkeypatch,
        github,
        first_slack,
        [(FLOW_PARENT, "urgent", FLOW_PARENT_ASSIGN, 100)],
    )
    try:
        with TestClient(app) as client:
            assert post(client, opened_payload(), delivery="restart-open").status_code == 202
            wait_until(lambda: len(first_slack.payloads) == 1)
        assert "New issue triage" in json.dumps(first_slack.payloads[0])

        # "Restart": a brand-new app composition over the same graph. The old process's
        # deliveries, cooldown and caches are gone; the lineage lives on the node.
        restarted_slack = FlowSlack()
        restarted_app, _, _ = compose_flow_app(
            flow_graph,
            monkeypatch,
            github,
            restarted_slack,
            [(FLOW_GITHUB_RUN, "blocker", FLOW_GITHUB_ASSIGN, 200)],
        )
        notifications = restarted_app.state.notifications
        assert notifications is not None and notifications._sender is not None

        attempt = notifications.begin_issue(
            tenant=FLOW_TENANT,
            issue_key=FLOW_ISSUE_KEY,
            delivery_id="restart-open",
            action="edited",
            title="Graph traversal is slow",
            replay_of=None,
        )
        # The same issue is a follow-up again, proved from the persisted origin.
        assert attempt is not None and attempt.parent is not None
        assert attempt.parent.data.run.run_key == FLOW_PARENT
        assert attempt.cause == "issue_edited"

        write_flow_run(
            flow_graph, FLOW_GITHUB_RUN, at=200, label="blocker", action_key=FLOW_GITHUB_ASSIGN
        )
        notifications.complete(attempt, run_key=FLOW_GITHUB_RUN, applied=None)

        # The fresh process sends for a delivery the previous process already handled:
        # cross-process delivery deduplication is explicitly not guaranteed.
        assert len(restarted_slack.payloads) == 1
        assert "Changed since the parent run" in json.dumps(restarted_slack.payloads[0])
        restarted_slack.close()
    finally:
        first_slack.close()
        github.close()


class FlakyApply:
    """Fails the first apply, then succeeds, to force one failure and one recovery."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.fail = True

    def apply_triage(self, tenant, run_key, dry_run=True):
        self.calls.append((tenant, run_key, bool(dry_run)))
        if self.fail:
            self.fail = False
            raise RuntimeError("apply down")
        return schemas.ApplyResult(run_key=run_key, dry_run=dry_run, actions=[applied_action()])


def test_core_recovery_after_apply_failure(monkeypatch):
    """A failed apply sends once; its redelivery recovers even with an unchanged fingerprint."""
    parent = run_snapshot(run_key=PARENT_KEY)
    store = NotifStore(latest=parent, runs={RUN_KEY: run_snapshot(run_key=RUN_KEY)})
    sender = RecordingSender()
    apply = FlakyApply()
    pipeline = make_pipeline(monkeypatch, apply=apply, notifications=coordinator(store, sender))
    job = make_job(action="edited", delivery_id="recover-1")

    with pytest.raises(RuntimeError):
        pipeline(job)
    assert len(sender.payloads) == 1
    assert STAGE_FAILURE_COPY["apply"] in json.dumps(sender.payloads[0])

    # The redelivery reuses the reserved run; because the event already failed, the
    # recovery is sent even though the recommendations are unchanged.
    pipeline(job)
    assert len(sender.payloads) == 2
    recovery = json.dumps(sender.payloads[1], ensure_ascii=False)
    assert RECOVERY_NOTE in recovery
    assert "Changed since the parent run" not in recovery
    assert len(apply.calls) == 2

    # The recovered attempt is recorded, so a third delivery is silent.
    pipeline(job)
    assert len(sender.payloads) == 2
