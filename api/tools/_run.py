"""Shared by every tool: run a named query from cypher/ and wrap the answer as a ToolResult (spec section 5)."""
from __future__ import annotations

import os
import time
from typing import Any

from fastmcp.exceptions import ToolError

from api import cypher
from api.config import _split_csv
from api.db import graph
from api.schemas import ToolResult

DAY = 86400
VECTOR = "<384-d vector>"


def now() -> int:
    return int(time.time())


def rows(tenant: str, name: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Rows of cypher/<name>.cypher as dicts by column name, read-only, plus the query text that ran.
    A tenant not in TENANTS (parsed as api.config does) raises ToolError before the graph is touched."""
    if tenant not in (_split_csv(os.getenv("TENANTS", "falkordb")) or ("falkordb",)):
        raise ToolError(f"unknown tenant: {tenant}")
    text = cypher.query(name)
    res = graph(tenant).ro_query(text, params)
    cols = [h[1] for h in res.header]
    return [dict(zip(cols, r)) for r in res.result_set], text


def result(
    tool: str,
    tenant: str,
    started: float,
    data: Any,
    queries: list[str],
    params: dict[str, Any],
    evidence: list[str | None],
    empty: bool,
) -> ToolResult:
    """A ToolResult; `params` shows the query parameters with the embedding `q` redacted,
    `evidence` is deduplicated in order, and `ms` covers the whole tool call since `started`."""
    shown = {"tenant": tenant, **{k: VECTOR if k == "q" else v for k, v in params.items()}}
    return ToolResult(
        tool=tool,
        data=data,
        cypher=queries,
        params=shown,
        evidence=list(dict.fromkeys(k for k in evidence if k)),
        ms=round((time.perf_counter() - started) * 1000),
        empty=empty,
    )
