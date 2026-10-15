"""Guarded, recorded sandbox triage action execution (B6 Task 2, spec section 7).

``apply_triage`` loads a trusted tenant/run context, validates that the handled
issue is an ``incoming`` issue in the configured ``SANDBOX_REPO``, then plans
(dry run) or executes (live) the persisted proposed actions through a
sandbox-bound :class:`~api.github.SandboxGitHub` writer. Every outcome is a
:class:`~api.schemas.ActionResult` recorded through A's ``record_actions.cypher``
executor.

A owns the run-context lookup and the record executor; this module only defines
the :class:`ActionStore` seam they plug into. A fixture run is never eligible
for live writes. No target repo or issue number is ever read from a comment URL,
browser payload or ``REPLAY_OF``: the sandbox target comes only from validated
configuration and the stored issue identity.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from api import schemas
from api.github import GitHubWriteError, SandboxGitHub, validate_repo
from api.github_text import AmbiguousMarkdown, code_span, neutralize

# Stable per-run marker embedded in the posted comment so a retry can detect an
# already-posted run comment without reposting it.
COMMENT_MARKER_PREFIX = "keystone-apply"

DRY_LABEL = "Dry run: label would be created if missing and attached in the sandbox."
DRY_ASSIGN = "Dry run: assignment not attempted; collaborator eligibility is pending."
DRY_COMMENT = "Dry run: comment not posted."
DRY_COMMENT_FALLBACK = (
    "Dry run: comment not posted; the composed comment has ambiguous code delimiters, "
    "so the fallback comment would be posted."
)
POSTED_COMMENT = "Posted the triage comment in the sandbox."
POSTED_FALLBACK = (
    "Posted the fallback comment in the sandbox; the composed comment has ambiguous "
    "code delimiters."
)
SKIP_APPLIED = "Already applied; skipped without reapplying."
SKIP_REJECTED = "Previously rejected; not reapplied."
REFUSED_INVALID = "refused: only incoming issues in the configured sandbox repo can be applied"
REFUSED_NO_WRITER = "refused: no sandbox write credential is configured"
REFUSED_UNSAFE_COMMENT = "refused: the comment could not be made safe to post; not posted"

# A sandbox write can fail at the transport layer (timeout, connection error) or
# return an unreadable body; neither is a ``GitHubWriteError``. They must still
# become a per-action ``refused`` outcome rather than escaping and aborting the
# whole apply, so the same handlers catch all three.
_WRITE_FAILURES = (GitHubWriteError, httpx.HTTPError, json.JSONDecodeError)

_ISSUE_PREFIX = "issue:"

# In-process serialization of same tenant/run applies. B7's single worker remains
# the concurrency model; cross-process exactly-once is not promised by B6.
_APPLY_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_APPLY_LOCKS_GUARD = threading.Lock()


class ActionError(RuntimeError):
    """Base class for apply-service errors. Messages never contain a token."""


class ActionContextNotFound(ActionError):
    """No trusted run context exists for the tenant/run (route maps to 404)."""


class ActionServiceUnavailable(ActionError):
    """The live apply service is not available in this configuration (503)."""


class ActionStoreError(ActionError):
    """Action outcomes could not be recorded; the caller must not report success."""


class UnsafeCommentError(ActionError):
    """A comment still contains user/team/broadcast mention syntax."""


@dataclass(frozen=True)
class ApplyContext:
    """Internal, immutable run context supplied by A's lookup (not the request)."""

    run_key: str
    issue_key: str
    repo: str
    issue_number: int
    incoming: bool
    actions: list[schemas.RecordedAction]
    comment_markdown: str


@runtime_checkable
class ActionStore(Protocol):
    """Adapter seam over A's run-context lookup and ``record_actions.cypher``."""

    def load_context(self, tenant: str, run_key: str) -> ApplyContext: ...

    def record_results(
        self, tenant: str, run_key: str, results: list[schemas.ActionResult]
    ) -> None: ...


