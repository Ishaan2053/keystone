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

import pytest
from fastapi.testclient import TestClient

from api import schemas
from api.config import Settings
from api.fixture_store import FixtureStore
from api.main import create_app
from api.notification_messages import RunSnapshot
from api.notifications import SlackNotifications
from api.webhooks import WebhookWorker, build_job, verify_signature

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
    return schemas.ActionResult(action_key="action:1", kind="label", value="graph", status="applied")


def fallback_snapshot(run_key: str) -> RunSnapshot:
    base = run_snapshot(run_key=run_key)
    data = base.data.model_copy(
        update={"run": base.data.run.model_copy(update={"fallback": True})}
    )
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
        assert post(
            client, opened_payload(repository={"full_name": "other/repo"})
        ).status_code == 202  # out of sandbox
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
        monkeypatch, store=store, apply=apply, triage=make_triage(error=triage_boom),
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
    pipeline = make_pipeline(
        monkeypatch, store=store, apply=apply, notifications=notifier
    )
    with pytest.raises(RuntimeError) as raised:
        pipeline(make_job(delivery_id="apply"))
    assert raised.value is apply_boom
    assert [(stage, run_key) for _, stage, run_key in notifier.failures] == [("apply", RUN_KEY)]
    assert len(store.calls) == 1 and apply.calls == [(TENANT, RUN_KEY, False)]


def test_issue_edit_changed_vs_unchanged(monkeypatch):
    parent = run_snapshot(run_key=PARENT_KEY, label="urgent")
    store = NotifStore(latest=parent, runs={RUN_KEY: run_snapshot(run_key=RUN_KEY, label="blocker")})
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
    pipeline = make_pipeline(
        monkeypatch, apply=FakeApply(error=apply_boom), notifications=notifier
    )
    with pytest.raises(RuntimeError) as raised:
        pipeline(make_job(delivery_id="job-failed"))
    assert raised.value is apply_boom
    assert [stage for _, stage, _ in notifier.failures] == ["apply"]
