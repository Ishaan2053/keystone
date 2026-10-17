"""B12: A11's deterministic feedback refusals get one explanatory reply, not a failing job.

``record_feedback`` raises ``FeedbackNotFound`` (unknown run, action not proposed by the run,
assign action with no Person) or ``FeedbackRefused`` (module-scoped rule without a module) before
it writes anything. The lane answers once with the "no rule was created" reply, runs no re-triage,
and a redelivery of the same comment neither records again nor replies again. Any other error
still propagates so the worker's retry semantics hold. No FalkorDB, network or LLM.
"""
from __future__ import annotations

import pytest

from api.comment_commands import no_rule_marker, reject_marker
from api.memory import FeedbackNotFound, FeedbackRefused
from test_comment_commands import (
    COMMENT_ID,
    FakeFeedback,
    FakeWriter,
    make_job,
    make_lane,
    make_triage,
    recorded_assign,
    run_data,
)

REFUSALS = [
    FeedbackNotFound("run run:x did not propose action action:assign-alice"),
    FeedbackRefused("assign action action:assign-alice has no module for a module-scoped rule"),
]


@pytest.mark.parametrize("error", REFUSALS, ids=lambda e: type(e).__name__)
def test_a_feedback_refusal_replies_once_without_retriage(error):
    writer = FakeWriter()
    feedback = FakeFeedback(error=error)
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

    lane(make_job())

    assert len(feedback.calls) == 1
    (number, body), = writer.comments
    assert number == 42
    assert no_rule_marker(COMMENT_ID) in body and reject_marker(COMMENT_ID) not in body
    assert "no rule was created" in body.lower()
    # Nothing was recorded, so the reply never claims it was; the exception text is never posted.
    assert "recorded" not in body.lower()
    assert str(error) not in body
    assert "`alice`" in body and "@alice" not in body
    assert triage_calls == []


@pytest.mark.parametrize("error", REFUSALS, ids=lambda e: type(e).__name__)
def test_a_redelivered_refusal_neither_records_nor_replies_again(error):
    writer = FakeWriter()
    feedback = FakeFeedback(error=error)
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

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
    (_, body), = writer.comments
    assert no_rule_marker(COMMENT_ID) in body and "recorded" not in body.lower()


def test_an_unrelated_feedback_error_still_propagates():
    writer = FakeWriter()
    feedback = FakeFeedback(error=RuntimeError("falkordb query timed out"))
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

    with pytest.raises(RuntimeError):
        lane(make_job())
    # A transient failure is retried against the same reserved target, not answered.
    with pytest.raises(RuntimeError):
        lane(make_job(delivery_id="redelivery"))

    assert len(feedback.calls) == 2
    assert writer.comments == []
    assert triage_calls == []
