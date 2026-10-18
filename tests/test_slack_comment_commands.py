"""Task 5: GitHub feedback commands notify Slack without changing any command semantic.

An authorized ``/keystone reject`` that resolves an exact reserved run and confirms a
persisted rule records one best-effort Slack attempt for the *re-triage* it triggers:
the coordinator is asked to prove the exact reserved parent (never a fresh latest
lookup after feedback was recorded), the re-triage run is marked with the
``comment:<comment_id>`` event, and ``complete`` runs only after the live apply
returns. A notification-only failure, an unproven parent, a ``/keystone why``, a
malformed command, a bot/non-collaborator, an absent assignment, an absent rule or a
refused feedback all stay silent, and the existing confirmation/re-triage/apply
outcome never changes because of Slack.

These tests use the Task 3 recording notification store/sender (no graph, LLM,
network or live GitHub write) and the B8 command-lane doubles.
"""

from __future__ import annotations

import json

import pytest
from test_comment_commands import (
    COMMENT_ID,
    ISSUE_KEY,
    NEW_RUN_KEY,
    RULE_KEY,
    RUN_KEY,
    TENANT,
    FakeApply,
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

from api.github import GitHubWriteError
from api.memory import FeedbackRefused
from api.notification_messages import STAGE_FAILURE_COPY, RunSnapshot
from api.notifications import SlackNotifications

BASE_URL = "https://keystone.example"


# --- doubles and helpers ----------------------------------------------------


def snapshot(run_key, *, label="urgent", at=1, ms=1, comment="composed comment"):
    """One persisted run differing only in execution metadata and prose from its twin."""
    base = run_snapshot(run_key=run_key, label=label)
    data = base.data.model_copy(
        update={
            "run": base.data.run.model_copy(update={"at": at, "ms": ms}),
            "triage": base.data.triage.model_copy(update={"comment_markdown": comment}),
        }
    )
    return RunSnapshot(data=data, title=base.title, replay_of=base.replay_of)


def proven(*, parent_label="urgent", child_label="urgent", at=1, ms=1, comment="composed comment"):
    """A notification store that proves the reserved parent of the command's re-triage."""
    return NotifStore(
        parent=run_snapshot(run_key=RUN_KEY, label=parent_label),
        runs={NEW_RUN_KEY: snapshot(NEW_RUN_KEY, label=child_label, at=at, ms=ms, comment=comment)},
    )


def coordinator(store, sender) -> SlackNotifications:
    return SlackNotifications(store, sender, public_base_url=BASE_URL)


class ThrowingNotifier:
    """A minimal adapter double that raises at a selected boundary."""

    def __init__(self, *, raises=()):
        self._raises = set(raises)
        self.begins: list[dict] = []
        self.completes: list[tuple] = []
        self.failures: list[tuple] = []

    def begin_github_feedback(self, **kwargs):
        self.begins.append(kwargs)
        if "begin" in self._raises:
            raise RuntimeError("adapter boom")
        return "attempt"

    def complete(self, attempt, *, run_key, applied):
        self.completes.append((attempt, run_key, applied))
        if "complete" in self._raises:
            raise RuntimeError("adapter boom")

    def failed(self, attempt, *, stage, run_key=None):
        self.failures.append((attempt, stage, run_key))
        if "failed" in self._raises:
            raise RuntimeError("adapter boom")


class RaisingApply:
    """An apply service that always raises, exposing the exact exception instance."""

    def __init__(self, error: Exception):
        self.error = error
        self.calls: list[tuple] = []
        self.public_base_url = BASE_URL

    def apply_triage(self, tenant, run_key, dry_run=True):
        self.calls.append((tenant, run_key, bool(dry_run)))
        raise self.error


def failing_triage(error: Exception):
    async def triage(tenant, issue_key):
        raise error

    return triage


# --- the notifying follow-up ------------------------------------------------


def test_reject_changed_recommendation_notifies():
    """A changed re-triage notifies with the comment-scoped event and exact parent."""
    store = proven(child_label="blocker")
    sender = RecordingSender()
    writer = FakeWriter()
    feedback = FakeFeedback(rule_key=RULE_KEY)
    apply = FakeApply()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        feedback=feedback,
        apply=apply,
        notifications=coordinator(store, sender),
    )

    lane(make_job())

    # The exact reserved run is what the coordinator was asked to prove, never latest.
    assert ("eligible_parent", TENANT, ISSUE_KEY, RUN_KEY, None) in store.calls
    assert len(sender.payloads) == 1
    rendered = json.dumps(sender.payloads[0], ensure_ascii=False)
    assert "Changed since the parent run" in rendered
    assert RUN_KEY in rendered
    # The message is rendered only after apply, with the real apply outcome.
    assert "GitHub apply outcome: no actions." in rendered
    # Lineage is recorded under the comment-scoped event with the exact reserved parent.
    mark = next(call for call in store.calls if call[0] == "mark_run")
    assert mark[1:] == (
        TENANT,
        NEW_RUN_KEY,
        ISSUE_KEY,
        "github_feedback",
        f"comment:{COMMENT_ID}",
        RUN_KEY,
    )
    # The GitHub confirmation and the live apply are unchanged.
    ((number, body),) = writer.comments
    assert number == 42 and RULE_KEY in body
    assert apply.calls == [(TENANT, NEW_RUN_KEY, False)]


