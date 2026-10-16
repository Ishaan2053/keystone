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
thread keeps running.

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

WEBHOOK_UNAVAILABLE = "Webhook processing is not available"
INVALID_SIGNATURE = "Invalid signature"
INVALID_PAYLOAD = "Invalid webhook payload"

_SIGNATURE_PREFIX = "sha256="
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
    return hmac.compare_digest(expected, provided)


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

    ``close`` stops admission, drains the accepted backlog, joins the thread and
    then releases any clients the lifespan handed in through ``closer`` (the
    deployment's stop grace must cover the drain).
    """

    def __init__(
        self,
        runner: Callable[[WebhookJob], None],
        *,
        maxsize: int = DEFAULT_QUEUE_CAPACITY,
        closer: Callable[[], None] | None = None,
    ):
        self._runner = runner
        self._closer = closer
        self._queue: queue.Queue[WebhookJob | None] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False

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
        """Admit one job; ``False`` when the worker is closed/not started or full."""
        with self._lock:
            if self._closed or not self._started:
                return False
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                return False
        return True

    def close(self) -> None:
        """Stop admission, drain accepted work, join the thread, close clients."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is not None:
            self._queue.put(None)  # blocks until the draining worker makes room
            thread.join()
        if self._closer is not None:
            self._closer()

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._process(job)
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
