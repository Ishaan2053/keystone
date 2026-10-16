"""B8 Task 4: collaborator comment commands (parse, format, reject lane).

Only a strict leading ``/keystone reject <login> <reason>`` or
``/keystone why <login>`` counts; an ordinary comment, a quoted/fenced/incidental
mention or a missing reason is ignored. The reject lane verifies the actual
comment sender (``is_collaborator``), resolves the latest HANDLED run for the
exact incoming issue, finds exactly one matching assign action with a module,
records a module-scoped reject feedback through the injected A11 seam, confirms
only a persisted rule key with a safe marker, then re-triages and live-applies.

The A11 ``record_feedback`` is injected here as a fake; the real graph-backed
feedback/rule integration test is deferred (R2/R5).
"""
from __future__ import annotations

import pytest

from api import schemas
from api.comment_commands import (
    CommentCommandLane,
    RejectCommand,
    WhyCommand,
    format_no_rule,
    format_reject_confirmation,
    no_rule_marker,
    parse_command,
    reject_marker,
)
from api.github import GitHubWriteError
from api.webhooks import WebhookJob

TENANT = "falkordb"
SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
MODULE = "module:acme/keystone-sandbox/src/graph"
RUN_KEY = "run:00000000-0000-4000-8000-000000000001"
NEW_RUN_KEY = "run:00000000-0000-4000-8000-0000000000aa"
RULE_KEY = "rule:00000000-0000-4000-8000-000000000002"
EXISTING_RULE_KEY = "rule:00000000-0000-4000-8000-0000000000bb"
COMMENT_ID = 555


def make_job(**overrides) -> WebhookJob:
    values = dict(
        delivery_id="d1",
        tenant=TENANT,
        event="issue_comment",
        action="created",
        issue_number=42,
        title="t",
        body="b",
        issue_state="open",
        issue_author_login="octocat",
        issue_author_url="https://github.com/octocat",
        issue_author_is_bot=False,
        issue_created_at="2026-10-16T00:00:00Z",
        sender_login="maintainer",
        comment_id=COMMENT_ID,
        comment_body="/keystone reject alice bad module",
    )
    values.update(overrides)
    return WebhookJob(**values)


# --- parse_command ----------------------------------------------------------


def test_parse_reject_with_a_single_line_reason():
    assert parse_command("/keystone reject alice bad module") == RejectCommand("alice", "bad module")


def test_parse_reject_strips_one_optional_at_and_preserves_case():
    assert parse_command("/keystone reject @Alice because") == RejectCommand("Alice", "because")


def test_parse_reject_preserves_a_multiline_reason():
    body = "/keystone reject alice first line\nsecond line"
    assert parse_command(body) == RejectCommand("alice", "first line\nsecond line")


def test_parse_reject_without_a_reason_is_ignored():
    assert parse_command("/keystone reject alice") is None
    assert parse_command("/keystone reject alice   ") is None


def test_parse_reject_with_an_empty_reason_body_is_ignored():
    assert parse_command("/keystone reject alice\n\n   ") is None


def test_parse_ignores_a_quoted_command():
    assert parse_command("> /keystone reject alice bad module") is None


def test_parse_ignores_a_fenced_command():
    assert parse_command("```\n/keystone reject alice bad module\n```") is None


def test_parse_ignores_an_incidental_command():
    assert parse_command("please run /keystone reject alice bad module") is None


def test_parse_ignores_an_ordinary_comment():
    assert parse_command("looks good to me") is None
    assert parse_command("") is None
    assert parse_command(None) is None


def test_parse_ignores_a_command_after_a_leading_blank_line():
    assert parse_command("\n/keystone reject alice bad module") is None


def test_parse_rejects_a_malformed_login():
    assert parse_command("/keystone reject alice, bad") is None
    assert parse_command("/keystone reject @ bad") is None


