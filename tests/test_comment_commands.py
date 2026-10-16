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

B8 Task 5a adds the ``why`` lane: for a collaborator ``/keystone why <login>`` it
verifies the actual sender, resolves the latest HANDLED run, calls the injected
``explain_path`` from the trusted incoming issue key to ``person:<login>``, and
renders the stored path (keys and relationship types in order) with only the
returned keys' stored URLs as code spans and the configured run URL clickable. An
empty path (no route within four hops, or an unknown person) or no run at all is
explained as absent; no replacement chain is computed here. A bot or
non-collaborator gets no graph query and no reply, and a 403/429 lookup fails
closed. All replies carry a stable comment-ID marker and are checked with
``find_comment`` before posting.
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
    format_why,
    format_why_no_run,
    no_rule_marker,
    parse_command,
    reject_marker,
    why_marker,
)
from api.github import GitHubWriteError
from api.github_text import code_span
from api.webhooks import WebhookJob

TENANT = "falkordb"
SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
PERSON_KEY = "person:alice"
ISSUE_URL = f"https://github.com/{SANDBOX}/issues/42"
PERSON_URL = "https://github.com/alice"
RUN_URL = f"https://keystone.example/t/{TENANT}?run=00000000-0000-4000-8000-000000000001"
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


def test_parse_ignores_an_indented_command():
    # A four-space-indented line is a standard Markdown code block, not a command.
    assert parse_command("    /keystone reject alice bad module") is None
    assert parse_command("\t/keystone why alice") is None


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


def test_reject_confirmation_bounds_a_huge_reason():
    reason = "x" * 60_000

    body = format_reject_confirmation(
        login="alice", reason=reason, rule_key=RULE_KEY, comment_id=COMMENT_ID
    )

    # The echoed reason is truncated so the reply cannot exceed GitHub's comment cap.
    assert len(body) < 5_000
    assert reason not in body
    assert "…" in body
    assert reject_marker(COMMENT_ID) in body


def test_no_rule_reply_is_safe_and_marked():
    body = format_no_rule(login="alice", comment_id=COMMENT_ID)

    assert no_rule_marker(COMMENT_ID) in body
    # The confirmation marker is distinct, so a "no rule" reply never hides one.
    assert reject_marker(COMMENT_ID) not in body
    assert "@alice" not in body
    assert "`alice`" in body


# --- format_why -------------------------------------------------------------


def test_format_why_renders_keys_and_relationship_types_in_order():
    path = schemas.PathData(keys=[ISSUE_KEY, PERSON_KEY], rels=["AUTHORED"])

    body = format_why(path, {ISSUE_KEY: ISSUE_URL, PERSON_KEY: PERSON_URL}, RUN_URL)

    assert body.index(ISSUE_KEY) < body.index("AUTHORED") < body.index(PERSON_KEY)
    assert ISSUE_URL in body and PERSON_URL in body
    assert RUN_URL in body


def test_format_why_renders_a_source_link_as_a_code_span_not_a_live_backlink():
    path = schemas.PathData(keys=[ISSUE_KEY, PERSON_KEY], rels=["AUTHORED"])

    body = format_why(path, {ISSUE_KEY: ISSUE_URL}, RUN_URL)

    assert code_span(ISSUE_URL) in body
    assert f"]({ISSUE_URL})" not in body
    assert "](http" not in body
    assert "@" not in body


def test_format_why_keeps_the_run_url_clickable():
    path = schemas.PathData(keys=[ISSUE_KEY, PERSON_KEY], rels=["AUTHORED"])

    body = format_why(path, {}, RUN_URL)

    # The configured run URL is not a GitHub source link, so it stays live.
    assert RUN_URL in body
    assert code_span(RUN_URL) not in body


def test_format_why_empty_path_says_no_evidence_and_invents_no_chain():
    body = format_why(schemas.PathData(keys=[], rels=[]), {}, RUN_URL)

    assert "evidence" in body.lower()
    assert "chain" in body.lower()
    assert ISSUE_KEY not in body and PERSON_KEY not in body
    assert "->" not in body
    assert RUN_URL in body
    assert "@" not in body


