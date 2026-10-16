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

from api.webhooks import WebhookJob, WebhookWorker


def make_job(delivery_id: str = "d1", issue_number: int = 1) -> WebhookJob:
    return WebhookJob(
        delivery_id=delivery_id,
        tenant="falkordb",
        event="issues",
        action="opened",
        issue_number=issue_number,
        title="t",
        body="b",
        issue_state="open",
        issue_author_login="octocat",
        issue_author_url="https://github.com/octocat",
        issue_author_is_bot=False,
        issue_created_at="2026-10-16T00:00:00Z",
        sender_login="octocat",
        comment_id=None,
        comment_body=None,
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
            assert worker.enqueue(make_job(f"d{index}")) is True
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
        assert worker.enqueue(make_job("boom")) is True
        assert worker.enqueue(make_job("after")) is True
        wait_until(lambda: processed == ["after"])
        assert worker.enqueue(make_job("later")) is True
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
        assert worker.enqueue(make_job("busy")) is True
        assert started.wait(5)
        assert worker.enqueue(make_job("queued")) is True
        assert worker.enqueue(make_job("overflow")) is False
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