def test_parse_why():
    assert parse_command("/keystone why bob") == WhyCommand("bob")
    assert parse_command("/keystone why @Bob") == WhyCommand("Bob")


def test_parse_why_with_extra_tokens_is_ignored():
    assert parse_command("/keystone why bob please") is None
    assert parse_command("/keystone why bob\nextra") is None


# --- safe formatting --------------------------------------------------------


def test_reject_confirmation_has_no_live_mention_and_a_stable_marker():
    body = format_reject_confirmation(
        login="alice", reason="wrong module", rule_key=RULE_KEY, comment_id=COMMENT_ID
    )

    assert RULE_KEY in body
    assert reject_marker(COMMENT_ID) in body
    assert "@alice" not in body
    assert "`alice`" in body


def test_reject_confirmation_neutralizes_a_mention_in_the_reason():
    body = format_reject_confirmation(
        login="alice", reason="@bob owns this", rule_key=RULE_KEY, comment_id=COMMENT_ID
    )

    assert "@bob" not in body
    assert "`bob`" in body


def test_no_rule_reply_is_safe_and_marked():
    body = format_no_rule(login="alice", comment_id=COMMENT_ID)

    assert no_rule_marker(COMMENT_ID) in body
    # The confirmation marker is distinct, so a "no rule" reply never hides one.
    assert reject_marker(COMMENT_ID) not in body
    assert "@alice" not in body
    assert "`alice`" in body


# --- the reject lane --------------------------------------------------------


def recorded_assign(login: str, *, module: str | None = MODULE, feedback=None, status="proposed"):
    return schemas.RecordedAction(
        action_key=f"action:assign-{login}",
        kind="assign",
        value=login,
        module=module,
        status=status,
        note=None,
        feedback=list(feedback or []),
    )


def run_data(*actions) -> schemas.RecallRunData:
    return schemas.RecallRunData(
        run=schemas.RunSummary(
            run_key=RUN_KEY,
            at=1,
            issue_key=ISSUE_KEY,
            summary="s",
            fallback=False,
            model="m",
            llm_calls=0,
            ms=1,
        ),
        evidence=[],
        actions=list(actions),
        triage=schemas.TriageResult(
            run_key=RUN_KEY,
            labels=[],
            reviewers=[],
            risks=[],
            decisions=[],
            comment_markdown="x",
            trace=[],
        ),
    )


class FakeWriter:
    """A sandbox writer stand-in; it deliberately has no assign/unassign method."""

    def __init__(self, *, collaborator=True, error=None, existing_markers=()):
        self.repo = SANDBOX
        self.collaborator = collaborator
        self.error = error
        self.existing = set(existing_markers)
        self.collaborator_calls: list[str] = []
        self.comments: list[tuple[int, str]] = []

    def is_collaborator(self, login: str) -> bool:
        self.collaborator_calls.append(login)
        if self.error is not None:
            raise self.error
        return self.collaborator

    def find_comment(self, number: int, marker: str) -> bool:
        return marker in self.existing or any(marker in body for _, body in self.comments)

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))


class FakeStore:
    def __init__(self, run=None):
        self.run = run
        self.latest_calls: list[tuple[str, str]] = []

    def incoming_issue_key(self, number: int) -> str:
        return f"issue:{SANDBOX}#{number}"

    def latest_run(self, tenant: str, issue_key: str):
        self.latest_calls.append((tenant, issue_key))
        return self.run


class FakeFeedback:
    def __init__(self, *, rule_key=RULE_KEY, error=None):
        self.calls: list[tuple] = []
        self.rule_key = rule_key
        self.error = error

    def __call__(self, tenant, run_key, request, *, given_by):
        self.calls.append((tenant, run_key, request, given_by))
        if self.error is not None:
            raise self.error
        return schemas.FeedbackResult(
            feedback_key="feedback:x",
            action_key=request.action_key,
            verdict=request.verdict,
            rule_key=self.rule_key,
        )