def test_format_why_no_run_is_explicit_and_safe():
    body = format_why_no_run("alice")

    assert "no handled triage run" in body.lower()
    assert "`alice`" in body
    assert "@alice" not in body


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
    def __init__(self, run=None, links=None, persons=None):
        self.run = run
        self.links = dict(links or {})
        # Canonical Person keys by casefolded login, mirroring the graph's stored casing.
        self.persons = {login.casefold(): key for login, key in (persons or {}).items()}
        self.latest_calls: list[tuple[str, str]] = []
        self.runs = {} if run is None else {run.run.run_key: run}
        self.recall_calls: list[tuple[str, str]] = []
        self.link_calls: list[tuple[str, list[str]]] = []
        self.person_calls: list[tuple[str, str]] = []

    def incoming_issue_key(self, number: int) -> str:
        return f"issue:{SANDBOX}#{number}"

    def latest_run(self, tenant: str, issue_key: str):
        self.latest_calls.append((tenant, issue_key))
        return self.run

    def person_key(self, tenant: str, login: str) -> str | None:
        self.person_calls.append((tenant, login))
        return self.persons.get(login.casefold())

    def recall_run(self, tenant: str, run_key: str):
        self.recall_calls.append((tenant, run_key))
        return self.runs.get(run_key)

    def node_links(self, tenant: str, keys: list[str]) -> dict[str, str]:
        self.link_calls.append((tenant, list(keys)))
        return {key: self.links[key] for key in keys if key in self.links}


class FakeExplain:
    """The injected ``explain_path`` seam; records (tenant, from_key, to_key)."""

    def __init__(self, path: schemas.PathData | None = None):
        self.path = path if path is not None else schemas.PathData(keys=[], rels=[])
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, tenant: str, from_key: str, to_key: str) -> schemas.ToolResult:
        self.calls.append((tenant, from_key, to_key))
        return schemas.ToolResult(
            tool="explain_path",
            data=self.path.model_dump(),
            cypher=["explain_path.cypher"],
            params={"tenant": tenant, "from": from_key, "to": to_key},
            evidence=list(self.path.keys),
            ms=1,
            empty=not self.path.keys,
        )


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
    def __init__(self, public_base_url="https://keystone.example"):
        self.calls: list[tuple] = []
        self.public_base_url = public_base_url

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


def make_lane(
    *,
    run=None,
    writer=None,
    feedback=None,
    triage=None,
    apply=None,
    explain=None,
    store=None,
    bot_login=None,
    links=None,
):
    return CommentCommandLane(
        store=store if store is not None else FakeStore(run=run, links=links),
        writer=writer if writer is not None else FakeWriter(),
        record_feedback=feedback if feedback is not None else FakeFeedback(),
        triage=triage if triage is not None else make_triage(),
        apply_service=apply if apply is not None else FakeApply(),
        tenant=TENANT,
        explain=explain if explain is not None else FakeExplain(),
        bot_login=bot_login,
    )


def test_reject_apply_record_retry_reuses_persisted_run():
    from api.actions import ActionStoreError
    from webhook_retry_support import RetryHarness

    harness = RetryHarness()
    lane = make_lane(run=run_data(recorded_assign("alice")), writer=harness.writer,
                     triage=harness.triage, apply=harness.service)
    try:
        with pytest.raises(ActionStoreError):
            lane(make_job())
        assert len(harness.comments) == 2  # confirmation plus accepted run comment
        lane(make_job(delivery_id="redelivery", tenant="untrusted"))
        assert len(harness.runs) == 1
        assert len(harness.comments) == 2
        assert harness.record_attempts == [(TENANT, RUN_KEY), (TENANT, RUN_KEY)]
        assert harness.recorded[0][2][0].status == "applied"
        lane(make_job(delivery_id="another", comment_id=556))
        lane(make_job(issue_number=43, comment_id=557))
        assert [(t, i) for t, i, _ in harness.runs] == [
            (TENANT, ISSUE_KEY), (TENANT, ISSUE_KEY),
            (TENANT, f"issue:{SANDBOX}#43"),
        ]
        assert len(harness.comments) == 6
    finally:
        harness.client.close()


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


