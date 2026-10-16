"""Verified GitHub webhook receiver and serialized worker (B7 Task 1, spec section 13).

The receiver verifies the raw-body ``X-Hub-Signature-256`` HMAC before it reads
anything else, validates the signed JSON into a small frozen :class:`WebhookJob`
that carries no raw user payload, ignores events it must not act on (wrong repo,
the Keystone bot, pull requests, unsupported events/actions) and admits one job
into a single background worker.

``WebhookWorker`` owns one dedicated thread and a bounded FIFO queue so local
embedding, graph writes, A's agent and the sandbox writer never run on the
FastAPI event loop. The job runner is injected: A10/A11 do not exist yet, so with
no real runner configured admission stays disabled (a sanitized 503) rather than
faking live work. A failing job is logged with delivery/issue context and the
thread keeps running. Admission itself deduplicates deliveries, guards an issue
already in flight and applies a 60-second cooldown to ordinary issue triage.

The HMAC secret never appears in a job, a log line or an error; the receiver
never embeds, calls GitHub or runs an agent on the request path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.actions import normalize_repo
from api.config import Settings
from api.github import GitHubWriteError

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

# The single worker queue holds at most this many pending jobs (spec section 13;
# a full queue is a 503 and the same delivery can be retried).
DEFAULT_QUEUE_CAPACITY = 100

# Admission state (spec section 13.3). A completed delivery ID is remembered so a
# retransmission is a no-op; the memory is bounded and pending IDs are kept apart.
COMPLETED_DELIVERY_LIMIT = 10_000
# An ordinary issue job whose issue was triaged this recently is skipped.
ISSUE_COOLDOWN_SECONDS = 60.0
# ``close`` drains the accepted backlog for at most this long, comfortably inside
# the API's ``stop_grace_period`` (docker-compose.yml). The queue is process-local,
# not durable: every job still pending at the deadline is logged by delivery ID so
# the operator can press "Redeliver" in the sandbox repo's webhook settings.
SHUTDOWN_DRAIN_SECONDS = 25.0

WEBHOOK_UNAVAILABLE = "Webhook processing is not available"
INVALID_SIGNATURE = "Invalid signature"
INVALID_PAYLOAD = "Invalid webhook payload"

_SIGNATURE_PREFIX = "sha256="
_NOT_PROCESSED = "not processed before shutdown; redeliver it from the repo's webhook settings"
_STILL_RUNNING = "still running at shutdown; redeliver it if it did not finish"
_EVENT_FAMILIES = frozenset({"issues", "issue_comment"})
_SUPPORTED_EVENTS = frozenset(
    {
        ("issues", "opened"),
        ("issues", "edited"),
        ("issue_comment", "created"),
    }
)


class WebhookIgnored(Exception):
    """A validly signed event this deployment must not act on."""


class WebhookPayloadError(Exception):
    """A validly signed body is not a well-formed supported event."""


@dataclass(frozen=True)
class WebhookJob:
    """The validated, minimal work item admitted to the worker.

    It holds only scalar fields extracted from a signed event; the raw user
    payload and the webhook secret are never stored on it.
    """

    delivery_id: str
    tenant: str
    event: str
    action: str
    issue_number: int
    title: str
    body: str
    issue_state: str
    issue_author_login: str
    issue_author_url: str
    issue_author_is_bot: bool
    issue_created_at: str
    sender_login: str
    comment_id: int | None = None
    comment_body: str | None = None


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """Return whether ``signature`` is GitHub's ``sha256=<hex>`` HMAC of the body.

    Constant-time comparison; a missing/blank secret, a missing signature or any
    other prefix (``sha1=``, bare hex) fails closed.
    """
    if not secret or not isinstance(signature, str):
        return False
    if not signature.startswith(_SIGNATURE_PREFIX):
        return False
    provided = signature[len(_SIGNATURE_PREFIX):]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # ``hmac.compare_digest`` rejects str operands with non-ASCII characters, and
    # ``provided`` is attacker-controlled, so compare bytes: a crafted non-ASCII
    # header must yield False (a 401), never an unhandled TypeError (a 500).
    return hmac.compare_digest(expected.encode("ascii"), provided.encode("utf-8", "surrogateescape"))


def webhook_admission_enabled(settings: Settings) -> bool:
    """Whether this configuration may admit webhook work.

    Exactly one configured tenant is required (one sandbox serves one live
    tenant); zero or multiple tenants, a non-live API mode, or a missing sandbox
    repo/bot login all fail closed.
    """
    return (
        settings.api_mode == "live"
        and len(settings.tenants) == 1
        and bool(settings.sandbox_repo)
        and bool(settings.bot_login)
    )


def build_job(
    settings: Settings,
    payload: dict,
    *,
    event: str | None,
    delivery_id: str | None,
) -> WebhookJob:
    """Validate a signed payload into a :class:`WebhookJob`.

    Raises :class:`WebhookIgnored` for events to skip and
    :class:`WebhookPayloadError` for a malformed supported event.
    """
    if event not in _EVENT_FAMILIES:
        raise WebhookIgnored("unsupported event")
    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        raise WebhookPayloadError("payload is missing an action")
    if (event, action) not in _SUPPORTED_EVENTS:
        raise WebhookIgnored("unsupported action")

    repository = _require_mapping(payload.get("repository"), "repository")
    repo_name = _require_token(repository, "full_name", "repository.full_name")
    try:
        matches_sandbox = normalize_repo(repo_name) == normalize_repo(settings.sandbox_repo)
    except GitHubWriteError:
        # An unparseable repository name can never be the configured sandbox.
        raise WebhookIgnored("repository name is not the configured sandbox") from None
    if not matches_sandbox:
        raise WebhookIgnored("repository is not the configured sandbox")

    sender = _require_mapping(payload.get("sender"), "sender")
    sender_login = _require_token(sender, "login", "sender.login")
    if sender_login.casefold() == settings.bot_login.casefold():
        raise WebhookIgnored("sender is the Keystone bot")

    issue = _require_mapping(payload.get("issue"), "issue")
    if issue.get("pull_request") is not None:
        raise WebhookIgnored("issue is a pull request")

    if not isinstance(delivery_id, str) or not delivery_id.strip():
        raise WebhookPayloadError("missing delivery id")

    number = _require_positive_int(issue, "number", "issue.number")
    title = _require_text(issue, "title", "issue.title")
    state = _require_token(issue, "state", "issue.state")
    created_at = _require_token(issue, "created_at", "issue.created_at")
    body = issue.get("body")
    if body is None:
        body = ""
    elif not isinstance(body, str):
        raise WebhookPayloadError("issue.body must be a string")

    author = _require_mapping(issue.get("user"), "issue.user")
    author_login = _require_token(author, "login", "issue.user.login")
    author_url = _require_token(author, "html_url", "issue.user.html_url")

    comment_id: int | None = None
    comment_body: str | None = None
    if event == "issue_comment":
        comment = _require_mapping(payload.get("comment"), "comment")
        comment_id = _require_positive_int(comment, "id", "comment.id")
        comment_body = _require_text(comment, "body", "comment.body")

    return WebhookJob(
        delivery_id=delivery_id,
        tenant=settings.tenants[0],
        event=event,
        action=action,
        issue_number=number,
        title=title,
        body=body,
        issue_state=state,
        issue_author_login=author_login,
        issue_author_url=author_url,
        issue_author_is_bot=author.get("type") == "Bot",
        issue_created_at=created_at,
        sender_login=sender_login,
        comment_id=comment_id,
        comment_body=comment_body,
    )


def _require_mapping(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise WebhookPayloadError(f"{name} must be an object")
    return value


def _require_text(container: dict, key: str, name: str) -> str:
    value = container.get(key)
    if not isinstance(value, str):
        raise WebhookPayloadError(f"{name} must be a string")
    return value


def _require_token(container: dict, key: str, name: str) -> str:
    value = _require_text(container, key, name)
    if not value.strip():
        raise WebhookPayloadError(f"{name} must not be blank")
    return value


def _require_positive_int(container: dict, key: str, name: str) -> int:
    value = container.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise WebhookPayloadError(f"{name} must be a positive integer")
    return value


class WebhookWorker:
    """One dedicated thread pulling validated jobs from a bounded FIFO queue.

    ``runner`` is the injectable job runner; because A's triage is async it may
    call ``asyncio.run()``, and it must never run on the FastAPI event loop. A
    job the runner rejects is logged with delivery/issue context and dropped, so
    one failure never kills the worker or the jobs after it.

    Admission (all under one lock) deduplicates ``X-GitHub-Delivery``, holds a
    per-issue pending guard and applies a monotonic completion cooldown to
    ordinary issue jobs only (a collaborator command still re-triages; B8). A
    command job is also deduplicated by its comment ID, with the same bounded
    completed / unbounded in-flight retention as deliveries. A delivery is
    reserved only once its queue insertion succeeds, so a full queue leaves it
    free to retry, and a failed job forgets both reservations so a manual
    redelivery can run again. No retry is ever scheduled here.

    ``close`` stops admission and drains the accepted backlog for at most
    ``drain_timeout`` seconds, then releases any clients the lifespan handed in
    through ``closer``. A job still queued at the deadline never starts, and it
    and any job still running are each logged (delivery, event, action, issue;
    never text) for a manual redelivery.
    """

    def __init__(
        self,
        runner: Callable[[WebhookJob], None],
        *,
        maxsize: int = DEFAULT_QUEUE_CAPACITY,
        closer: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        completed_delivery_limit: int = COMPLETED_DELIVERY_LIMIT,
        drain_timeout: float = SHUTDOWN_DRAIN_SECONDS,
    ):
        self._runner = runner
        self._drain_timeout = drain_timeout
        self._closer = closer
        self._clock = clock
        self._completed_delivery_limit = completed_delivery_limit
        self._queue: queue.Queue[WebhookJob | None] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False
        # Set once the shutdown drain deadline passes: no further job starts.
        self._abandoned = False
        self._current: WebhookJob | None = None
        # Deliveries in flight or queued (never bounded) and completed deliveries
        # (bounded, oldest evicted first); ordinary issues in flight and the
        # monotonic time each was last triaged.
        self._pending_deliveries: set[str] = set()
        self._completed_deliveries: OrderedDict[str, None] = OrderedDict()
        self._pending_issues: set[tuple[str, int]] = set()
        self._completed_issues: dict[tuple[str, int], float] = {}
        # Command jobs are deduplicated by comment ID with the same policy as
        # deliveries: in-flight is unbounded, completed is bounded and evicted.
        self._pending_comments: set[tuple[str, int]] = set()
        self._completed_comments: OrderedDict[tuple[str, int], None] = OrderedDict()

    def start(self) -> None:
        """Start the dedicated worker thread (idempotent)."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._closed = False
            self._thread = threading.Thread(target=self._run, name="keystone-webhook", daemon=True)
            self._thread.start()

    def enqueue(self, job: WebhookJob) -> bool:
        """Admit one job; ``False`` only when the worker is closed/not started or full.

        A duplicate delivery, an issue already in flight or an issue inside its
        cooldown is acknowledged (``True``) without admitting new work, so a
        retransmission never produces a second triage.
        """
        ordinary = _is_ordinary_issue(job)
        issue_key = (job.tenant, job.issue_number)
        comment_key = _comment_key(job)
        with self._lock:
            if self._closed or not self._started:
                return False
            if (
                job.delivery_id in self._completed_deliveries
                or job.delivery_id in self._pending_deliveries
            ):
                return True
            if comment_key is not None and (
                comment_key in self._pending_comments or comment_key in self._completed_comments
            ):
                return True
            if ordinary:
                if issue_key in self._pending_issues:
                    return True
                completed_at = self._completed_issues.get(issue_key)
                if completed_at is not None and self._clock() - completed_at < ISSUE_COOLDOWN_SECONDS:
                    return True
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                return False
            # Reserve only now that the job is actually queued.
            self._pending_deliveries.add(job.delivery_id)
            if comment_key is not None:
                self._pending_comments.add(comment_key)
            if ordinary:
                self._pending_issues.add(issue_key)
        return True

    def close(self) -> None:
        """Stop admission, drain for at most ``drain_timeout``, close clients.

        Whatever is left at the deadline is given up and logged, never run.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is not None:
            deadline = time.monotonic() + self._drain_timeout
            try:
                # Waits for room on a full queue, but never past the deadline.
                self._queue.put(None, timeout=self._drain_timeout)
            except queue.Full:
                pass
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                self._abandon()
        if self._closer is not None:
            self._closer()

    def _abandon(self) -> None:
        """Give up on the backlog: log the running job and every queued one."""
        with self._lock:
            self._abandoned = True
            current = self._current
        if current is not None:
            _log_unprocessed(current, _STILL_RUNNING)
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            self._queue.task_done()
            if job is not None:
                _log_unprocessed(job, _NOT_PROCESSED)
        try:
            self._queue.put_nowait(None)  # lets the worker exit after its current job
        except queue.Full:
            pass

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                with self._lock:
                    abandoned = self._abandoned
                    if not abandoned:
                        self._current = job
                if abandoned:
                    # Dequeued after the deadline: it never starts.
                    _log_unprocessed(job, _NOT_PROCESSED)
                    continue
                try:
                    self._process(job)
                finally:
                    with self._lock:
                        self._current = None
            finally:
                self._queue.task_done()

    def _process(self, job: WebhookJob) -> None:
        try:
            self._runner(job)
        except Exception as exc:  # noqa: BLE001 - one bad job must never kill the worker
            # Delivery and issue context only: never the secret or the raw body.
            log.error(
                "webhook job failed: delivery=%s issue=%s error=%s",
                job.delivery_id,
                job.issue_number,
                type(exc).__name__,
            )
            self._release(job, failed=True)
        else:
            self._release(job, failed=False)

    def _release(self, job: WebhookJob, *, failed: bool) -> None:
        """Clear the in-flight reservations and, on success, start the cooldown.

        A failed job forgets its delivery ID entirely so a manual redelivery can
        retry; it never records a completion or a cooldown.
        """
        ordinary = _is_ordinary_issue(job)
        issue_key = (job.tenant, job.issue_number)
        comment_key = _comment_key(job)
        with self._lock:
            self._pending_deliveries.discard(job.delivery_id)
            if comment_key is not None:
                self._pending_comments.discard(comment_key)
            if ordinary:
                self._pending_issues.discard(issue_key)
            if failed:
                return
            self._completed_deliveries[job.delivery_id] = None
            while len(self._completed_deliveries) > self._completed_delivery_limit:
                self._completed_deliveries.popitem(last=False)
            if comment_key is not None:
                self._completed_comments[comment_key] = None
                while len(self._completed_comments) > self._completed_delivery_limit:
                    self._completed_comments.popitem(last=False)
            if ordinary:
                now = self._clock()
                self._completed_issues[issue_key] = now
                for key in [k for k, at in self._completed_issues.items() if now - at >= ISSUE_COOLDOWN_SECONDS]:
                    del self._completed_issues[key]


def _log_unprocessed(job: WebhookJob, reason: str) -> None:
    """One line per accepted job lost to shutdown: identifiers only, never text."""
    log.error(
        "webhook job %s: delivery=%s event=%s action=%s issue=%s",
        reason,
        job.delivery_id,
        job.event,
        job.action,
        job.issue_number,
    )


def _is_ordinary_issue(job: WebhookJob) -> bool:
    """Whether the job is ordinary issue triage (subject to the issue cooldown)."""
    return job.event == "issues"


def _comment_key(job: WebhookJob) -> tuple[str, int] | None:
    """The ``(tenant, comment_id)`` a command job is deduplicated by, or ``None``."""
    if job.event == "issue_comment" and job.comment_id is not None:
        return (job.tenant, job.comment_id)
    return None


router = APIRouter(tags=["webhooks"])


def _json(body: dict, status_code: int) -> JSONResponse:
    return JSONResponse(body, status_code=status_code)


@router.post(
    "/webhooks/github",
    status_code=202,
    response_model=None,
    responses={
        202: {"description": "Accepted for serialized processing, or ignored after validation"},
        400: {"description": "A signed payload that is not a well-formed supported event"},
        401: {"description": "Missing or invalid X-Hub-Signature-256"},
        503: {"description": "Webhook processing is unavailable or the queue is full"},
    },
    description="Verified GitHub webhook receiver (spec section 13); raw-body HMAC first.",
)
async def github_webhook(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    raw = await request.body()

    secret = settings.webhook_secret
    if not secret:
        # Nothing to verify against: fail closed before touching the body.
        return _json({"detail": WEBHOOK_UNAVAILABLE}, 503)

    if not verify_signature(raw, request.headers.get(SIGNATURE_HEADER), secret):
        return _json({"detail": INVALID_SIGNATURE}, 401)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json({"detail": INVALID_PAYLOAD}, 400)
    if not isinstance(payload, dict):
        return _json({"detail": INVALID_PAYLOAD}, 400)

    # Configuration guards run only after signature validation, never before.
    worker = getattr(request.app.state, "webhook_worker", None)
    if not webhook_admission_enabled(settings) or worker is None:
        return _json({"detail": WEBHOOK_UNAVAILABLE}, 503)

    try:
        job = build_job(
            settings,
            payload,
            event=request.headers.get(EVENT_HEADER),
            delivery_id=request.headers.get(DELIVERY_HEADER),
        )
    except WebhookIgnored:
        return _json({"status": "ignored"}, 202)
    except WebhookPayloadError:
        return _json({"detail": INVALID_PAYLOAD}, 400)

    if not worker.enqueue(job):
        return _json({"detail": WEBHOOK_UNAVAILABLE}, 503)
    return _json({"status": "accepted"}, 202)
