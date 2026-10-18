"""Pending-run lifecycle is shared by ordinary and reject triage."""
import json

import pytest

from api.actions import ActionStoreError
from api.notification_messages import RECOVERY_NOTE
from api.notifications import SlackNotifications
from test_comment_commands import make_lane, recorded_assign, run_data
from test_comment_commands import make_job as comment_job
from test_notifications import FakeStore as NotifStore
from test_notifications import RecordingSender, run_snapshot
from test_webhook_pipeline import make_pipeline, FakeStore
from test_webhook_pipeline import make_job as issue_job
from webhook_retry_support import RetryHarness

HARNESS_RUN = "run:00000000-0000-4000-8000-000000000001"
OTHER_RUN = "run:00000000-0000-4000-8000-000000000099"
BASE_URL = "https://keystone.example"


@pytest.mark.parametrize("command", [False, True], ids=["ordinary", "reject"])
def test_pending_runs_isolate_jobs_and_failed_triage_is_retryable(monkeypatch, command):
    harness = RetryHarness()
    if command:
        runner = make_lane(run=run_data(recorded_assign("alice")), writer=harness.writer,
                           triage=harness.triage, apply=harness.service)
        job = comment_job
    else:
        store = FakeStore()
        # Model the trusted adapter's canonical issue-key selection.
        store.upsert_issue = lambda tenant, job, vector, replay: (
            f"issue:acme/keystone-sandbox#{job.issue_number}"
        )
        runner = make_pipeline(monkeypatch, store=store, triage=harness.triage,
                               apply=harness.service)
        job = issue_job
    try:
        harness.fail_triage = True
        with pytest.raises(RuntimeError, match="triage failed"):
            runner(job())
        assert harness.runs == []
        with pytest.raises(ActionStoreError):
            runner(job())
        original_run = harness.runs[0][2]
        # Other identities while the original is pending must not reuse its run.
        runner(job(delivery_id="other", comment_id=556 if command else None))
        runner(job(issue_number=43))  # same delivery/comment identity, different issue
        runner(job(delivery_id="redelivered" if command else "d1"))
        assert len(harness.runs) == 3
        assert harness.record_attempts[-1] == ("falkordb", original_run)
        assert len([body for _, body in harness.comments if original_run in body]) == 1
    finally:
        harness.client.close()


def test_apply_retry_preserves_run_and_notification_parent(monkeypatch):
    """A write-then-raise apply failure keeps one run and one reserved parent.

    The retry reapplies the exact persisted run (no second triage, no duplicate
    comment) and the reserved parent is unchanged even when a competing eligible
    completion lands in between. Recovery after the recorded core failure sends a
    second, distinct message.
    """
    harness = RetryHarness()
    notif = NotifStore(latest=None, runs={HARNESS_RUN: run_snapshot(run_key=HARNESS_RUN)})
    sender = RecordingSender()
    notifier = SlackNotifications(notif, sender, public_base_url=BASE_URL)
    runner = make_pipeline(
        monkeypatch, store=FakeStore(), triage=harness.triage, apply=harness.service,
        notifications=notifier,
    )
    try:
        with pytest.raises(ActionStoreError):
            runner(issue_job())
        assert len(harness.runs) == 1
        assert len(sender.payloads) == 1  # one apply-failure message
        assert notif.kinds().count("latest_eligible") == 1

        # A competing eligible completion must not move the reserved parent.
        notif.latest = run_snapshot(run_key=OTHER_RUN)
        runner(issue_job())

        assert len(harness.runs) == 1  # same persisted run, never a second triage
        assert notif.kinds().count("latest_eligible") == 1  # reservation held
        assert len(harness.comments) == 1  # the per-run comment marker prevented a repost
        assert len(sender.payloads) == 2  # one failure plus one recovery
        assert RECOVERY_NOTE in json.dumps(sender.payloads[1])
    finally:
        harness.client.close()


def test_pending_run_cache_evicts_oldest_and_releases_success():
    from api.webhook_retry import PendingTriageRuns

    retained = PendingTriageRuns(limit=2)
    calls = []

    async def triage(tenant, issue):
        from test_webhook_pipeline import triage_result
        calls.append((tenant, issue))
        return triage_result(run_key=f"run:{len(calls)}")

    a = ("trusted", "issue:a", "delivery-a")
    b = ("trusted", "issue:b", "delivery-b")
    c = ("trusted", "issue:c", "delivery-c")
    assert retained.run_key(a, triage) == "run:1"
    assert retained.run_key(b, triage) == "run:2"
    assert retained.run_key(a, triage) == "run:1"  # reads do not extend FIFO retention
    assert retained.run_key(c, triage) == "run:3"
    assert retained.run_key(b, triage) == "run:2"
    retained.applied(b)
    assert retained.run_key(b, triage) == "run:4"  # success releases its slot
    assert retained.run_key(a, triage) == "run:5"  # oldest pending run was evicted
    assert retained.run_key(("other", "issue:a", "delivery-a"), triage) == "run:6"