def normalize_repo(repo: str) -> str:
    """Return a validated, case-normalized ``owner/name``."""
    return validate_repo(repo).lower()


def parse_issue_key(key: str) -> tuple[str, int] | None:
    """Parse ``issue:<owner>/<name>#<n>`` into ``(normalized owner/name, n)``."""
    if not isinstance(key, str) or not key.startswith(_ISSUE_PREFIX):
        return None
    repo, sep, number = key[len(_ISSUE_PREFIX):].rpartition("#")
    if not sep or not number.isdigit():
        return None
    try:
        repo = validate_repo(repo)
    except GitHubWriteError:
        return None
    return repo.lower(), int(number)


def comment_marker(run_key: str) -> str:
    """Return the stable, per-run marker embedded in the run comment."""
    return f"<!-- {COMMENT_MARKER_PREFIX}:{run_key} -->"


def run_uuid(run_key: str) -> str:
    """Return the run UUID portion of a canonical ``run:<uuid>`` key."""
    if isinstance(run_key, str) and run_key.startswith("run:"):
        return run_key[len("run:"):]
    return run_key


def validate_apply_context(context: ApplyContext, sandbox_repo: str) -> str | None:
    """Return ``None`` when the context is eligible, else a sanitized refusal note.

    Repo comparison is case-normalized and validated; the stored issue key and
    own number must agree with the context; ``incoming`` must be exactly true.
    """
    try:
        expected = normalize_repo(sandbox_repo)
    except GitHubWriteError:
        return REFUSED_INVALID
    if context.incoming is not True:
        return REFUSED_INVALID
    if not isinstance(context.issue_number, int) or isinstance(context.issue_number, bool):
        return REFUSED_INVALID
    if context.issue_number <= 0:
        return REFUSED_INVALID
    try:
        repo = normalize_repo(context.repo)
    except GitHubWriteError:
        return REFUSED_INVALID
    if repo != expected:
        return REFUSED_INVALID
    parsed = parse_issue_key(context.issue_key)
    if parsed is None:
        return REFUSED_INVALID
    key_repo, key_number = parsed
    if key_repo != expected or key_number != context.issue_number:
        return REFUSED_INVALID
    return None


def sanitize_comment(markdown: str, logins: list[str]) -> str:
    """Return the comment with no live mention or cross-reference (spec section 7).

    Every ``@login`` or ``@org/team`` outside code becomes a code span without
    the ``@`` (`` `login` ``), and an existing `` `@login` `` for one of
    ``logins`` is normalized the same way. Cross-references and GitHub issue,
    pull request and discussion URLs become code spans; code is left alone
    (``api.github_text.neutralize``). Raises :class:`UnsafeCommentError` only
    when code delimiters are too ambiguous to rewrite safely.
    """
    if not isinstance(markdown, str):
        raise UnsafeCommentError("comment must be a string")
    try:
        return neutralize(markdown, logins)
    except AmbiguousMarkdown as exc:
        raise UnsafeCommentError(f"comment not safe to rewrite: {exc}") from None


def fallback_comment(context: ApplyContext) -> str:
    """A comment built only from the run's own actions, posted when the composed one is unsafe.

    Labels and reviewer logins are single-line code spans without ``@``;
    rejected actions are left out.
    """

    def listed(kind: str) -> str:
        values = []
        for action in context.actions:
            if action.kind == kind and action.status != "rejected":
                value = " ".join(action.value.split())
                values.append(value.lstrip("@") if kind == "assign" else value)
        return ", ".join(code_span(v) for v in dict.fromkeys(values) if v) or "none"

    return (
        "Keystone triaged this issue. The composed comment was withheld because its "
        "formatting could not be checked safely.\n\n"
        f"- Labels: {listed('label')}\n"
        f"- Suggested reviewers: {listed('assign')}"
    )


def _lock_for(tenant: str, run_key: str) -> threading.Lock:
    with _APPLY_LOCKS_GUARD:
        return _APPLY_LOCKS.setdefault((tenant, run_key), threading.Lock())