@pytest.mark.parametrize("persisted", [False, True])
def test_feedback_retry_pins_original_target_even_when_latest_changes(persisted):
    original = run_data(recorded_assign("alice"))
    store = FakeStore(run=original)
    writer = FakeWriter()
    triage_calls = []
    attempts = []

    def feedback(tenant, run_key, request, *, given_by):
        action = next(a for a in store.runs[run_key].actions if a.action_key == request.action_key)
        attempts.append((tenant, run_key, request.action_key, action.module, given_by))
        result = schemas.FeedbackResult(
            feedback_key="feedback:persisted", action_key=request.action_key,
            verdict="reject", rule_key=EXISTING_RULE_KEY,
        )
        if len(attempts) == 1:
            if persisted:
                action.feedback.append(result)
            raise RuntimeError("feedback response lost")
        return result

    lane = make_lane(store=store, writer=writer, feedback=feedback, triage=make_triage(triage_calls))
    with pytest.raises(RuntimeError, match="feedback response lost"):
        lane(make_job())
    assert writer.comments == [] and triage_calls == []
    newer = run_data(recorded_assign("alice", module="module:different"))
    newer.run.run_key = NEW_RUN_KEY
    newer.actions[0].action_key = "action:newer-alice"
    store.run = newer
    store.runs[NEW_RUN_KEY] = newer

    # A retry must authorize again before reading either reserved or latest state.
    writer.collaborator = False
    lane(make_job(delivery_id="unauthorized"))
    assert store.recall_calls == [] and len(attempts) == 1
    writer.collaborator = True
    lane(make_job(delivery_id="retry", tenant="untrusted"))

    expected_attempt = (TENANT, RUN_KEY, "action:assign-alice", MODULE, "maintainer")
    assert attempts == [expected_attempt] * (1 if persisted else 2)
    assert store.latest_calls == [(TENANT, ISSUE_KEY)]
    assert store.recall_calls == [(TENANT, RUN_KEY)]
    assert writer.collaborator_calls == ["maintainer"] * 3
    assert len(writer.comments) == 1 and EXISTING_RULE_KEY in writer.comments[0][1]
    assert triage_calls == [(TENANT, ISSUE_KEY)]


@pytest.mark.parametrize("change", [
    "missing-run", "missing-action", "ambiguous-action", "module", "login", "kind", "issue", "run",
])
def test_feedback_retry_fails_closed_when_exact_target_changes(change):
    original = run_data(recorded_assign("alice"))
    store = FakeStore(run=original)
    writer = FakeWriter()
    feedback = FakeFeedback(error=RuntimeError("feedback failed"))
    triage_calls = []
    lane = make_lane(store=store, writer=writer, feedback=feedback, triage=make_triage(triage_calls))
    with pytest.raises(RuntimeError, match="feedback failed"):
        lane(make_job())
    if change == "missing-run":
        store.runs.clear()
    elif change == "missing-action":
        original.actions.clear()
    elif change == "ambiguous-action":
        original.actions.append(original.actions[0].model_copy(deep=True))
    elif change == "module":
        original.actions[0].module = "module:changed"
    elif change == "login":
        original.actions[0].value = "bob"
    elif change == "kind":
        original.actions[0].kind = "label"
    elif change == "issue":
        original.run.issue_key = "issue:other#1"
    else:
        original.run.run_key = NEW_RUN_KEY
    store.run = run_data(recorded_assign("alice", module="module:new"))
    feedback.error = None

    with pytest.raises(RuntimeError, match="reserved reject target"):
        lane(make_job(delivery_id="retry"))
    assert len(feedback.calls) == 1
    assert store.latest_calls == [(TENANT, ISSUE_KEY)]
    assert store.recall_calls == [(TENANT, RUN_KEY)]
    assert writer.comments == [] and triage_calls == []


def test_failed_feedback_reservations_are_bounded_and_evict_fifo(monkeypatch):
    monkeypatch.setattr("api.comment_commands.RESOLVED_LIMIT", 2)
    store = FakeStore(run=run_data(recorded_assign("alice")))
    lane = make_lane(store=store, feedback=FakeFeedback(error=RuntimeError("failed")))
    for comment_id in (1, 2, 3, 2):
        with pytest.raises(RuntimeError, match="failed"):
            lane(make_job(comment_id=comment_id))
    assert len(lane._resolved) == 2
    assert store.latest_calls == [(TENANT, ISSUE_KEY)] * 3
    assert store.recall_calls == [(TENANT, RUN_KEY)]
    with pytest.raises(RuntimeError, match="failed"):
        lane(make_job(comment_id=1))
    assert len(store.latest_calls) == 4  # evicted reservations have no retry guarantee


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


