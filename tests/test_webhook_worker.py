"""B7 Task 1: the serialized webhook worker.

One dedicated thread pulls from a bounded FIFO queue so local embedding, graph
writes, the agent and the sandbox writer never run on the FastAPI event loop. A
failing job is logged and dropped; the thread must survive to run the next one.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from api.webhooks import (
    COMPLETED_DELIVERY_LIMIT,
    ISSUE_COOLDOWN_SECONDS,
    WebhookJob,
    WebhookWorker,
)


class FakeClock:
    """A manually advanced monotonic clock for the admission cooldown."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_job(
    delivery_id: str = "d1",
    issue_number: int = 1,
    event: str = "issues",
    comment_id: int | None = None,
    action: str = "opened",
) -> WebhookJob:
    return WebhookJob(
        delivery_id=delivery_id,
        tenant="falkordb",
        event=event,
        action=action,
        issue_number=issue_number,
        title="t",
        body="b",
        issue_state="open",
        issue_author_login="octocat",
        issue_author_url="https://github.com/octocat",
        issue_author_is_bot=False,
        issue_created_at="2026-10-16T00:00:00Z",
        sender_login="octocat",
        comment_id=comment_id,
        comment_body="/keystone reject alice bad module" if comment_id is not None else None,
    )


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not met in time")


def test_worker_runs_one_job_at_a_time_in_fifo_order():
    processed: list[str] = []
    active = 0
    peak = 0
    guard = threading.Lock()

    def runner(job: WebhookJob) -> None:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with guard:
            active -= 1
            processed.append(job.delivery_id)

    worker = WebhookWorker(runner)
    worker.start()
    try:
        for index in range(5):
            # Distinct issues: this test is about serialization/FIFO, not admission.
            assert worker.enqueue(make_job(f"d{index}", issue_number=index)) is True
        wait_until(lambda: len(processed) == 5)
    finally:
        worker.close()

    assert peak == 1
    assert processed == ["d0", "d1", "d2", "d3", "d4"]


def test_a_failing_job_does_not_kill_the_worker():
    processed: list[str] = []

    def runner(job: WebhookJob) -> None:
        if job.delivery_id == "boom":
            raise RuntimeError("runner exploded")
        processed.append(job.delivery_id)

    worker = WebhookWorker(runner)
    worker.start()
    try:
        # Distinct issues: this test is about failure isolation, not admission.
        assert worker.enqueue(make_job("boom", issue_number=1)) is True
        assert worker.enqueue(make_job("after", issue_number=2)) is True
        wait_until(lambda: processed == ["after"])
        assert worker.enqueue(make_job("later", issue_number=3)) is True
        wait_until(lambda: processed == ["after", "later"])
    finally:
        worker.close()


def test_close_drains_an_admitted_job_then_refuses_new_work():
    ran = threading.Event()

    def runner(job: WebhookJob) -> None:
        ran.set()

    worker = WebhookWorker(runner)
    worker.start()
    assert worker.enqueue(make_job("only")) is True

    worker.close()

    assert ran.is_set()
    assert worker.enqueue(make_job("late")) is False


def test_enqueue_returns_false_when_the_queue_is_full():
    release = threading.Event()
    started = threading.Event()

    def runner(job: WebhookJob) -> None:
        started.set()
        release.wait(5)

    worker = WebhookWorker(runner, maxsize=1)
    worker.start()
    try:
        # Distinct issues: this test is about the queue bound, not admission.
        assert worker.enqueue(make_job("busy", issue_number=1)) is True
        assert started.wait(5)
        assert worker.enqueue(make_job("queued", issue_number=2)) is True
        assert worker.enqueue(make_job("overflow", issue_number=3)) is False
    finally:
        release.set()
        worker.close()


def test_enqueue_before_start_is_refused():
    worker = WebhookWorker(lambda job: None)

    assert worker.enqueue(make_job()) is False