class FakeApply:
    def __init__(self):
        self.calls: list[tuple] = []

    def apply_triage(self, tenant, run_key, dry_run=True):
        self.calls.append((tenant, run_key, bool(dry_run)))
        return schemas.ApplyResult(run_key=run_key, dry_run=dry_run, actions=[])


def make_triage(calls=None, run_key=NEW_RUN_KEY):
    calls = [] if calls is None else calls

    async def triage(tenant: str, issue_key: str) -> schemas.TriageResult:
        calls.append((tenant, issue_key))
        return schemas.TriageResult(
            run_key=run_key,
            labels=[],
            reviewers=[],
            risks=[],
            decisions=[],
            comment_markdown="x",
            trace=[],
        )

    return triage


def make_lane(*, run=None, writer=None, feedback=None, triage=None, apply=None):
    return CommentCommandLane(
        store=FakeStore(run=run),
        writer=writer if writer is not None else FakeWriter(),
        record_feedback=feedback if feedback is not None else FakeFeedback(),
        triage=triage if triage is not None else make_triage(),
        apply_service=apply if apply is not None else FakeApply(),
        tenant=TENANT,
    )


def test_collaborator_reject_records_feedback_confirms_and_retriages():
    action = recorded_assign("alice")
    writer = FakeWriter(collaborator=True)
    feedback = FakeFeedback(rule_key=RULE_KEY)
    triage_calls: list = []
    apply = FakeApply()
    lane = make_lane(
        run=run_data(action),
        writer=writer,
        feedback=feedback,
        triage=make_triage(triage_calls),
        apply=apply,
    )

    lane(make_job())

    (call,) = feedback.calls
    tenant, run_key, request, given_by = call
    assert (tenant, run_key, given_by) == (TENANT, RUN_KEY, "maintainer")
    assert request.action_key == action.action_key
    assert request.verdict == "reject" and request.scope == "module"
    assert request.reason == "bad module"
    (number, body), = writer.comments
    assert number == 42
    assert RULE_KEY in body and reject_marker(COMMENT_ID) in body
    assert "@alice" not in body
    assert triage_calls == [(TENANT, ISSUE_KEY)]
    # The re-triage's own run is live-applied, not the rejected run.
    assert apply.calls == [(TENANT, NEW_RUN_KEY, False)]


def test_non_collaborator_is_ignored_without_writes():
    writer = FakeWriter(collaborator=False)
    feedback = FakeFeedback()
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

    lane(make_job())

    assert writer.collaborator_calls == ["maintainer"]
    assert writer.comments == []
    assert feedback.calls == []
    assert triage_calls == []


def test_collaborator_lookup_failure_fails_closed():
    writer = FakeWriter(error=GitHubWriteError("sandbox collaborator lookup failed with status 403"))
    feedback = FakeFeedback()
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

    with pytest.raises(GitHubWriteError):
        lane(make_job())

    assert writer.comments == []
    assert feedback.calls == []
    assert triage_calls == []


def test_missing_run_replies_without_a_rule():
    writer = FakeWriter()
    feedback = FakeFeedback()
    triage_calls: list = []
    lane = make_lane(run=None, writer=writer, feedback=feedback, triage=make_triage(triage_calls))

    lane(make_job())

    (number, body), = writer.comments
    assert number == 42 and no_rule_marker(COMMENT_ID) in body
    assert feedback.calls == [] and triage_calls == []


def test_no_matching_assign_replies_without_a_rule():
    writer = FakeWriter()
    feedback = FakeFeedback()
    lane = make_lane(run=run_data(recorded_assign("bob")), writer=writer, feedback=feedback)

    lane(make_job())

    assert len(writer.comments) == 1
    assert feedback.calls == []


def test_multiple_matching_assigns_reply_without_a_rule():
    writer = FakeWriter()
    feedback = FakeFeedback()
    run = run_data(recorded_assign("alice"), recorded_assign("alice"))
    lane = make_lane(run=run, writer=writer, feedback=feedback)

    lane(make_job())

    assert len(writer.comments) == 1
    assert feedback.calls == []


