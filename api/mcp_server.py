"""Keystone's MCP server (spec sections 5 and 7): the six graph tools plus apply_triage.

Run over stdio with `python -m api.mcp_server`. The agent reaches the same `mcp` object through
fastmcp's in-memory client, and api/mcp_http.py serves it over HTTP without apply_triage.
"""
from __future__ import annotations

import threading

from fastmcp import FastMCP

from api import embeddings
from api.schemas import ActionResult, ApplyResult
from api.tools import affected_modules, bus_factor, explain_path, find_decisions, rank_reviewers, recall_run


def apply_triage(tenant: str, run_key: str, dry_run: bool = True) -> ApplyResult:
    """Stub until B6 lands api.actions.apply_triage: refuses every action the run proposed."""
    run = recall_run(tenant, run_key)
    actions = [] if run.empty else run.data["actions"]
    return ApplyResult(
        run_key=run_key,
        dry_run=dry_run,
        actions=[
            ActionResult(
                action_key=a["action_key"],
                kind=a["kind"],
                value=a["value"],
                status="refused",
                note="apply_triage is a stub until B6",
            )
            for a in actions
        ],
    )


mcp = FastMCP(
    "keystone",
    instructions="Graph tools over an org's GitHub history in FalkorDB. Every result carries the Cypher "
    "that ran and the node keys that support it; cite those keys and say so when a result is empty.",
)

READ_ONLY = {"readOnlyHint": True}

mcp.tool(
    affected_modules,
    description="Use first on a new issue, to find the code modules it most likely touches, "
    "with similar past issues and the labels they were given.",
    annotations=READ_ONLY,
)
mcp.tool(
    rank_reviewers,
    description="Use once you know an affected module, to rank who should review changes to it "
    "by merged-PR expertise, open review load and any avoid rules.",
    annotations=READ_ONLY,
)
mcp.tool(
    bus_factor,
    description="Use on an affected module to check whether its knowledge rests on one person: "
    "merged PRs, the top author's share and how many authors are still active.",
    annotations=READ_ONLY,
)
mcp.tool(
    find_decisions,
    description="Use when an issue may touch something already settled, to find past design decisions "
    "and documentation passages about it, with source links.",
    annotations=READ_ONLY,
)
mcp.tool(
    explain_path,
    description="Use to justify a claim by finding the shortest chain of graph relationships between "
    "two node keys, such as an issue and a suggested reviewer.",
    annotations=READ_ONLY,
)
mcp.tool(
    recall_run,
    description="Use to look back at a past triage run: what it concluded, the evidence it cited, "
    "its proposed actions and any feedback on them.",
    annotations=READ_ONLY,
)
mcp.tool(
    apply_triage,
    description="Use only after a triage run is recorded, to apply its proposed labels, assignee and "
    "comment to the sandbox issue; it is a dry run unless dry_run is false.",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)


if __name__ == "__main__":
    from ingest.fetch import load_env

    load_env()
    # Load the embedding model in the background so the first affected_modules or find_decisions
    # call doesn't pay about 19 s, without delaying the client's initialize handshake.
    threading.Thread(target=embeddings.model, daemon=True).start()
    mcp.run(transport="stdio")