def test_runner_may_host_asyncio_run_off_the_event_loop():
    seen: list[str] = []

    async def triage(job: WebhookJob) -> None:
        seen.append(job.delivery_id)

    worker = WebhookWorker(lambda job: asyncio.run(triage(job)))
    worker.start()
    try:
        assert worker.enqueue(make_job("async-1")) is True
        wait_until(lambda: seen == ["async-1"])
    finally:
        worker.close()

    assert seen == ["async-1"]


def test_close_releases_owned_clients_after_the_drain():
    events: list[str] = []

    def runner(job: WebhookJob) -> None:
        events.append("job")

    worker = WebhookWorker(runner, closer=lambda: events.append("closed"))
    worker.start()
    worker.enqueue(make_job("only"))

    worker.close()

    assert events == ["job", "closed"]


def test_failure_is_logged_with_context_and_never_the_payload(caplog):
    def runner(job: WebhookJob) -> None:
        raise RuntimeError("exploded with token super-secret-body")

    worker = WebhookWorker(runner)
    worker.start()
    try:
        with caplog.at_level(logging.ERROR):
            assert worker.enqueue(make_job("delivery-xyz", issue_number=7)) is True
            wait_until(lambda: any("webhook job failed" in r.message for r in caplog.records))
    finally:
        worker.close()

    (record,) = [r for r in caplog.records if "webhook job failed" in r.message]
    message = record.getMessage()
    assert "delivery=delivery-xyz" in message
    assert "issue=7" in message
    assert "RuntimeError" in message
    assert "super-secret-body" not in message


# --- admission state: delivery dedupe, pending guard, cooldown -------------


def test_the_same_delivery_is_admitted_only_once():
    processed: list[str] = []
    worker = WebhookWorker(lambda job: processed.append(job.delivery_id))
    worker.start()

    assert worker.enqueue(make_job("d1")) is True
    assert worker.enqueue(make_job("d1")) is True  # a retransmission is a no-op
    worker.close()

    assert processed == ["d1"]


def test_a_pending_issue_does_not_admit_a_second_delivery():
    started = threading.Event()
    release = threading.Event()
    processed: list[str] = []

    def runner(job: WebhookJob) -> None:
        processed.append(job.delivery_id)
        if job.delivery_id == "first":
            started.set()
            release.wait(5)

    worker = WebhookWorker(runner)
    worker.start()
    try:
        assert worker.enqueue(make_job("first", issue_number=5)) is True
        assert started.wait(5)
        # A different delivery for the same issue while the first is in flight.
        assert worker.enqueue(make_job("second", issue_number=5)) is True
    finally:
        release.set()
        worker.close()

    assert processed == ["first"]


def test_a_successful_issue_is_skipped_inside_the_cooldown_and_runs_after_it():
    clock = FakeClock(0.0)
    processed: list[str] = []
    synced = threading.Event()

    def runner(job: WebhookJob) -> None:
        processed.append(job.delivery_id)
        if job.delivery_id == "sync":
            synced.set()

    worker = WebhookWorker(runner, clock=clock)
    worker.start()
    try:
        assert worker.enqueue(make_job("first", issue_number=1)) is True
        assert worker.enqueue(make_job("sync", issue_number=99)) is True
        # The sync job runs after "first" completed, so its cooldown is recorded.
        assert synced.wait(5)

        clock.now = ISSUE_COOLDOWN_SECONDS - 1
        assert worker.enqueue(make_job("early", issue_number=1)) is True
        clock.now = ISSUE_COOLDOWN_SECONDS
        assert worker.enqueue(make_job("late", issue_number=1)) is True
    finally:
        worker.close()

    assert processed == ["first", "sync", "late"]


