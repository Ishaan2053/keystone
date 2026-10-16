"""B7 Task 3: the composed webhook triage -> apply pipeline (spec section 13).

One verified job runs, on the dedicated worker thread, through the real
persisted pipeline in this exact order::

    vector = incoming_issue_vector(job)
    replay = parse_replay(job.body, allowed_repos)
    issue_key = store.upsert_issue(tenant, job, vector, replay)
    result = asyncio.run(triage(tenant, issue_key))
    apply_service.apply_triage(tenant, result.run_key, dry_run=False)

``triage`` is A10's injected ``async def triage_issue(tenant, issue_key) ->
TriageResult`` seam; this module never implements it. The tenant is fixed at
construction from the validated configuration and is never read from the job,
the replay URL or any request input (ruling R7).

Failure semantics: a failing upsert never reaches triage; a failing triage never
reaches apply; a persistence failure (``ActionStoreError``) propagates so the
worker records no completion. Partial ``refused`` actions are logged by action
key and returned normally: they stay visible in the persisted results and are
never reported as a full success.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from api import schemas
from api.actions import ActionService
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
    """

    def __init__(
        self,
        *,
        store: WebhookStore,
        triage: TriageFn,
        apply_service: ActionService,
        tenant: str,
        allowed_repos: set[str],
    ):
        self._store = store
        self._triage = triage
        self._apply_service = apply_service
        self._tenant = tenant
        self._allowed_repos = set(allowed_repos)

    def __call__(self, job: WebhookJob) -> None:
        vector = incoming_issue_vector(job)
        replay = parse_replay(job.body, self._allowed_repos)
        issue_key = self._store.upsert_issue(self._tenant, job, vector, replay)
        result = asyncio.run(self._triage(self._tenant, issue_key))
        applied = self._apply_service.apply_triage(self._tenant, result.run_key, dry_run=False)
        refused = [action.action_key for action in applied.actions if action.status == "refused"]
        if refused:
            # Partial refusals stay visible in the persisted results; a warning
            # names them without claiming the run fully succeeded.
            log.warning(
                "webhook apply left refused actions: delivery=%s issue=%s run=%s refused=%s",
                job.delivery_id,
                job.issue_number,
                result.run_key,
                ",".join(refused),
            )
