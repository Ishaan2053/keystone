from __future__ import annotations

import time

from api.schemas import ToolResult
from api.tools._run import BUS_WINDOW, now, result, rows


def bus_factor(
    tenant: str,
    module_key: str,
    since: int | None = None,
    before: int | None = None,
) -> ToolResult:
    """Merged PRs touching a module in a window, its top author and their share, and the number of
    active authors. `since` defaults to a year before `before`, so a backtest sees the same window."""
    started = time.perf_counter()
    before = before if before is not None else now()
    params = {
        "module": module_key,
        "since": since if since is not None else before - BUS_WINDOW,
        "before": before,
    }
    found, text = rows(tenant, "bus_factor", params)
    row = found[0] if found else None
    empty = row is None or row["total"] == 0
    evidence = [] if empty else [row["module"], row["top"]["key"]]
    return result("bus_factor", tenant, started, row, [text], params, evidence, empty)
