"""B7 Task 3: the composed webhook triage -> apply pipeline (spec section 13).

On its first attempt, one verified job runs on the dedicated worker thread
through the real persisted pipeline in this order::

    vector = incoming_issue_vector(job)
    replay = parse_replay(job.body, allowed_repos)
    issue_key = store.upsert_issue(tenant, job, vector, replay)
    result = asyncio.run(triage(tenant, issue_key))
    apply_service.apply_triage(tenant, result.run_key, dry_run=False)

An upsert-only job (``job.triage`` is false: an ``issues.edited`` the worker
admitted inside the issue's triage window) stops after the upsert, so the
incoming Issue and its ``REPLAY_OF`` stay current without another triage.

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
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from api import schemas
from api.actions import ActionService
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
    ):
        self._store = store
        self._triage = triage
        self._apply_service = apply_service
        self._tenant = tenant
        self._allowed_repos = set(allowed_repos)
        self._commands = commands
        self._pending_runs = PendingTriageRuns()

    def __call__(self, job: WebhookJob) -> None:
        if job.event == "issue_comment":
            if self._commands is not None:
                self._commands(job)
            return
        vector = incoming_issue_vector(job)
        replay = parse_replay(job.body, self._allowed_repos)
        issue_key = self._store.upsert_issue(self._tenant, job, vector, replay)
        if not job.triage:
            return
        retry_key = (self._tenant, issue_key, job.delivery_id)
        run_key = self._pending_runs.run_key(retry_key, self._triage)
        applied = self._apply_service.apply_triage(self._tenant, run_key, dry_run=False)
        self._pending_runs.applied(retry_key)
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
