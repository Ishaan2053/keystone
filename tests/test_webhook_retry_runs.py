"""Pending-run lifecycle is shared by ordinary and reject triage."""
import pytest

from api.actions import ActionStoreError
from test_comment_commands import make_lane, recorded_assign, run_data
from test_comment_commands import make_job as comment_job
from test_webhook_pipeline import make_pipeline, FakeStore
from test_webhook_pipeline import make_job as issue_job
from webhook_retry_support import RetryHarness


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