def test_a_failed_issue_releases_pending_and_allows_manual_redelivery():
    attempts: list[str] = []
    synced = threading.Event()

    def runner(job: WebhookJob) -> None:
        attempts.append(job.delivery_id)
        if job.delivery_id == "flaky" and attempts.count("flaky") == 1:
            raise RuntimeError("first attempt fails")
        if job.delivery_id == "sync":
            synced.set()

    worker = WebhookWorker(runner)
    worker.start()
    try:
        assert worker.enqueue(make_job("flaky", issue_number=1)) is True
        assert worker.enqueue(make_job("sync", issue_number=99)) is True
        # The sync job runs after the failure released the pending state.
        assert synced.wait(5)

        # A manual redelivery of the same delivery ID is admitted again.
        assert worker.enqueue(make_job("flaky", issue_number=1)) is True
    finally:
        worker.close()

    assert attempts == ["flaky", "sync", "flaky"]


def test_completed_delivery_retention_is_bounded_but_keeps_pending_ids():
    assert COMPLETED_DELIVERY_LIMIT == 10_000

    release = threading.Event()
    started = threading.Event()
    processed: list[str] = []

    def runner(job: WebhookJob) -> None:
        if job.delivery_id == "hold":
            started.set()
            release.wait(5)
        processed.append(job.delivery_id)

    # A tiny bound so eviction is observable; the default is the briefed 10,000.
    worker = WebhookWorker(runner, completed_delivery_limit=1)
    worker.start()
    try:
        assert worker.enqueue(make_job("old", issue_number=100)) is True
        assert worker.enqueue(make_job("hold", issue_number=200)) is True
        # "hold" starts only after "old" finished, so the completed bound is full.
        assert started.wait(5)
        assert worker.enqueue(make_job("queued", issue_number=300)) is True

        # In-flight and queued delivery IDs are still deduplicated, never evicted.
        assert worker.enqueue(make_job("hold", issue_number=999)) is True
        assert worker.enqueue(make_job("queued", issue_number=998)) is True
    finally:
        release.set()
        worker.close()

    assert processed == ["old", "hold", "queued"]


def test_a_completed_delivery_is_evicted_from_the_bounded_retention():
    processed: list[str] = []
    synced = threading.Event()

    def runner(job: WebhookJob) -> None:
        processed.append(job.delivery_id)
        if job.delivery_id == "sync":
            synced.set()

    worker = WebhookWorker(runner, completed_delivery_limit=3)
    worker.start()
    try:
        for index, delivery in enumerate(("d1", "d2", "d3"), start=1):
            assert worker.enqueue(make_job(delivery, issue_number=index)) is True
        # "sync" overflows the bound, evicting the oldest completed delivery.
        assert worker.enqueue(make_job("sync", issue_number=99)) is True
        assert synced.wait(5)

        assert worker.enqueue(make_job("d1", issue_number=100)) is True  # evicted: runs again
        assert worker.enqueue(make_job("d2", issue_number=101)) is True  # retained: no-op
    finally:
        worker.close()

    assert processed == ["d1", "d2", "d3", "sync", "d1"]


def test_command_jobs_bypass_the_issue_cooldown():
    processed: list[tuple[str, str]] = []
    worker = WebhookWorker(lambda job: processed.append((job.event, job.delivery_id)))
    worker.start()

    assert worker.enqueue(make_job("issue", issue_number=1, event="issues")) is True
    assert worker.enqueue(make_job("comment", issue_number=1, event="issue_comment")) is True
    worker.close()

    assert processed == [("issues", "issue"), ("issue_comment", "comment")]


# --- an edit inside the triage window still refreshes the issue -------------


