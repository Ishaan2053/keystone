from __future__ import annotations

import time

from api.embeddings import encode
from api.schemas import ToolResult
from api.tools._run import result, rows


def find_decisions(tenant: str, query: str) -> ToolResult:
    """Up to 5 past decisions and 5 doc chunks closest to a question, with their source URLs.
    `empty` is true only when both lists are empty."""
    started = time.perf_counter()
    params = {"q": encode([query])[0]}
    decisions, decisions_text = rows(tenant, "find_decisions", params)
    chunks, chunks_text = rows(tenant, "find_chunks", params)
    evidence = []
    for d in decisions:
        evidence += [d["decision"], *(s["key"] for s in d["sources"]), *d["modules"]]
    for c in chunks:
        evidence += [c["chunk"], c["doc"]]
    data = {"decisions": decisions, "chunks": chunks}
    return result(
        "find_decisions", tenant, started, data, [decisions_text, chunks_text], params, evidence,
        not decisions and not chunks,
    )
