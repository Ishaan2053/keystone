"""B12: A11's deterministic feedback refusals get one explanatory reply, not a failing job.

``record_feedback`` raises ``FeedbackNotFound`` (unknown run, action not proposed by the run,
assign action with no Person) or ``FeedbackRefused`` (module-scoped rule without a module) before
it writes anything. The lane answers once with a "could not record" reply, runs no re-triage,
and a same-process redelivery of the same comment neither records again nor replies again. Any
other error still propagates so the worker's retry semantics hold. No FalkorDB, network or LLM.
"""

from __future__ import annotations

import pytest
from test_comment_commands import (
    COMMENT_ID,
    RUN_KEY,
    RUN_URL,
    FakeFeedback,
    FakeWriter,
    make_job,
    make_lane,
    make_triage,
    recorded_assign,
    run_data,
)
from test_notifications import FakeStore as NotifStore
from test_notifications import RecordingSender, run_snapshot

from api.comment_commands import no_rule_marker, reject_marker
from api.memory import FeedbackNotFound, FeedbackRefused
from api.notifications import SlackNotifications

BASE_URL = "https://keystone.example"

REFUSALS = [
    FeedbackNotFound("run run:x did not propose action action:assign-alice"),
    FeedbackRefused("assign action action:assign-alice has no module for a module-scoped rule"),
]


@pytest.mark.parametrize("error", REFUSALS, ids=lambda e: type(e).__name__)
def test_a_feedback_refusal_replies_once_without_retriage(error):
    writer = FakeWriter()
    feedback = FakeFeedback(error=error)
    triage_calls: list = []
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        feedback=feedback,
        triage=make_triage(triage_calls),
    )

    lane(make_job())

    assert len(feedback.calls) == 1
    ((number, body),) = writer.comments
    assert number == 42
    assert no_rule_marker(COMMENT_ID) in body and reject_marker(COMMENT_ID) not in body
    # It names the resolved suggestion and run, never blames the latest run or claims a record.
    assert "could not record the rejection" in body.lower()
    assert "no rule was created and nothing was re-triaged" in body.lower()
    assert "latest triage run" not in body.lower() and "recorded" not in body.lower()
    assert f"`{RUN_KEY}`" in body and f"Run: {RUN_URL}" in body
    assert str(error) not in body  # the exception text is never posted
    assert "`alice`" in body and "@alice" not in body
    assert triage_calls == []


@pytest.mark.parametrize("error", REFUSALS, ids=lambda e: type(e).__name__)
def test_a_redelivered_refusal_neither_records_nor_replies_again(error):
    writer = FakeWriter()
    feedback = FakeFeedback(error=error)
    triage_calls: list = []
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        feedback=feedback,
        triage=make_triage(triage_calls),
    )

    lane(make_job(delivery_id="d1"))
    lane(make_job(delivery_id="d2"))  # same comment ID, redelivered

    assert len(feedback.calls) == 1
    assert len(writer.comments) == 1
    assert triage_calls == []


def test_a_refusal_whose_reply_failed_retries_only_the_reply():
    class FlakyWriter(FakeWriter):
        def __init__(self):
            super().__init__()
            self.fail_next = True

        def comment(self, number, body):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("github 502")
            super().comment(number, body)

    writer = FlakyWriter()
    feedback = FakeFeedback(error=FeedbackRefused("no module"))
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback)

    with pytest.raises(RuntimeError):
        lane(make_job(delivery_id="d1"))
    lane(make_job(delivery_id="d2"))

    assert len(feedback.calls) == 1
    ((_, body),) = writer.comments
    # The retried reply is the same "could not record" text, not the generic no-rule one.
    assert no_rule_marker(COMMENT_ID) in body
    assert "could not record the rejection" in body.lower() and f"`{RUN_KEY}`" in body


def test_an_unrelated_feedback_error_still_propagates():
    writer = FakeWriter()
    feedback = FakeFeedback(error=RuntimeError("falkordb query timed out"))
    triage_calls: list = []
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        feedback=feedback,
        triage=make_triage(triage_calls),
    )

    with pytest.raises(RuntimeError):
        lane(make_job())
    # A transient failure is retried against the same reserved target, not answered.
    with pytest.raises(RuntimeError):
        lane(make_job(delivery_id="redelivery"))

    assert len(feedback.calls) == 2
    assert writer.comments == []
    assert triage_calls == []


@pytest.mark.parametrize("error", REFUSALS, ids=lambda e: type(e).__name__)
def test_a_feedback_refusal_notifies_nothing(error):
    """A deterministic refusal raised before any write never reaches the Slack coordinator."""
    store = NotifStore(parent=run_snapshot(run_key=RUN_KEY), runs={})
    sender = RecordingSender()
    writer = FakeWriter()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        feedback=FakeFeedback(error=error),
        notifications=SlackNotifications(store, sender, public_base_url=BASE_URL),
    )

    lane(make_job())

    # The "could not record" reply still happens, but no follow-up is prepared or sent.
    ((number, body),) = writer.comments
    assert number == 42 and no_rule_marker(COMMENT_ID) in body
    assert store.calls == [] and sender.payloads == []