def test_an_edit_inside_the_cooldown_is_admitted_upsert_only_and_keeps_the_cooldown():
    clock = FakeClock(0.0)
    processed: list[tuple[str, str, bool]] = []
    synced = threading.Event()

    def runner(job: WebhookJob) -> None:
        processed.append((job.delivery_id, job.action, job.triage))
        if job.delivery_id == "sync":
            synced.set()

    worker = WebhookWorker(runner, clock=clock)
    worker.start()
    try:
        assert worker.enqueue(make_job("first", issue_number=1)) is True
        assert worker.enqueue(make_job("sync", issue_number=99)) is True
        assert synced.wait(5)

        clock.now = 10
        assert worker.enqueue(make_job("edit", issue_number=1, action="edited")) is True
        # The same edit redelivered is still deduplicated by its delivery ID.
        assert worker.enqueue(make_job("edit", issue_number=1, action="edited")) is True
        # An "opened" inside the window is still skipped, as before.
        assert worker.enqueue(make_job("again", issue_number=1)) is True
        # The upsert-only edit did not restart the cooldown, which ends 60 s
        # after "first" completed: an edit then is ordinary triage again.
        clock.now = ISSUE_COOLDOWN_SECONDS
        assert worker.enqueue(make_job("late", issue_number=1, action="edited")) is True
    finally:
        worker.close()

    assert processed == [
        ("first", "opened", True),
        ("sync", "opened", True),
        ("edit", "edited", False),
        ("late", "edited", True),
    ]


def test_an_edit_while_the_issue_is_in_flight_queues_an_upsert_after_it():
    started = threading.Event()
    release = threading.Event()
    processed: list[tuple[str, bool]] = []

    def runner(job: WebhookJob) -> None:
        processed.append((job.delivery_id, job.triage))
        if job.delivery_id == "first":
            started.set()
            release.wait(5)

    worker = WebhookWorker(runner)
    worker.start()
    try:
        assert worker.enqueue(make_job("first", issue_number=5)) is True
        assert started.wait(5)
        assert worker.enqueue(make_job("edit", issue_number=5, action="edited")) is True
        assert worker.enqueue(make_job("reopen", issue_number=5)) is True  # still skipped
    finally:
        release.set()
        worker.close()

    assert processed == [("first", True), ("edit", False)]


# --- command comment dedupe -------------------------------------------------


def test_the_same_comment_is_admitted_only_once():
    processed: list[str] = []
    worker = WebhookWorker(lambda job: processed.append(job.delivery_id))
    worker.start()
    try:
        assert worker.enqueue(make_job("c1", event="issue_comment", comment_id=7)) is True
        # A redelivery of the same comment under a new delivery ID is a no-op.
        assert worker.enqueue(make_job("c2", event="issue_comment", comment_id=7)) is True
    finally:
        worker.close()

    assert processed == ["c1"]


def test_a_different_comment_on_the_same_issue_is_admitted():
    processed: list[str] = []
    worker = WebhookWorker(lambda job: processed.append(job.delivery_id))
    worker.start()
    try:
        assert worker.enqueue(make_job("c1", event="issue_comment", comment_id=7)) is True
        assert worker.enqueue(make_job("c2", event="issue_comment", comment_id=8)) is True
    finally:
        worker.close()

    assert processed == ["c1", "c2"]


def test_a_failed_command_releases_its_comment_id_for_retry():
    attempts: list[str] = []
    synced = threading.Event()

    def runner(job: WebhookJob) -> None:
        attempts.append(job.delivery_id)
        if job.delivery_id == "boom" and attempts.count("boom") == 1:
            raise RuntimeError("first attempt fails")
        if job.delivery_id == "sync":
            synced.set()

    worker = WebhookWorker(runner)
    worker.start()
    try:
        assert worker.enqueue(make_job("boom", event="issue_comment", comment_id=7)) is True
        assert worker.enqueue(make_job("sync", issue_number=99)) is True
        # The sync job runs after the failure released the comment reservation.
        assert synced.wait(5)
        assert worker.enqueue(make_job("boom", event="issue_comment", comment_id=7)) is True
    finally:
        worker.close()

    assert attempts == ["boom", "sync", "boom"]


