"""B7 Task 3: the composed webhook triage -> apply pipeline.

One verified job runs, on the dedicated worker thread, through the real
persisted pipeline in this exact order:

    incoming_issue_vector(job)
    parse_replay(job.body, allowed_repos)
    store.upsert_issue(tenant, job, vector, replay)
    asyncio.run(triage(tenant, issue_key))
    apply_service.apply_triage(tenant, result.run_key, dry_run=False)

A failing upsert never reaches triage; a failing triage never reaches apply; a
persistence failure raises so the worker records no completion. Partial
``refused`` actions are logged and returned normally (they stay visible in the
persisted results). The tenant is fixed at construction and never read from the
job, the replay URL or any request input.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

from api import schemas
from api.actions import ActionStoreError
from api.webhook_pipeline import WebhookPipeline
from api.webhooks import WebhookJob, WebhookWorker

TENANT = "falkordb"
TRUSTED_TENANT = "trusted"
ISSUE_KEY = "issue:acme/keystone-sandbox#42"
RUN_KEY = "run:00000000-0000-4000-8000-000000000001"
VECTOR = [0.0] * 384


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


def triage_result(run_key: str = RUN_KEY) -> schemas.TriageResult:
    return schemas.TriageResult(
        run_key=run_key,
        labels=[],
        reviewers=[],
        risks=[],
        decisions=[],
        comment_markdown="Keystone triaged this issue.",
        trace=[],
    )


class FakeStore:
    def __init__(self, *, key: str = ISSUE_KEY, error: Exception | None = None):
        self.calls: list[dict] = []
        self.key = key
        self.error = error

    def upsert_issue(self, tenant, job, vector, replay):
        self.calls.append({"tenant": tenant, "job": job, "vector": vector, "replay": replay})
        if self.error is not None:
            raise self.error
        return self.key


def make_triage(*, error: Exception | None = None, calls: list | None = None):
    calls = [] if calls is None else calls

    async def triage(tenant: str, issue_key: str) -> schemas.TriageResult:
        calls.append((tenant, issue_key))
        if error is not None:
            raise error
        return triage_result()

    return triage


class FakeApply:
    def __init__(self, *, actions: list | None = None, error: Exception | None = None):
        self.calls: list[tuple] = []
        self.actions = actions or []
        self.error = error

    def apply_triage(self, tenant, run_key, dry_run=True):
        self.calls.append((tenant, run_key, bool(dry_run)))
        if self.error is not None:
            raise self.error
        return schemas.ApplyResult(run_key=run_key, dry_run=dry_run, actions=self.actions)


def make_pipeline(monkeypatch, *, store=None, triage=None, apply=None, tenant=TENANT):
    # ``incoming_issue_vector`` would load the real embedding model; the vector
    # value itself is not under test here.
    monkeypatch.setattr("api.webhook_pipeline.incoming_issue_vector", lambda job: list(VECTOR))
    return WebhookPipeline(
        store=store if store is not None else FakeStore(),
        triage=triage if triage is not None else make_triage(),
        apply_service=apply if apply is not None else FakeApply(),
        tenant=tenant,
        allowed_repos=set(),
    )


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not met in time")


def test_runner_runs_upsert_then_triage_then_live_apply(monkeypatch):
    store = FakeStore()
    triage_calls: list = []
    apply = FakeApply()
    pipeline = make_pipeline(
        monkeypatch, store=store, triage=make_triage(calls=triage_calls), apply=apply
    )

    job = make_job()
    pipeline(job)

    (upsert,) = store.calls
    assert upsert["tenant"] == TENANT and upsert["job"] is job
    assert upsert["vector"] == VECTOR
    # R8: with no allowed upstream repos, replay resolution fails closed.
    assert upsert["replay"] is None
    assert triage_calls == [(TENANT, ISSUE_KEY)]
    # The run key from the persisted triage drives a non-dry-run apply.
    assert apply.calls == [(TENANT, RUN_KEY, False)]


def test_upsert_failure_skips_triage_and_apply(monkeypatch):
    store = FakeStore(error=RuntimeError("graph down"))
    triage_calls: list = []
    apply = FakeApply()
    pipeline = make_pipeline(
        monkeypatch, store=store, triage=make_triage(calls=triage_calls), apply=apply
    )

    with pytest.raises(RuntimeError):
        pipeline(make_job())

    assert triage_calls == []
    assert apply.calls == []


def test_triage_failure_skips_apply(monkeypatch):
    store = FakeStore()
    apply = FakeApply()
    pipeline = make_pipeline(
        monkeypatch, store=store, triage=make_triage(error=RuntimeError("agent down")), apply=apply
    )

    with pytest.raises(RuntimeError):
        pipeline(make_job())

    assert len(store.calls) == 1
    assert apply.calls == []


def test_apply_persistence_failure_propagates(monkeypatch):
    apply = FakeApply(error=ActionStoreError("results could not be recorded"))
    pipeline = make_pipeline(monkeypatch, apply=apply)

    # Propagating (not swallowing) lets the worker record no completion.
    with pytest.raises(ActionStoreError):
        pipeline(make_job())

    assert apply.calls == [(TENANT, RUN_KEY, False)]


def test_partial_refused_actions_are_logged_and_return_normally(monkeypatch, caplog):
    actions = [
        schemas.ActionResult(
            action_key="action:applied", kind="label", value="graph", status="applied", note="ok"
        ),
        schemas.ActionResult(
            action_key="action:refused",
            kind="comment",
            value="comment",
            status="refused",
            note="refused: sandbox comment failed",
        ),
    ]
    apply = FakeApply(actions=actions)
    pipeline = make_pipeline(monkeypatch, apply=apply)

    with caplog.at_level(logging.WARNING):
        pipeline(make_job())  # must not raise

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("action:refused" in message for message in warnings)
    # A refusal is not reported as a full success.
    assert all("action:applied" not in message for message in warnings)
    assert apply.calls == [(TENANT, RUN_KEY, False)]


def test_tenant_comes_from_construction_not_the_job(monkeypatch):
    store = FakeStore()
    triage_calls: list = []
    apply = FakeApply()
    pipeline = make_pipeline(
        monkeypatch,
        store=store,
        triage=make_triage(calls=triage_calls),
        apply=apply,
        tenant=TRUSTED_TENANT,
    )

    pipeline(make_job(tenant="attacker-controlled"))

    assert store.calls[0]["tenant"] == TRUSTED_TENANT
    assert triage_calls == [(TRUSTED_TENANT, ISSUE_KEY)]
    assert apply.calls == [(TRUSTED_TENANT, RUN_KEY, False)]


def test_pipeline_is_a_plain_sync_worker_runner(monkeypatch):
    store = FakeStore()
    apply = FakeApply()
    pipeline = make_pipeline(monkeypatch, store=store, apply=apply)

    worker = WebhookWorker(pipeline)
    worker.start()
    try:
        assert worker.enqueue(make_job()) is True
        wait_until(lambda: apply.calls == [(TENANT, RUN_KEY, False)])
    finally:
        worker.close()

    assert len(store.calls) == 1
    assert apply.calls == [(TENANT, RUN_KEY, False)]
