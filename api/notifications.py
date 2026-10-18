"""Task 3: the best-effort Slack notification coordinator.

``SlackNotifications`` sits between the trusted call sites (Tasks 4-6) and the
pure renderer: it decides whether one event is notifiable, reserves exactly one
immutable parent snapshot per trusted event, deduplicates delivery attempts in
this process, and never lets a notification-only failure change a core triage or
apply outcome. It performs no LLM or tool work of its own.

Delivery is best-effort: an optionless construction (``sender=None``) still
records lineage so enabling Slack later can recognize proven runs, but sends
nothing. Same-process deduplication is bounded and explicitly not a delivery
guarantee: eviction, restart and ambiguous transport failures can resend or drop
a message. No lock is ever held across graph or HTTP work.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Literal

from api import schemas
from api.notification_messages import (
    NotificationAttempt,
    NotificationMessage,
    RunSnapshot,
    citation_keys,
    recommendation_fingerprint,
    render_message,
)
from api.notification_store import NotificationStore
from api.slack import SlackSender

logger = logging.getLogger("api.notifications")

# Fixed copy: a notification-only failure never echoes a value, key or exception.
DEPENDENCY_WARNING = "Slack notification step failed; triage is unaffected"


class SlackNotifications:
    """Coordinate bounded, best-effort Slack notifications for one tenant graph."""

    def __init__(
        self,
        store: NotificationStore,
        sender: SlackSender | None,
        *,
        public_base_url: str,
        limit: int = 10_000,
    ):
        self._store = store
        self._sender = sender
        self._public_base_url = public_base_url
        self._limit = max(int(limit), 1)
        self._lock = threading.Lock()
        # (tenant, issue_key, event_id) -> reserved parent snapshot (or None for a root).
        self._reservations: dict[tuple[str, str, str], RunSnapshot | None] = {}
        # (tenant, issue_key, event_id, outcome) -> None; the delivery-attempt record.
        self._attempted: dict[tuple[str, str, str, str], None] = {}
        # (tenant, issue_key, event_id) -> None; events whose core processing failed.
        self._core_failures: dict[tuple[str, str, str], None] = {}

    # --- beginnings ---------------------------------------------------------

    def begin_issue(
        self,
        *,
        tenant: str,
        issue_key: str,
        delivery_id: str,
        action: Literal["opened", "edited"],
        title: str,
        replay_of: str | None,
    ) -> NotificationAttempt | None:
        """Reserve the latest eligible parent for one admitted issue event.

        Either issue action reserves the same latest lineage-marked run for the
        exact issue, so an in-flight edit keeps its baseline across apply retries;
        no eligible parent means this attempt is the issue's initial triage.
        """
        try:
            if action not in ("opened", "edited") or not self._trusted(tenant, issue_key, delivery_id):
                return None
            event_id = f"delivery:{delivery_id}"
            cause = "issue_opened" if action == "opened" else "issue_edited"
            parent = self._reserve(tenant, issue_key, event_id, self._latest_loader(tenant, issue_key))
            return NotificationAttempt(
                tenant=tenant,
                issue_key=issue_key,
                event_id=event_id,
                cause=cause,
                title=title,
                replay_of=replay_of,
                parent=parent,
            )
        except Exception:
            logger.warning(DEPENDENCY_WARNING)
            return None

    def begin_github_feedback(
        self,
        *,
        tenant: str,
        issue_key: str,
        parent_run_key: str,
        comment_id: int,
    ) -> NotificationAttempt | None:
        """Reserve the exact proven parent a GitHub reject command re-triages from."""
        return self._begin_feedback(
            tenant=tenant,
            issue_key=issue_key,
            event_id=f"comment:{comment_id}",
            cause="github_feedback",
            parent_run_key=parent_run_key,
            ui_feedback_key=None,
        )

    def begin_ui_feedback(
        self,
        *,
        tenant: str,
        issue_key: str,
        parent_run_key: str,
        feedback_key: str,
    ) -> NotificationAttempt | None:
        """Reserve the exact proven parent a UI re-triage follows validated feedback from."""
        if not str(feedback_key).strip():
            return None
        return self._begin_feedback(
            tenant=tenant,
            issue_key=issue_key,
            event_id=f"ui:{parent_run_key}:{feedback_key}",
            cause="ui_feedback",
            parent_run_key=parent_run_key,
            ui_feedback_key=feedback_key,
        )

    # --- outcomes -----------------------------------------------------------

    def complete(
        self,
        attempt: NotificationAttempt | None,
        *,
        run_key: str,
        applied: schemas.ApplyResult | None,
    ) -> None:
        """Record lineage and, when meaningful, deliver one completed-run message."""
        if attempt is None:
            return
        try:
            self._complete(attempt, run_key, applied)
        except Exception:
            logger.warning(DEPENDENCY_WARNING)

    def failed(
        self,
        attempt: NotificationAttempt | None,
        *,
        stage: Literal["ingest", "triage", "apply"],
        run_key: str | None = None,
    ) -> None:
        """Deliver one sanitized processing-failure message for an eligible attempt.

        Never records success lineage, and never releases the parent reservation:
        a failed processing job keeps its reserved parent for its retry.
        """
        if attempt is None:
            return
        try:
            self._report_failure(attempt, stage, run_key)
        except Exception:
            logger.warning(DEPENDENCY_WARNING)

    # --- internals ----------------------------------------------------------

    def _trusted(self, tenant: str, issue_key: str, event_id: str) -> bool:
        return all(str(value).strip() for value in (tenant, issue_key, event_id))

    def _begin_feedback(
        self,
        *,
        tenant: str,
        issue_key: str,
        event_id: str,
        cause: Literal["github_feedback", "ui_feedback"],
        parent_run_key: str,
        ui_feedback_key: str | None,
    ) -> NotificationAttempt | None:
        try:
            if not self._trusted(tenant, issue_key, event_id) or not str(parent_run_key).strip():
                return None
            parent = self._reserve(
                tenant, issue_key, event_id, self._parent_loader(tenant, issue_key, parent_run_key, ui_feedback_key)
            )
            if parent is None:
                # No proven lineage: never guess a parent, and never notify.
                return None
            return NotificationAttempt(
                tenant=tenant,
                issue_key=issue_key,
                event_id=event_id,
                cause=cause,
                title=parent.title,
                replay_of=parent.replay_of,
                parent=parent,
            )
        except Exception:
            logger.warning(DEPENDENCY_WARNING)
            return None

    def _latest_loader(self, tenant: str, issue_key: str) -> Callable[[], RunSnapshot | None]:
        return lambda: self._store.latest_eligible(tenant, issue_key)

    def _parent_loader(
        self, tenant: str, issue_key: str, parent_run_key: str, ui_feedback_key: str | None
    ) -> Callable[[], RunSnapshot | None]:
        return lambda: self._store.eligible_parent(
            tenant, issue_key, parent_run_key, ui_feedback_key=ui_feedback_key
        )

    def _reserve(
        self, tenant: str, issue_key: str, event_id: str, loader: Callable[[], RunSnapshot | None]
    ) -> RunSnapshot | None:
        """Atomically select one immutable parent for one trusted event.

        The graph read happens off the lock; the first value stored under the lock
        wins, so two racing callers of the same event always agree on one parent.
        """
        key = (tenant, issue_key, event_id)
        with self._lock:
            if key in self._reservations:
                return self._reservations[key]
        parent = loader()
        with self._lock:
            if key in self._reservations:
                return self._reservations[key]
            self._put(self._reservations, key, parent)
            return parent

    def _complete(
        self, attempt: NotificationAttempt, run_key: str, applied: schemas.ApplyResult | None
    ) -> None:
        outcome = "completed"
        if self._seen(attempt, outcome):
            return
        parent_key = attempt.parent.data.run.run_key if attempt.parent is not None else None
        # Lineage is recorded before rendering or sending, even when Slack is off:
        # it is what a later enabling or restart recognizes a proven run by.
        if not self._store.mark_run(
            attempt.tenant,
            run_key,
            issue_key=attempt.issue_key,
            cause=attempt.cause,
            event_id=attempt.event_id,
            parent_run_key=parent_key,
        ):
            return
        current = self._store.load_run(attempt.tenant, run_key)
        if current is None:
            return
        recovered = self._has_failed(attempt)
        unchanged = (
            attempt.parent is not None
            and not recovered
            and recommendation_fingerprint(current) == recommendation_fingerprint(attempt.parent)
        )
        if unchanged:
            self._release(attempt)
            return
        if not self._record(attempt, outcome):
            return
        self._release(attempt)
        if self._sender is None:
            return
        message = NotificationMessage(
            attempt=attempt, current=current, applied=applied, failure_stage=None, recovered=recovered
        )
        self._deliver(attempt, message)

    def _report_failure(
        self, attempt: NotificationAttempt, stage: str, run_key: str | None
    ) -> None:
        outcome = f"failed:{stage}"
        if not self._record(attempt, outcome):
            return
        with self._lock:
            self._put(
                self._core_failures, (attempt.tenant, attempt.issue_key, attempt.event_id), None
            )
        if self._sender is None:
            return
        # Only an apply failure can still have a persisted run to show triage from;
        # ingest and triage failures have no run, and a failure message never reads
        # the graph just to build itself.
        current = self._load_run(attempt.tenant, run_key) if stage == "apply" and run_key else None
        message = NotificationMessage(
            attempt=attempt, current=current, applied=None, failure_stage=stage, recovered=False
        )
        self._deliver(attempt, message)

    def _deliver(self, attempt: NotificationAttempt, message: NotificationMessage) -> None:
        """Render and send once; evidence lookup and transport failures are sanitized.

        The attempt is already recorded, so a failure here is never retried: v1
        prefers a possibly-missed duplicate ping over a re-sent message.
        """
        sender = self._sender
        if sender is None:
            return
        links: dict[str, str] = {}
        try:
            keys = citation_keys(message.current) if message.current is not None else []
            links = self._store.node_links(attempt.tenant, keys) if keys else {}
        except Exception:
            logger.warning(DEPENDENCY_WARNING)
            links = {}
        payload = render_message(
            message, public_base_url=self._public_base_url, evidence_links=links
        )
        sender.send(payload)

    def _load_run(self, tenant: str, run_key: str | None) -> RunSnapshot | None:
        if not run_key:
            return None
        try:
            return self._store.load_run(tenant, run_key)
        except Exception:
            logger.warning(DEPENDENCY_WARNING)
            return None

    def _seen(self, attempt: NotificationAttempt, outcome: str) -> bool:
        key = (attempt.tenant, attempt.issue_key, attempt.event_id, outcome)
        with self._lock:
            return key in self._attempted

    def _record(self, attempt: NotificationAttempt, outcome: str) -> bool:
        """Atomically record one outcome; ``False`` when it was already recorded."""
        key = (attempt.tenant, attempt.issue_key, attempt.event_id, outcome)
        with self._lock:
            if key in self._attempted:
                return False
            self._put(self._attempted, key, None)
            return True

    def _has_failed(self, attempt: NotificationAttempt) -> bool:
        with self._lock:
            return (attempt.tenant, attempt.issue_key, attempt.event_id) in self._core_failures

    def _release(self, attempt: NotificationAttempt) -> None:
        """Forget a completed event's reservation; attempted outcomes are retained."""
        key = (attempt.tenant, attempt.issue_key, attempt.event_id)
        with self._lock:
            self._reservations.pop(key, None)

    def _put(self, cache: dict, key, value) -> None:
        """Insert and evict the oldest entry once ``limit`` is exceeded (FIFO)."""
        cache[key] = value
        while len(cache) > self._limit:
            del cache[next(iter(cache))]
