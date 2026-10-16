"""B8 collaborator comment commands: strict parsing, safe replies and the reject lane.

Only a strict *leading* command line counts::

    /keystone reject <login> <reason>
    /keystone why <login>

An ordinary comment, a quoted/fenced/incidental mention, or a reject with no
reason is ignored (``None``). A reject's reason is the entire remaining text after
the login, so a multiline reason is preserved. Logins compare case-insensitively
and one optional leading ``@`` is stripped.

The :class:`CommentCommandLane` handles ``issue_comment`` jobs on the webhook
worker thread. It verifies the *actual* comment sender with
``SandboxGitHub.is_collaborator`` (never ``author_association``), resolves the
latest HANDLED run for the exact incoming issue, finds exactly one matching
``assign`` action that carries a module, records a module-scoped reject through
the injected A11 ``record_feedback`` seam, confirms only a persisted returned rule
key with a safe marker (checked with ``find_comment`` before posting), then
re-triages and live-applies. The tenant is fixed at construction (R7): no action,
run, module or tenant is ever read from comment text.

Idempotence is bounded to this process. A duplicate delivery ID or comment ID is
dropped by the worker; a same-process retry reuses the resolved run/action/rule
identity (or reloads the action's existing reject feedback) instead of creating a
second rule. Cross-process exactly-once is not promised: a crash between the
feedback commit and the re-triage is a documented limitation of this in-memory
worker. No automatic remove-assignee operation is added.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from api import schemas
from api.actions import ActionService, UnsafeCommentError, sanitize_comment
from api.github import SandboxGitHub
from api.github_text import code_span
from api.webhook_store import WebhookStore
from api.webhooks import WebhookJob

log = logging.getLogger(__name__)

COMMAND = "/keystone"
REJECT_MARKER_PREFIX = "keystone-reject"
NO_RULE_MARKER_PREFIX = "keystone-reject-none"
# Bound the same-process resolved-rejection memory; the worker itself is one thread.
RESOLVED_LIMIT = 10_000

_COMMAND = re.compile(r"[ \t]*/keystone[ \t]+(?P<sub>reject|why)\b(?P<rest>.*)")
_LOGIN = re.compile(r"@?([A-Za-z0-9][A-Za-z0-9-]*)")

TriageFn = Callable[[str, str], Awaitable[schemas.TriageResult]]
RecordFeedbackFn = Callable[..., schemas.FeedbackResult]


@dataclass(frozen=True)
class RejectCommand:
    login: str
    reason: str


@dataclass(frozen=True)
class WhyCommand:
    login: str


def parse_command(body: str) -> RejectCommand | WhyCommand | None:
    """Parse a strict leading ``/keystone`` command, or ``None`` to ignore the comment."""
    if not isinstance(body, str):
        return None
    lines = body.splitlines()
    if not lines:
        return None
    match = _COMMAND.fullmatch(lines[0])
    if match is None:
        return None
    if match.group("sub") == "why":
        return _parse_why(match.group("rest"), lines[1:])
    return _parse_reject(match.group("rest"), lines[1:])


def _parse_why(rest: str, extra_lines: list[str]) -> WhyCommand | None:
    tokens = rest.split()
    if len(tokens) != 1:
        return None
    login = _login(tokens[0])
    if login is None or any(line.strip() for line in extra_lines):
        return None
    return WhyCommand(login=login)


def _parse_reject(rest: str, extra_lines: list[str]) -> RejectCommand | None:
    parts = rest.split(None, 1)
    if not parts:
        return None
    login = _login(parts[0])
    if login is None:
        return None
    first = parts[1] if len(parts) > 1 else ""
    # The reason is the entire remaining text: a multiline reason is preserved,
    # and a missing/blank reason is ignored rather than guessed.
    reason = "\n".join([first, *extra_lines]).strip()
    if not reason:
        return None
    return RejectCommand(login=login, reason=reason)


def _login(token: str) -> str | None:
    match = _LOGIN.fullmatch(token)
    return match.group(1) if match is not None else None


def reject_marker(comment_id: int) -> str:
    """The stable marker for a reject confirmation, derived from the GitHub comment ID."""
    return f"<!-- {REJECT_MARKER_PREFIX}:{comment_id} -->"


def no_rule_marker(comment_id: int) -> str:
    """The stable marker for a "no rule" reply; distinct so it never hides a later confirmation."""
    return f"<!-- {NO_RULE_MARKER_PREFIX}:{comment_id} -->"


def format_reject_confirmation(*, login: str, reason: str, rule_key: str, comment_id: int) -> str:
    """A safe confirmation of a persisted reject rule: no live mention, stable marker."""
    return "\n".join(
        [
            f"Keystone recorded a rejection for {code_span(login)} and will avoid recommending "
            "them for the affected module.",
            "",
            f"- Rule: {code_span(rule_key)}",
            f"- Reason: {_safe_reason(reason, login)}",
            "",
            reject_marker(comment_id),
        ]
    )


def format_no_rule(*, login: str, comment_id: int) -> str:
    """A safe explanatory reply when no single module-scoped assign matches the login."""
    return "\n".join(
        [
            f"Keystone could not resolve a single module-scoped suggestion for {code_span(login)} "
            "in the latest triage run, so no rule was created.",
            "",
            no_rule_marker(comment_id),
        ]
    )


def _safe_reason(reason: str, login: str) -> str:
    try:
        return sanitize_comment(reason, [login])
    except UnsafeCommentError:
        # Never drop the confirmation for an unrewritable reason: show it as one code span.
        return code_span(" ".join(reason.split()))


def _same_login(value: str, login: str) -> bool:
    return value.strip().lstrip("@").casefold() == login.casefold()


def _single_assign(actions: list[schemas.RecordedAction], login: str) -> schemas.RecordedAction | None:
    matches = [a for a in actions if a.kind == "assign" and _same_login(a.value, login)]
    return matches[0] if len(matches) == 1 else None


def _existing_rejection_rule(action: schemas.RecordedAction) -> str | None:
    for feedback in action.feedback:
        if feedback.verdict == "reject" and feedback.rule_key:
            return feedback.rule_key
    return None


class CommentCommandLane:
    """The sync ``issue_comment`` runner: authorize, resolve, record, confirm, re-triage.

    ``record_feedback`` is A11's injected seam (``None`` when A11 is absent, so
    reject commands are ignored rather than faked); ``writer`` is the sandbox-bound
    writer. Every tenant-scoped call uses the construction-time ``tenant``.
    """

    def __init__(
        self,
        *,
        store: WebhookStore,
        writer: SandboxGitHub,
        record_feedback: RecordFeedbackFn | None,
        triage: TriageFn,
        apply_service: ActionService,
        tenant: str,
    ):
        self._store = store
        self._writer = writer
        self._record_feedback = record_feedback
        self._triage = triage
        self._apply_service = apply_service
        self._tenant = tenant
        self._resolved: OrderedDict[tuple[str, int], tuple[str, str, str]] = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, job: WebhookJob) -> None:
        if job.event != "issue_comment" or job.comment_id is None:
            return
        command = parse_command(job.comment_body or "")
        if not isinstance(command, RejectCommand):
            return
        if self._record_feedback is None:
            log.warning("reject command ignored: no feedback service is configured")
            return
        # The actual sender, never author_association; 404 is False (ignore), and
        # a 403/429 lookup raises so the job fails closed and stays retryable.
        if not self._writer.is_collaborator(job.sender_login):
            return
        issue_key = self._store.incoming_issue_key(job.issue_number)
        resolved = self._resolve(job, command, issue_key)
        if resolved is None:
            return
        _, _, rule_key = resolved
        self._confirm(job, command, rule_key)
        self._retriage(job, issue_key)

    # -- resolution ----------------------------------------------------------

    def _resolve(
        self, job: WebhookJob, command: RejectCommand, issue_key: str
    ) -> tuple[str, str, str] | None:
        """Resolve (run_key, action_key, rule_key), reusing this process's prior decision."""
        cache_key = (self._tenant, job.comment_id)
        with self._lock:
            cached = self._resolved.get(cache_key)
        if cached is not None:
            return cached

        run = self._store.latest_run(self._tenant, issue_key)
        action = None if run is None else _single_assign(run.actions, command.login)
        if action is None or not action.module:
            # No run, no single matching assign, or an assign with no module: explain, no rule.
            self._reply_no_rule(job, command.login)
            return None

        rule_key = _existing_rejection_rule(action)
        if rule_key is None:
            result = self._record_feedback(
                self._tenant,
                run.run.run_key,
                schemas.FeedbackRequest(
                    action_key=action.action_key,
                    verdict="reject",
                    reason=command.reason,
                    scope="module",
                ),
                given_by=job.sender_login,
            )
            if result.rule_key is None:
                log.warning(
                    "reject feedback persisted without a rule key: run=%s action=%s",
                    run.run.run_key,
                    action.action_key,
                )
                return None
            rule_key = result.rule_key

        resolved = (run.run.run_key, action.action_key, rule_key)
        with self._lock:
            self._resolved[cache_key] = resolved
            while len(self._resolved) > RESOLVED_LIMIT:
                self._resolved.popitem(last=False)
        return resolved

    # -- replies and re-triage ----------------------------------------------

    def _confirm(self, job: WebhookJob, command: RejectCommand, rule_key: str) -> None:
        marker = reject_marker(job.comment_id)
        if self._writer.find_comment(job.issue_number, marker):
            return
        self._writer.comment(
            job.issue_number,
            format_reject_confirmation(
                login=command.login,
                reason=command.reason,
                rule_key=rule_key,
                comment_id=job.comment_id,
            ),
        )

    def _reply_no_rule(self, job: WebhookJob, login: str) -> None:
        marker = no_rule_marker(job.comment_id)
        if self._writer.find_comment(job.issue_number, marker):
            return
        self._writer.comment(job.issue_number, format_no_rule(login=login, comment_id=job.comment_id))

    def _retriage(self, job: WebhookJob, issue_key: str) -> None:
        result = asyncio.run(self._triage(self._tenant, issue_key))
        applied = self._apply_service.apply_triage(self._tenant, result.run_key, dry_run=False)
        refused = [action.action_key for action in applied.actions if action.status == "refused"]
        if refused:
            log.warning(
                "command re-triage left refused actions: delivery=%s issue=%s run=%s refused=%s",
                job.delivery_id,
                job.issue_number,
                result.run_key,
                ",".join(refused),
            )