def test_unchanged_retriage_is_silent():
    """A re-triage that only changes timings/prose records lineage but sends nothing."""
    store = proven(at=999, ms=12_345, comment="a wholly different composed comment")
    sender = RecordingSender()
    writer = FakeWriter()
    apply = FakeApply()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        apply=apply,
        notifications=coordinator(store, sender),
    )

    lane(make_job())

    assert sender.payloads == []
    mark = next(call for call in store.calls if call[0] == "mark_run")
    assert mark[1:] == (
        TENANT,
        NEW_RUN_KEY,
        ISSUE_KEY,
        "github_feedback",
        f"comment:{COMMENT_ID}",
        RUN_KEY,
    )
    # The core lifecycle is untouched: exactly one confirmation and one live apply.
    assert len(writer.comments) == 1
    assert apply.calls == [(TENANT, NEW_RUN_KEY, False)]


@pytest.mark.parametrize(
    "scenario",
    [
        "why",
        "malformed",
        "noncollaborator",
        "bot",
        "absent-assignment",
        "absent-rule",
        "refused-feedback",
        "unproven-parent",
    ],
)
def test_why_ignored_and_unauthorized_commands_are_silent(scenario):
    """Every command that must not notify sends nothing; the unproven parent is proven."""
    notif = proven()
    sender = RecordingSender()
    kwargs: dict = {}
    job_kwargs: dict = {}
    if scenario == "why":
        job_kwargs["comment_body"] = "/keystone why alice"
    elif scenario == "malformed":
        job_kwargs["comment_body"] = "/keystone reject alice"  # no reason: ignored
    elif scenario == "noncollaborator":
        kwargs["writer"] = FakeWriter(collaborator=False)
    elif scenario == "bot":
        kwargs["bot_login"] = "keystone-bot"
        job_kwargs["sender_login"] = "keystone-bot"
    elif scenario == "absent-assignment":
        kwargs["run"] = run_data(recorded_assign("bob"))
    elif scenario == "absent-rule":
        kwargs["feedback"] = FakeFeedback(rule_key=None)
    elif scenario == "refused-feedback":
        kwargs["feedback"] = FakeFeedback(error=FeedbackRefused("assign has no module"))
    else:  # unproven-parent: the store cannot prove the reserved run
        notif = NotifStore(parent=None, runs={NEW_RUN_KEY: snapshot(NEW_RUN_KEY)})
    lane = make_lane(
        run=kwargs.pop("run", run_data(recorded_assign("alice"))),
        notifications=coordinator(notif, sender),
        **kwargs,
    )

    lane(make_job(**job_kwargs))

    assert sender.payloads == []
    assert "mark_run" not in notif.kinds()
    if scenario == "unproven-parent":
        # The one case that reaches the coordinator: it is asked for the exact parent
        # and, without proof, notifies nothing.
        assert ("eligible_parent", TENANT, ISSUE_KEY, RUN_KEY, None) in notif.calls
    else:
        assert "eligible_parent" not in notif.kinds()


# --- isolation and core-failure reporting -----------------------------------


