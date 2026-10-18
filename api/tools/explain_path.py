from __future__ import annotations

import time

from api.schemas import PathData, ToolResult
from api.tools._run import result, rows

MAX_LENS = (
    2,
    3,
    4,
)  # deepen one hop at a time; a 4-hop search from a busy node nears the 1 s timeout


def explain_path(tenant: str, from_key: str, to_key: str) -> ToolResult:
    """The shortest evidence path (at most 4 hops) between two nodes, as keys and relationship types.
    Searches 2 hops, then 3, then 4, and stops at the first path; `params` shows the last search.
    A path that needs all 4 hops from a busy node can still hit FalkorDB's query timeout, which raises."""
    started = time.perf_counter()
    for max_len in MAX_LENS:
        params = {"from": from_key, "to": to_key, "max_len": max_len}
        found, text = rows(tenant, "explain_path", params)
        if found:
            break
    path = PathData(**found[0]) if found else PathData(keys=[], rels=[])
    return result(
        "explain_path", tenant, started, path.model_dump(), [text], params, path.keys, not found
    )
