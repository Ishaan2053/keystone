from __future__ import annotations

import time

from api.schemas import ToolResult
from api.tools._run import RECENT, now, result, rows


def rank_reviewers(
    tenant: str,
    module_key: str,
    k: int = 5,
    before: int | None = None,
    exclude_logins: list[str] | None = None,
) -> ToolResult:
    """Up to k people to review a change in a module: expertise from merged PRs (recent ones weigh
    3x), divided by open review load, with active 'avoid' rules zeroing the score."""
    started = time.perf_counter()
    before = before if before is not None else now()
    params = {
        "module": module_key,
        "k": k,
        "before": before,
        "recent": before - RECENT,
        "exclude": exclude_logins or [],
    }
    found, text = rows(tenant, "rank_reviewers", params)
    evidence = [module_key] if found else []
    for p in found:
        evidence += [p["person"], *p["via"], *p["rules"]]
    data = {"module": module_key, "reviewers": found}
    return result("rank_reviewers", tenant, started, data, [text], params, evidence, not found)