# --- the why lane -----------------------------------------------------------


def why_path(*keys, rels=None):
    return schemas.PathData(keys=list(keys), rels=list(rels or []))


def test_collaborator_why_explains_the_trusted_path_with_links_and_run_url():
    path = why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"])
    store = FakeStore(run=run_data(), links={ISSUE_KEY: ISSUE_URL, PERSON_KEY: PERSON_URL})
    explain = FakeExplain(path)
    writer = FakeWriter(collaborator=True)
    triage_calls: list = []
    apply = FakeApply()
    lane = make_lane(store=store, writer=writer, explain=explain, triage=make_triage(triage_calls), apply=apply)

    lane(make_job(comment_body="/keystone why alice"))

    # The trusted incoming issue key is the path source; the person key is the target.
    assert explain.calls == [(TENANT, ISSUE_KEY, PERSON_KEY)]
    assert store.latest_calls == [(TENANT, ISSUE_KEY)]
    # Only the returned keys' stored URLs are resolved.
    assert store.link_calls == [(TENANT, [ISSUE_KEY, PERSON_KEY])]
    (number, body), = writer.comments
    assert number == 42
    assert why_marker(COMMENT_ID) in body
    assert body.index(ISSUE_KEY) < body.index("AUTHORED") < body.index(PERSON_KEY)
    assert ISSUE_URL in body and PERSON_URL in body
    assert RUN_URL in body
    # A why reply never re-triages or writes actions.
    assert triage_calls == [] and apply.calls == []


def test_why_resolves_a_mixed_case_login_to_the_stored_canonical_key():
    path = why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"])
    # The graph stores person:alice; the command's mixed case must still reach it.
    store = FakeStore(run=run_data(), persons={"alice": PERSON_KEY})
    explain = FakeExplain(path)
    writer = FakeWriter()
    lane = make_lane(store=store, writer=writer, explain=explain)

    lane(make_job(comment_body="/keystone why Alice"))

    assert store.person_calls == [(TENANT, "Alice")]
    assert explain.calls == [(TENANT, ISSUE_KEY, PERSON_KEY)]


def test_why_unknown_login_still_queries_the_literal_person_key():
    store = FakeStore(run=run_data(), persons={})
    explain = FakeExplain(why_path())
    writer = FakeWriter()
    lane = make_lane(store=store, writer=writer, explain=explain)

    lane(make_job(comment_body="/keystone why carol"))

    # No canonical key exists, so the literal (honestly absent) key is queried.
    assert store.person_calls == [(TENANT, "carol")]
    assert explain.calls == [(TENANT, ISSUE_KEY, "person:carol")]


def test_why_only_resolves_the_returned_keys_and_never_synthesizes_a_source_url():
    path = why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"])
    store = FakeStore(run=run_data(), links={})  # no stored URL for either key
    explain = FakeExplain(path)
    writer = FakeWriter()
    lane = make_lane(store=store, writer=writer, explain=explain)

    lane(make_job(comment_body="/keystone why alice"))

    assert store.link_calls == [(TENANT, [ISSUE_KEY, PERSON_KEY])]
    (_, body), = writer.comments
    assert "https://github.com" not in body
    assert ISSUE_KEY in body and PERSON_KEY in body


def test_why_empty_path_is_explained_as_absent_without_a_link_query():
    store = FakeStore(run=run_data())
    explain = FakeExplain(why_path())
    writer = FakeWriter()
    lane = make_lane(store=store, writer=writer, explain=explain)

    lane(make_job(comment_body="/keystone why alice"))

    assert explain.calls == [(TENANT, ISSUE_KEY, PERSON_KEY)]
    assert store.link_calls == []
    (_, body), = writer.comments
    assert "evidence" in body.lower() and "chain" in body.lower()
    assert "AUTHORED" not in body


