"""B7 Task 3: the composed webhook triage -> apply pipeline (spec section 13).

On its first attempt, one verified job runs on the dedicated worker thread
through the real persisted pipeline in this order::

    attempt = notifications.begin_issue(...)   # trusted canonical identity, before ingest
    replay = parse_replay(job.body, allowed_repos)
    vector = incoming_issue_vector(job)
    issue_key = store.upsert_issue(tenant, job, vector, replay)
    run_key = pending.run_key((tenant, issue_key, delivery_id), triage)
    applied = apply_service.apply_triage(tenant, run_key, dry_run=False)

The canonical sandbox issue key (``store.incoming_issue_key(job.issue_number)``)
comes from construction-time configuration, never the job body or the replay URL,
so an ingest failure still has a trusted identity to report. An upsert-only job
(``job.triage`` is false: an ``issues.edited`` the worker admitted inside the
issue's triage window) stops after the upsert, so the incoming Issue and its
``REPLAY_OF`` stay current without another triage and without preparing a
notification.

``triage`` is A10's injected ``async def triage_issue(tenant, issue_key) ->
TriageResult`` seam; this module never implements it. The tenant is fixed at
construction from the validated configuration and is never read from the job,
the replay URL or any request input (ruling R7).

Failure semantics: a failing upsert never reaches triage; a failing triage never
reaches apply; a persistence failure (``ActionStoreError``) propagates so the
worker records no completion. Partial ``refused`` actions are logged by action
key and returned normally: they stay visible in the persisted results and are
never reported as a full success.

After triage returns a persisted run, failed apply attempts retain its identity
in a bounded FIFO cache. Redelivery reapplies that exact run instead of creating
a new one, preserving ActionService's per-run comment marker. Normal apply
completion releases the entry; eviction and process restart lose this protection.

Slack hooks (Task 4): an optional injected ``notifications`` coordinator is
called at three points, always through a fixed log boundary so a throwing
adapter can never change a core result. One attempt is established before ingest
with the trusted canonical identity; each core stage failure reports that stage
and re-raises the exact exception; a successful apply reports the persisted run
with its real outcome. Redelivery never re-triages just to render a message.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from api import schemas
from api.actions import ActionService
from api.notifications import DEPENDENCY_WARNING, SlackNotifications
from api.webhook_retry import PendingTriageRuns
from api.webhook_store import WebhookStore, incoming_issue_vector, parse_replay
from api.webhooks import WebhookJob

log = logging.getLogger(__name__)

# A10's injected triage seam. It is async because the agent owns async I/O; the
# pipeline is sync and calls it through ``asyncio.run`` off the event loop.
TriageFn = Callable[[str, str], Awaitable[schemas.TriageResult]]


class WebhookPipeline:
    """The sync job runner the webhook worker invokes on its dedicated thread.

    Every dependency is injected and every tenant-scoped call uses the
    construction-time ``tenant``. ``allowed_repos`` is the set of upstream repos
    a replay's ``REPLAY_OF`` edge may target; an empty set fails closed (R8).

    An ``issue_comment`` job is a B8 collaborator command, not ordinary triage:
    it is dispatched to the injected ``commands`` lane (``None`` ignores it) and
    never runs the upsert -> triage -> apply pipeline.

    ``notifications`` is the optional best-effort Slack coordinator. It is only
    consulted for ordinary issue triage; upsert-only and command jobs never
    prepare or send anything, and every call is isolated from core outcomes.
    """

    def __init__(
        self,
        *,
        store: WebhookStore,
        triage: TriageFn,
        apply_service: ActionService,
        tenant: str,
        allowed_repos: set[str],
        commands: Callable[[WebhookJob], None] | None = None,
        notifications: SlackNotifications | None = None,
    ):
        self._store = store
        self._triage = triage
        self._apply_service = apply_service
        self._tenant = tenant
        self._allowed_repos = set(allowed_repos)
        self._commands = commands
        self._notifications = notifications
        self._pending_runs = PendingTriageRuns()

    def __call__(self, job: WebhookJob) -> None:
        if job.event == "issue_comment":
            if self._commands is not None:
                self._commands(job)
            return
        if not job.triage:
            # An upsert-only edit refreshes the incoming Issue (and REPLAY_OF)
            # without triage and never prepares or sends a notification.
            replay = parse_replay(job.body, self._allowed_repos)
            self._store.upsert_issue(self._tenant, job, incoming_issue_vector(job), replay)
            return
        # Preparation resolves the trusted canonical key from construction-time
        # configuration before any ingest work, so an ingest failure has an
        # identity to report; it never enters core failure handling.
        attempt = self._begin(job)
        try:
            replay = parse_replay(job.body, self._allowed_repos)
            vector = incoming_issue_vector(job)
            issue_key = self._store.upsert_issue(self._tenant, job, vector, replay)
        except Exception:
            self._failed(attempt, "ingest")
            raise
        retry_key = (self._tenant, issue_key, job.delivery_id)
        try:
            run_key = self._pending_runs.run_key(retry_key, self._triage)
        except Exception:
            self._failed(attempt, "triage")
            raise
        try:
            applied = self._apply_service.apply_triage(self._tenant, run_key, dry_run=False)
        except Exception:
            # Keep the persisted run pending (never release it early) and report
            # the run key that a redelivery will reapply; Slack is never a reason
            # to run a fresh triage.
            self._failed(attempt, "apply", run_key)
            raise
        self._pending_runs.applied(retry_key)
        self._complete(attempt, run_key, applied)
        refused = [action.action_key for action in applied.actions if action.status == "refused"]
        if refused:
            # Partial refusals stay visible in the persisted results; a warning
            # names them without claiming the run fully succeeded.
            log.warning(
                "webhook apply left refused actions: delivery=%s issue=%s run=%s refused=%s",
                job.delivery_id,
                job.issue_number,
                run_key,
                ",".join(refused),
            )

    # --- Slack boundaries (never change a core result) ----------------------

    def _begin(self, job: WebhookJob):
        """Prepare one attempt, or ``None`` if Slack is off or preparation fails."""
        if self._notifications is None:
            return None
        try:
            # The trusted canonical key comes from the construction-time store;
            # a store that cannot resolve it yields no attempt, never a guess.
            issue_key = self._store.incoming_issue_key(job.issue_number)
            return self._notifications.begin_issue(
                tenant=self._tenant,
                issue_key=issue_key,
                delivery_id=job.delivery_id,
                action=job.action,
                title=job.title,
                # A body-derived candidate is not persisted provenance. Completed
                # notifications resolve REPLAY_OF from the stored run snapshot.
                replay_of=None,
            )
        except Exception:  # noqa: BLE001 - a notification failure never changes the job
            log.warning(DEPENDENCY_WARNING)
            return None

    def _complete(self, attempt, run_key: str, applied: schemas.ApplyResult) -> None:
        if attempt is None or self._notifications is None:
            return
        try:
            self._notifications.complete(attempt, run_key=run_key, applied=applied)
        except Exception:  # noqa: BLE001 - a notification failure never changes the job
            log.warning(DEPENDENCY_WARNING)

    def _failed(self, attempt, stage: str, run_key: str | None = None) -> None:
        if attempt is None or self._notifications is None:
            return
        try:
            self._notifications.failed(attempt, stage=stage, run_key=run_key)
        except Exception:  # noqa: BLE001 - a notification failure never changes the job
            log.warning(DEPENDENCY_WARNING)
