from __future__ import annotations

import time

from api.schemas import PathData, ToolResult
from api.tools._run import result, rows


def explain_path(tenant: str, from_key: str, to_key: str) -> ToolResult:
    """The shortest evidence path (at most 4 hops) between two nodes, as keys and relationship types."""
    started = time.perf_counter()
    params = {"from": from_key, "to": to_key}
    found, text = rows(tenant, "explain_path", params)
    path = PathData(**found[0]) if found else PathData(keys=[], rels=[])
    return result("explain_path", tenant, started, path.model_dump(), [text], params, path.keys, not found)