def _write_failure_note(op: str, exc: Exception) -> str:
    """Return a sanitized per-action refusal note for a failed sandbox write.

    ``GitHubWriteError`` messages are authored here (status codes only), so they
    are safe to echo. Raw transport/decoding text is never echoed; only the
    exception class name is included.
    """
    if isinstance(exc, GitHubWriteError):
        return f"refused: sandbox {op} failed ({exc})"
    return f"refused: sandbox {op} failed (transport or response error: {type(exc).__name__})"


class ActionService:
    """Guarded, recorded action execution configured by app/service construction.

    ``store`` is A's run-context adapter; ``writer`` is the sandbox-bound writer
    (``None`` when no write credential is configured, which still allows dry
    runs). ``api_mode`` defaults to ``fixtures`` so a misconfigured service
    fails closed rather than executing live writes.
    """

    def __init__(
        self,
        store: ActionStore,
        *,
        sandbox_repo: str,
        writer: SandboxGitHub | None = None,
        api_mode: str = "fixtures",
        public_base_url: str | None = None,
    ):
        self.store = store
        self.sandbox_repo = sandbox_repo
        self.writer = writer
        self.api_mode = api_mode
        self.public_base_url = public_base_url
        if writer is not None:
            # ``writer.repo`` is an independent construction input; if it does
            # not name the same sandbox that context validation compares
            # against, a context could pass validation for repo A while writes
            # land in repo B. Fail closed instead of writing to the wrong repo.
            try:
                writer_repo = normalize_repo(writer.repo)
                configured_repo = normalize_repo(sandbox_repo)
            except GitHubWriteError:
                raise ActionError("sandbox repo configuration is invalid") from None
            if writer_repo != configured_repo:
                raise ActionError(
                    "sandbox writer is bound to a different repo than the configured sandbox"
                )

    def apply_triage(
        self, tenant: str, run_key: str, dry_run: bool = True
    ) -> schemas.ApplyResult:
        """Plan (``dry_run``) or execute the run's proposed sandbox actions."""
        if self.api_mode != "live":
            raise ActionServiceUnavailable("apply service is only available in live API mode")
        if not isinstance(dry_run, bool):
            raise ActionError("dry_run must be a boolean")

        with _lock_for(tenant, run_key):
            context = self.store.load_context(tenant, run_key)
            if not isinstance(context, ApplyContext) or context.run_key != run_key:
                raise ActionContextNotFound("run context does not match the requested run")
            refusal = validate_apply_context(context, self.sandbox_repo)
            results, recorded = self._execute(tenant, context, dry_run, refusal)
            if recorded:
                try:
                    self.store.record_results(tenant, run_key, recorded)
                except Exception as exc:  # noqa: BLE001 - sanitized at the boundary
                    raise ActionStoreError("action results could not be recorded") from exc
            return schemas.ApplyResult(run_key=run_key, dry_run=dry_run, actions=results)

    # -- planning / execution ------------------------------------------------

    def _execute(
        self, tenant: str, context: ApplyContext, dry_run: bool, refusal: str | None
    ) -> tuple[list[schemas.ActionResult], list[schemas.ActionResult]]:
        results: list[schemas.ActionResult] = []
        recorded: list[schemas.ActionResult] = []
        seen_keys: set[str] = set()
        seen_comment = False

        for action in context.actions:
            key = action.action_key
            if key in seen_keys:
                result = self._result(action, "refused", "refused: duplicate action key in the run context")
                results.append(result)
                recorded.append(result)
                continue
            seen_keys.add(key)

            if action.status == "applied":
                results.append(self._result(action, "applied", SKIP_APPLIED))
                continue
            if action.status == "rejected":
                # Never persist a refusal over a stored rejected state.
                results.append(self._result(action, "refused", SKIP_REJECTED))
                continue

            if refusal is not None:
                result = self._result(action, "refused", refusal)
                results.append(result)
                recorded.append(result)
                continue

            if action.kind == "comment":
                if seen_comment:
                    result = self._result(action, "refused", "refused: only one run comment is posted")
                else:
                    seen_comment = True
                    result = self._comment(tenant, action, context, dry_run)
            elif action.kind == "label":
                result = self._label(action, context, dry_run)
            elif action.kind == "assign":
                result = self._assign(action, context, dry_run)
            else:
                result = self._result(action, "refused", "refused: unsupported action kind")

            results.append(result)
            recorded.append(result)

        return results, recorded

    def _label(
        self, action: schemas.RecordedAction, context: ApplyContext, dry_run: bool
    ) -> schemas.ActionResult:
        name = action.value.strip()
        if not name:
            return self._result(action, "refused", "refused: label name is blank")
        if dry_run:
            return self._result(action, "dry_run", DRY_LABEL)
        if self.writer is None:
            return self._result(action, "refused", REFUSED_NO_WRITER)
        try:
            self.writer.ensure_label(name)
            self.writer.add_labels(context.issue_number, [name])
        except _WRITE_FAILURES as exc:
            return self._result(action, "refused", _write_failure_note("label update", exc))
        return self._result(action, "applied", f"Label `{name}` ensured and attached in the sandbox.")

    def _assign(
        self, action: schemas.RecordedAction, context: ApplyContext, dry_run: bool
    ) -> schemas.ActionResult:
        login = action.value.strip().lstrip("@")
        if not login:
            return self._result(action, "refused", "refused: assignee login is blank")
        if dry_run:
            return self._result(action, "dry_run", DRY_ASSIGN)
        if self.writer is None:
            return self._result(action, "refused", REFUSED_NO_WRITER)
        try:
            if not self.writer.is_collaborator(login):
                return self._result(
                    action,
                    "dry_run",
                    f"`{login}` is not a sandbox collaborator; kept as a suggestion in the comment.",
                )
            self.writer.assign(context.issue_number, login)
        except _WRITE_FAILURES as exc:
            return self._result(action, "refused", _write_failure_note("assignment", exc))
        return self._result(action, "applied", f"Assigned `{login}` in the sandbox.")

    def _comment(
        self,
        tenant: str,
        action: schemas.RecordedAction,
        context: ApplyContext,
        dry_run: bool,
    ) -> schemas.ActionResult:
        fallback = False
        try:
            body = self._build_comment(tenant, context, context.comment_markdown)
        except UnsafeCommentError:
            # Post the run's own labels and reviewers rather than nothing.
            try:
                body = self._build_comment(tenant, context, fallback_comment(context))
            except UnsafeCommentError:
                return self._result(action, "refused", REFUSED_UNSAFE_COMMENT)
            fallback = True
        if dry_run:
            return self._result(action, "dry_run", DRY_COMMENT_FALLBACK if fallback else DRY_COMMENT)
        if self.writer is None:
            return self._result(action, "refused", REFUSED_NO_WRITER)
        marker = comment_marker(context.run_key)
        try:
            if self.writer.find_comment(context.issue_number, marker):
                return self._result(action, "applied", "Run comment already posted; not reposted.")
            self.writer.comment(context.issue_number, body)
        except _WRITE_FAILURES as exc:
            return self._result(action, "refused", _write_failure_note("comment", exc))
        return self._result(action, "applied", POSTED_FALLBACK if fallback else POSTED_COMMENT)

    def _build_comment(self, tenant: str, context: ApplyContext, markdown: str) -> str:
        logins = [
            a.value.strip().lstrip("@")
            for a in context.actions
            if a.kind == "assign" and a.value.strip()
        ]
        parts = [sanitize_comment(markdown, logins).rstrip()]
        if self.public_base_url:
            base = self.public_base_url.rstrip("/")
            parts.append(f"Run: {base}/t/{tenant}?run={run_uuid(context.run_key)}")
        parts.append(comment_marker(context.run_key))
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _result(
        action: schemas.RecordedAction, status: str, note: str
    ) -> schemas.ActionResult:
        return schemas.ActionResult(
            action_key=action.action_key,
            kind=action.kind,
            value=action.value,
            status=status,
            note=note,
        )
