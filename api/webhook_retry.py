"""Bounded pending triage identities for the single-threaded webhook worker."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from api import schemas

TriageFn = Callable[[str, str], Awaitable[schemas.TriageResult]]
RetryKey = tuple[str, str, str | int]  # trusted tenant, canonical issue, delivery/comment ID


class PendingTriageRuns:
    """Keep a persisted run until apply returns normally, never a failed triage.

    Only the serialized worker calls this cache. At most 10,000 pending runs are
    retained per lane; insertion evicts the oldest pending entry (reads do not
    refresh it). After eviction or restart, redelivery may create another run:
    this is bounded same-process retry protection, not durable exactly-once.
    """

    def __init__(self, *, limit: int = 10_000):
        self._limit = limit
        self._runs: OrderedDict[RetryKey, str] = OrderedDict()

    def run_key(self, key: RetryKey, triage: TriageFn) -> str:
        if key not in self._runs:
            tenant, issue_key, _ = key
            result = asyncio.run(triage(tenant, issue_key))
            # The triage seam returns only after persisting the run. Save before
            # apply can post to GitHub and then fail to record its outcomes.
            self._runs[key] = result.run_key
            while len(self._runs) > self._limit:
                self._runs.popitem(last=False)
        return self._runs[key]

    def applied(self, key: RetryKey) -> None:
        self._runs.pop(key, None)