def test_completed_comment_retention_is_bounded():
    processed: list[str] = []
    synced = threading.Event()

    def runner(job: WebhookJob) -> None:
        processed.append(job.delivery_id)
        if job.delivery_id == "sync":
            synced.set()

    # A tiny bound so eviction is observable; the default is the briefed 10,000.
    worker = WebhookWorker(runner, completed_delivery_limit=1)
    worker.start()
    try:
        assert worker.enqueue(make_job("c7", event="issue_comment", comment_id=7)) is True
        assert worker.enqueue(make_job("c8", event="issue_comment", comment_id=8)) is True
        assert worker.enqueue(make_job("sync", issue_number=99)) is True
        assert synced.wait(5)

        # c8 completed last and evicted c7's comment from the bounded memory.
        assert worker.enqueue(make_job("c7b", event="issue_comment", comment_id=7)) is True
        # c8 is still retained: a redelivery of the same comment is a no-op.
        assert worker.enqueue(make_job("c8b", event="issue_comment", comment_id=8)) is True
    finally:
        worker.close()

    assert processed == ["c7", "c8", "sync", "c7b"]


# --- bounded shutdown drain -------------------------------------------------


def test_close_gives_up_on_the_backlog_after_its_deadline_and_logs_each_job(caplog):
    started = threading.Event()
    release = threading.Event()
    processed: list[str] = []

    def runner(job: WebhookJob) -> None:
        if job.delivery_id == "busy":
            started.set()
            release.wait(10)
        processed.append(job.delivery_id)

    worker = WebhookWorker(runner, drain_timeout=0.2)
    worker.start()
    try:
        assert worker.enqueue(make_job("busy", issue_number=1)) is True
        assert started.wait(5)
        assert worker.enqueue(make_job("q1", issue_number=2)) is True
        assert worker.enqueue(make_job("q2", issue_number=3, event="issue_comment", comment_id=9)) is True

        with caplog.at_level(logging.ERROR):
            closer = threading.Thread(target=worker.close, daemon=True)
            closer.start()
            closer.join(5)
            # The drain is bounded: close returns although the job in progress
            # is still running and two accepted jobs never started.
            assert not closer.is_alive()
        assert worker.enqueue(make_job("late", issue_number=4)) is False
    finally:
        release.set()

    lines = [r.getMessage() for r in caplog.records if "shutdown" in r.getMessage()]
    assert len(lines) == 3
    assert any("delivery=busy" in line and "event=issues" in line and "issue=1" in line for line in lines)
    assert any("delivery=q1" in line and "event=issues" in line and "issue=2" in line for line in lines)
    assert any("delivery=q2" in line and "event=issue_comment" in line and "issue=3" in line for line in lines)
    for line in lines:
        # Identifiers only: never a title, body or comment text.
        assert "/keystone" not in line
        assert "title" not in line and "body" not in line
    # The in-progress job may finish; the abandoned backlog never runs.
    wait_until(lambda: processed == ["busy"])
    time.sleep(0.05)
    assert processed == ["busy"]


def test_close_drains_the_whole_backlog_inside_the_deadline_without_warnings(caplog):
    processed: list[str] = []
    worker = WebhookWorker(lambda job: processed.append(job.delivery_id), drain_timeout=5)
    worker.start()
    for index in range(3):
        assert worker.enqueue(make_job(f"d{index}", issue_number=index + 1)) is True

    with caplog.at_level(logging.ERROR):
        worker.close()

    assert processed == ["d0", "d1", "d2"]
    assert not [r for r in caplog.records if "shutdown" in r.getMessage()]


def test_close_on_a_full_queue_still_stops_inside_the_deadline():
    started = threading.Event()
    release = threading.Event()

    def runner(job: WebhookJob) -> None:
        started.set()
        release.wait(10)

    worker = WebhookWorker(runner, maxsize=1, drain_timeout=0.2)
    worker.start()
    try:
        assert worker.enqueue(make_job("busy", issue_number=1)) is True
        assert started.wait(5)
        assert worker.enqueue(make_job("queued", issue_number=2)) is True
        closer = threading.Thread(target=worker.close, daemon=True)
        closer.start()
        closer.join(5)
        assert not closer.is_alive()
    finally:
        release.set()
