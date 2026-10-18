"""Task 2: tenant-scoped webhook-origin lineage for Slack notification eligibility.

An eligible run is one that was recorded by a verified webhook job and lineage-marked
through :meth:`NotificationStore.mark_run`: the write only MATCHes an existing
``AgentRun`` that ``HANDLED`` an incoming sandbox ``Issue`` and stamps
``notify_root``/``notify_parent``/``notify_event``/``notify_cause``/``notify_at`` onto
it. A parentless issue event roots a lineage at its own key; every descendant inherits
the exact proven parent's root, so a restart can still recognize eligibility from the
tenant graph. Nothing here is authoritative by itself: reads validate the exact tenant,
the canonical incoming issue and (for a UI parent) the exact persisted UI feedback.

Each method is one named, parameterized query in ``cypher/``; the recall row shape is
the same one the recall tool already maps, and the incoming-issue reads and writes are
delegated to :class:`~api.webhook_store.WebhookStore` rather than duplicated.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from api.cypher import query
from api.db import graph as tenant_graph
from api.github import validate_repo
from api.notification_messages import Cause, RunSnapshot
from api.tools.recall_run import recall_run_data
from api.webhook_store import WebhookStore


# A parentless root may only be created by an issue job (opened or edited).
_ISSUE_CAUSES: frozenset[str] = frozenset({"issue_opened", "issue_edited"})


class _BoundedGraph:
    """Bound every notification query, including delegated recall/evidence reads."""

    def __init__(self, graph):
        self._graph = graph

    def query(self, text, params=None):
        return self._graph.query(text, params, timeout=1000)


def _notification_graph(tenant: str):
    return tenant_graph(tenant, timeout_seconds=2.0)


def _now_ms() -> int:
    """Current epoch milliseconds; a module-level seam so tests can pin ``notify_at``."""
    return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class _RunMeta:
    title: str
    replay_of: str | None
    notify_root: str | None = None


def _snapshot(row: dict) -> RunSnapshot | None:
    """Map one latest-run row (recall columns + title + replay) to a detached snapshot."""
    values = dict(row)
    title = values.pop("title", None)
    replay_of = values.pop("replay_of", None)
    values.pop("notify_root", None)
    data = recall_run_data(values)
    if data is None:
        return None
    return RunSnapshot(data=data, title=title or "", replay_of=replay_of)


class NotificationStore:
    """The tenant-graph lineage reads and the one lineage write Slack eligibility trusts.

    The default graph uses separate two-second connect/read bounds and no retries;
    queries have a one-second server runtime bound. Injected factories own their
    connection bounds and must accept FalkorDB's ``query(..., timeout=...)`` API.
    """

    def __init__(
        self,
        sandbox_repo: str,
        graph_factory: Callable[[str], object] = _notification_graph,
    ):
        sandbox_repo = validate_repo(sandbox_repo)
        self._sandbox_repo = sandbox_repo
        self._graph_factory = lambda tenant: _BoundedGraph(graph_factory(tenant))
        self._webhook = WebhookStore(sandbox_repo=sandbox_repo, graph_factory=self._graph_factory)

    # --- lineage reads ------------------------------------------------------

    def load_run(self, tenant: str, run_key: str) -> RunSnapshot | None:
        """One persisted issue run as a detached snapshot, or ``None``.

        Reuses ``WebhookStore.recall_run`` for the data and ``notification_run_meta``
        for the issue title and the issue's actual ``REPLAY_OF`` key. A raw/adhoc run
        has no ``HANDLED`` issue edge and is ``None``.
        """
        data = self._webhook.recall_run(tenant, run_key)
        if data is None:
            return None
        meta = self._run_meta(tenant, run_key, None)
        if meta is None:
            return None
        return RunSnapshot(data=data, title=meta.title, replay_of=meta.replay_of)

    def latest_eligible(self, tenant: str, issue_key: str) -> RunSnapshot | None:
        """The latest lineage-marked run for the exact incoming issue, or ``None``.

        Ordered by the recorded ``notify_at`` then run key, so an unrelated later
        manual run is never selected and ties break deterministically.
        """
        res = self._graph_factory(tenant).query(
            query("notification_latest_run"), {"issue": issue_key}
        )
        if not res.result_set:
            return None
        columns = [header[1] for header in res.header]
        return _snapshot(dict(zip(columns, res.result_set[0])))

    def eligible_parent(
        self,
        tenant: str,
        issue_key: str,
        parent_run_key: str,
        *,
        ui_feedback_key: str | None = None,
    ) -> RunSnapshot | None:
        """The proven eligible parent for this issue, or ``None``.

        The parent must have handled the exact incoming issue and carry recorded
        lineage. For a UI follow-up it must additionally have proposed the Action
        that the exact persisted ``given_by='ui'`` feedback is on.
        """
        data = self._webhook.recall_run(tenant, parent_run_key)
        if data is None:
            return None
        if ui_feedback_key is None:
            meta = self._run_meta(tenant, parent_run_key, issue_key)
            if meta is None or not meta.notify_root:
                return None
        else:
            meta = self._ui_parent_meta(tenant, parent_run_key, issue_key, ui_feedback_key)
            if meta is None:
                return None
        return RunSnapshot(data=data, title=meta.title, replay_of=meta.replay_of)

    def node_links(self, tenant: str, keys: list[str]) -> dict[str, str]:
        """Delegate to the existing store's stored-URL reader."""
        return self._webhook.node_links(tenant, keys)

    # --- the lineage write --------------------------------------------------

    def mark_run(
        self,
        tenant: str,
        run_key: str,
        *,
        issue_key: str,
        cause: Cause,
        event_id: str,
        parent_run_key: str | None,
    ) -> bool:
        """Record webhook-origin lineage for one run, or return ``False``.

        The run must already ``HANDLE`` the configured sandbox's own incoming issue
        ``issue_key``; a descendant must additionally have an already-marked proven
        parent on the same issue. Only an issue job may root a lineage. ``notify_at``
        is stamped once from the current epoch milliseconds and never changes, and a
        conflicting reparent is refused.
        """
        if parent_run_key is None and cause not in _ISSUE_CAUSES:
            return False
        if not self._is_sandbox_issue(issue_key):
            return False
        params = {
            "run": run_key,
            "issue": issue_key,
            "parent": parent_run_key,
            "event": event_id,
            "cause": cause,
            "now": _now_ms(),
        }
        res = self._graph_factory(tenant).query(query("notification_mark_run"), params)
        if not res.result_set:
            return False
        columns = [header[1] for header in res.header]
        row = dict(zip(columns, res.result_set[0]))
        return (
            row.get("root") is not None
            and row.get("parent") == parent_run_key
            and row.get("event") == event_id
            and row.get("cause") == cause
        )

    # --- helpers ------------------------------------------------------------

    def _is_sandbox_issue(self, issue_key: str) -> bool:
        prefix = f"issue:{self._sandbox_repo}#"
        if not isinstance(issue_key, str) or not issue_key.startswith(prefix):
            return False
        number = issue_key[len(prefix):]
        return number.isdigit() and int(number) > 0

    def _run_meta(self, tenant: str, run_key: str, issue_key: str | None) -> _RunMeta | None:
        res = self._graph_factory(tenant).query(
            query("notification_run_meta"), {"run": run_key, "issue": issue_key}
        )
        return _meta_row(res)

    def _ui_parent_meta(
        self, tenant: str, run_key: str, issue_key: str, feedback_key: str
    ) -> _RunMeta | None:
        res = self._graph_factory(tenant).query(
            query("notification_ui_parent"),
            {"run": run_key, "issue": issue_key, "feedback": feedback_key},
        )
        return _meta_row(res)


def _meta_row(res) -> _RunMeta | None:
    if not res.result_set:
        return None
    columns = [header[1] for header in res.header]
    row = dict(zip(columns, res.result_set[0]))
    return _RunMeta(
        title=row.get("title") or "",
        replay_of=row.get("replay_of"),
        notify_root=row.get("notify_root"),
    )