def test_why_without_a_run_replies_explicitly_and_runs_no_path_query():
    store = FakeStore(run=None)
    explain = FakeExplain(why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"]))
    writer = FakeWriter()
    lane = make_lane(store=store, writer=writer, explain=explain)

    lane(make_job(comment_body="/keystone why alice"))

    assert explain.calls == []
    assert store.link_calls == []
    (_, body), = writer.comments
    assert why_marker(COMMENT_ID) in body
    assert "no handled triage run" in body.lower()
    assert "@" not in body


def test_why_non_collaborator_gets_no_graph_query_and_no_reply():
    store = FakeStore(run=run_data())
    explain = FakeExplain(why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"]))
    writer = FakeWriter(collaborator=False)
    lane = make_lane(store=store, writer=writer, explain=explain)

    lane(make_job(comment_body="/keystone why alice"))

    assert writer.collaborator_calls == ["maintainer"]
    assert writer.comments == []
    assert store.latest_calls == [] and store.link_calls == [] and explain.calls == []


def test_why_from_a_bot_gets_no_graph_query_and_no_reply():
    store = FakeStore(run=run_data())
    explain = FakeExplain(why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"]))
    # Even a sender that would be a collaborator is ignored when it is the bot.
    writer = FakeWriter(collaborator=True)
    lane = make_lane(store=store, writer=writer, explain=explain, bot_login="keystone-bot")

    lane(make_job(comment_body="/keystone why alice", sender_login="keystone-bot"))

    assert writer.collaborator_calls == []
    assert writer.comments == []
    assert store.latest_calls == [] and explain.calls == []


def test_why_collaborator_lookup_failure_fails_closed():
    store = FakeStore(run=run_data())
    explain = FakeExplain(why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"]))
    writer = FakeWriter(error=GitHubWriteError("sandbox collaborator lookup failed with status 403"))
    lane = make_lane(store=store, writer=writer, explain=explain)

    with pytest.raises(GitHubWriteError):
        lane(make_job(comment_body="/keystone why alice"))

    assert writer.comments == []
    assert store.latest_calls == [] and explain.calls == []


def test_why_marker_lookup_prevents_a_duplicate_reply():
    path = why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"])
    store = FakeStore(run=run_data(), links={ISSUE_KEY: ISSUE_URL})
    explain = FakeExplain(path)
    writer = FakeWriter(existing_markers=[why_marker(COMMENT_ID)])
    lane = make_lane(store=store, writer=writer, explain=explain)

    lane(make_job(comment_body="/keystone why alice"))

    assert explain.calls == [(TENANT, ISSUE_KEY, PERSON_KEY)]
    assert writer.comments == []


def test_why_reply_has_no_live_mention_or_upstream_backlink():
    path = why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"])
    store = FakeStore(run=run_data(), links={ISSUE_KEY: ISSUE_URL, PERSON_KEY: PERSON_URL})
    writer = FakeWriter()
    lane = make_lane(store=store, writer=writer, explain=FakeExplain(path))

    lane(make_job(comment_body="/keystone why @alice"))

    (_, body), = writer.comments
    assert "@alice" not in body and "@" not in body
    assert "](http" not in body
    assert f"]({ISSUE_URL})" not in body
    assert code_span(ISSUE_URL) in body


def test_why_without_a_public_base_url_omits_the_run_line():
    store = FakeStore(run=run_data())
    explain = FakeExplain(why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"]))
    writer = FakeWriter()
    lane = make_lane(store=store, writer=writer, explain=explain, apply=FakeApply(public_base_url=None))

    lane(make_job(comment_body="/keystone why alice"))

    (_, body), = writer.comments
    assert "Run:" not in body
    assert ISSUE_KEY in body


def test_why_does_not_require_a_feedback_service():
    store = FakeStore(run=run_data())
    explain = FakeExplain(why_path(ISSUE_KEY, PERSON_KEY, rels=["AUTHORED"]))
    writer = FakeWriter()
    # A missing A11 seam (record_feedback=None) must not block the read-only why lane.
    lane = CommentCommandLane(
        store=store,
        writer=writer,
        record_feedback=None,
        triage=make_triage(),
        apply_service=FakeApply(),
        tenant=TENANT,
        explain=explain,
    )

    lane(make_job(comment_body="/keystone why alice"))

    assert explain.calls == [(TENANT, ISSUE_KEY, PERSON_KEY)]
    assert len(writer.comments) == 1


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