@pytest.mark.parametrize("boundary", ["begin", "complete"])
def test_feedback_preparation_and_slack_failures_do_not_change_command(boundary):
    """A throwing adapter or store never changes authorize/record/confirm/re-triage/apply."""
    notifier = ThrowingNotifier(raises={boundary})
    writer = FakeWriter()
    feedback = FakeFeedback(rule_key=RULE_KEY)
    triage_calls: list = []
    apply = FakeApply()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        feedback=feedback,
        triage=make_triage(triage_calls),
        apply=apply,
        notifications=notifier,
    )

    lane(make_job())  # must not raise

    assert len(feedback.calls) == 1
    ((_, body),) = writer.comments
    assert RULE_KEY in body
    assert triage_calls == [(TENANT, ISSUE_KEY)]
    assert apply.calls == [(TENANT, NEW_RUN_KEY, False)]
    assert len(notifier.begins) == 1  # began only after the successful confirmation

    # A notification store that cannot prove lineage is isolated the same way.
    broken = proven()
    broken.errors["parent"] = RuntimeError("store down")
    sender = RecordingSender()
    writer2 = FakeWriter()
    apply2 = FakeApply()
    lane2 = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer2,
        apply=apply2,
        notifications=coordinator(broken, sender),
    )

    lane2(make_job())

    assert len(writer2.comments) == 1
    assert apply2.calls == [(TENANT, NEW_RUN_KEY, False)]
    assert sender.payloads == []

    # Feedback is announced only after the confirmation succeeded: a failed post
    # sends nothing and stops the command before any re-triage.
    class FlakyWriter(FakeWriter):
        def __init__(self):
            super().__init__()
            self.fail_next = True

        def comment(self, number, body):
            if self.fail_next:
                self.fail_next = False
                raise GitHubWriteError("sandbox comment failed with status 502")
            super().comment(number, body)

    unconfirmed = proven()
    unconfirmed_sender = RecordingSender()
    triage_calls2: list = []
    lane3 = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=FlakyWriter(),
        triage=make_triage(triage_calls2),
        notifications=coordinator(unconfirmed, unconfirmed_sender),
    )

    with pytest.raises(GitHubWriteError):
        lane3(make_job())

    assert unconfirmed_sender.payloads == []
    assert triage_calls2 == []
    assert unconfirmed.calls == []


def test_eligible_retriage_failure_notifies_and_reraises():
    """An eligible core failure reports its stage and re-raises the exact exception."""
    # Triage failure: no run is persisted, so only the stage is reported.
    store = proven()
    sender = RecordingSender()
    triage_boom = RuntimeError("agent down")
    writer = FakeWriter()
    apply = FakeApply()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        triage=failing_triage(triage_boom),
        apply=apply,
        notifications=coordinator(store, sender),
    )

    with pytest.raises(RuntimeError) as raised:
        lane(make_job())
    assert raised.value is triage_boom
    assert len(sender.payloads) == 1
    assert STAGE_FAILURE_COPY["triage"] in json.dumps(sender.payloads[0], ensure_ascii=False)
    assert "mark_run" not in store.kinds()
    assert apply.calls == []

    # Apply failure: the persisted re-triage run key is reported and never released early.
    store = proven()
    sender = RecordingSender()
    apply_boom = RuntimeError("apply down")
    writer = FakeWriter()
    apply = RaisingApply(apply_boom)
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        apply=apply,
        notifications=coordinator(store, sender),
    )

    with pytest.raises(RuntimeError) as raised:
        lane(make_job())
    assert raised.value is apply_boom
    assert len(sender.payloads) == 1
    assert STAGE_FAILURE_COPY["apply"] in json.dumps(sender.payloads[0], ensure_ascii=False)
    assert "mark_run" not in store.kinds()
    assert apply.calls == [(TENANT, NEW_RUN_KEY, False)]
    assert len(writer.comments) == 1  # the confirmation still happened

    # Authorization failure (denied): nothing is looked up and nothing is sent.
    store = proven()
    sender = RecordingSender()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=FakeWriter(collaborator=False),
        notifications=coordinator(store, sender),
    )

    lane(make_job())

    assert store.calls == [] and sender.payloads == []

    # Authorization failure (lookup error): nothing is sent and the error propagates.
    store = proven()
    sender = RecordingSender()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=FakeWriter(error=GitHubWriteError("collaborator lookup failed with status 403")),
        notifications=coordinator(store, sender),
    )

    with pytest.raises(GitHubWriteError):
        lane(make_job())

    assert store.calls == [] and sender.payloads == []

    # Feedback-resolution failure: nothing is sent, and a transient error still propagates.
    store = proven()
    sender = RecordingSender()
    boom = RuntimeError("falkordb query timed out")
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        feedback=FakeFeedback(error=boom),
        notifications=coordinator(store, sender),
    )

    with pytest.raises(RuntimeError) as raised:
        lane(make_job())
    assert raised.value is boom
    assert store.calls == [] and sender.payloads == []

    # A deterministic refusal replies without ever reaching the coordinator.
    store = proven()
    sender = RecordingSender()
    writer = FakeWriter()
    lane = make_lane(
        run=run_data(recorded_assign("alice")),
        writer=writer,
        feedback=FakeFeedback(error=FeedbackRefused("no module")),
        notifications=coordinator(store, sender),
    )

    lane(make_job())

    assert len(writer.comments) == 1
    assert store.calls == [] and sender.payloads == []