def test_assign_without_a_module_replies_without_a_rule():
    writer = FakeWriter()
    feedback = FakeFeedback()
    lane = make_lane(run=run_data(recorded_assign("alice", module=None)), writer=writer, feedback=feedback)

    lane(make_job())

    assert len(writer.comments) == 1
    assert feedback.calls == []


def test_a_duplicate_comment_never_creates_another_rule():
    writer = FakeWriter()
    feedback = FakeFeedback(rule_key=RULE_KEY)
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback)

    lane(make_job(delivery_id="d1"))
    lane(make_job(delivery_id="d2"))  # same comment ID, redelivered

    assert len(feedback.calls) == 1
    assert len(writer.comments) == 1


def test_reloads_an_existing_rejection_rule_instead_of_creating_another():
    existing = schemas.FeedbackResult(
        feedback_key="feedback:old",
        action_key="action:assign-alice",
        verdict="reject",
        rule_key=EXISTING_RULE_KEY,
    )
    action = recorded_assign("alice", feedback=[existing], status="rejected")
    writer = FakeWriter()
    feedback = FakeFeedback(rule_key=RULE_KEY)
    lane = make_lane(run=run_data(action), writer=writer, feedback=feedback)

    lane(make_job())

    assert feedback.calls == []
    (_, body), = writer.comments
    assert EXISTING_RULE_KEY in body and RULE_KEY not in body


def test_feedback_persistence_failure_prevents_reply_and_retriage():
    writer = FakeWriter()
    feedback = FakeFeedback(error=RuntimeError("graph down"))
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

    with pytest.raises(RuntimeError):
        lane(make_job())

    assert writer.comments == []
    assert triage_calls == []


def test_a_missing_rule_key_is_not_confirmed():
    writer = FakeWriter()
    feedback = FakeFeedback(rule_key=None)
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

    lane(make_job())

    assert writer.comments == []
    assert triage_calls == []


def test_confirmation_marker_lookup_prevents_a_duplicate_reply():
    writer = FakeWriter(existing_markers=[reject_marker(COMMENT_ID)])
    feedback = FakeFeedback(rule_key=RULE_KEY)
    triage_calls: list = []
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback,
                     triage=make_triage(triage_calls))

    lane(make_job())

    assert len(feedback.calls) == 1
    assert writer.comments == []
    assert triage_calls == [(TENANT, ISSUE_KEY)]


def test_reject_uses_the_trusted_identity_never_comment_text():
    action = recorded_assign("alice")
    writer = FakeWriter()
    feedback = FakeFeedback(rule_key=RULE_KEY)
    lane = make_lane(run=run_data(action), writer=writer, feedback=feedback)
    body = "/keystone reject alice bad module\n\naction:evil run:evil module:evil"

    lane(make_job(comment_body=body))

    request = feedback.calls[0][2]
    assert request.action_key == action.action_key
    assert request.action_key != "action:evil"


def test_why_is_parsed_here_but_not_handled_in_this_lane():
    writer = FakeWriter()
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer)

    lane(make_job(comment_body="/keystone why alice"))

    assert writer.collaborator_calls == []
    assert writer.comments == []


def test_a_reject_without_a_comment_id_is_ignored():
    writer = FakeWriter()
    feedback = FakeFeedback()
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer, feedback=feedback)

    lane(make_job(comment_id=None))

    assert writer.collaborator_calls == []
    assert feedback.calls == []


def test_the_lane_never_removes_the_assignee():
    writer = FakeWriter()
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=writer)

    lane(make_job())

    # A writer without any unassign/remove method proves no removal is attempted.
    assert not hasattr(writer, "unassign")
    assert not hasattr(writer, "remove_assignee")
    assert [number for number, _ in writer.comments] == [42]
