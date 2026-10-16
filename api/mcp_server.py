"""Keystone's MCP server (spec sections 5 and 7): the six graph tools plus apply_triage.

`graph_tools` holds the six read-only graph tools; api/mcp_http.py serves it over HTTP, so
apply_triage is never exposed there (spec section 13). `mcp` mounts `graph_tools` and adds
apply_triage: run it over stdio with `python -m api.mcp_server`, and the agent reaches it
through fastmcp's in-memory client.
"""
from __future__ import annotations

import os
import threading

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from api import embeddings
from api.actions import ActionContextNotFound, ActionService
from api.memory import GraphActionStore
from api.schemas import ApplyResult
from api.tools import affected_modules, bus_factor, explain_path, find_decisions, rank_reviewers, recall_run


def apply_triage(tenant: str, run_key: str, dry_run: bool = True) -> ApplyResult:
    """B6's ActionService over the run's stored actions (api.memory.GraphActionStore). Over MCP it
    only plans: live writes happen in the webhook flow alone (spec section 13), so dry_run=false and
    a missing SANDBOX_REPO raise ToolError, as does an unknown run."""
    if dry_run is not True:
        raise ToolError("apply_triage over MCP is a dry run only; the sandbox webhook applies live")
    sandbox = os.getenv("SANDBOX_REPO", "").strip()
    if not sandbox:
        raise ToolError("SANDBOX_REPO is not configured")
    service = ActionService(GraphActionStore(), sandbox_repo=sandbox, api_mode="live",
                            public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip() or None)
    try:
        return service.apply_triage(tenant, run_key, dry_run=True)
    except ActionContextNotFound:
        raise ToolError(f"unknown run: {run_key}") from None


INSTRUCTIONS = (
    "Graph tools over an org's GitHub history in FalkorDB. Every result carries the Cypher "
    "that ran and the node keys that support it; cite those keys and say so when a result is empty."
)

# mask_error_details: clients see the text of a ToolError we raise, never raw FalkorDB or Redis errors.
graph_tools = FastMCP("keystone-graph", instructions=INSTRUCTIONS, mask_error_details=True)
mcp = FastMCP("keystone", instructions=INSTRUCTIONS, mask_error_details=True)

READ_ONLY = {"readOnlyHint": True}

graph_tools.tool(
    affected_modules,
    description="Use first on a new issue, to find the code modules it most likely touches, "
    "with similar past issues and the labels they were given.",
    annotations=READ_ONLY,
)
graph_tools.tool(
    rank_reviewers,
    description="Use once you know an affected module, to rank who should review changes to it "
    "by merged-PR expertise, open review load and any avoid rules.",
    annotations=READ_ONLY,
)
graph_tools.tool(
    bus_factor,
    description="Use on an affected module to check whether its knowledge rests on one person: "
    "merged PRs, the top author's share and how many authors are still active.",
    annotations=READ_ONLY,
)
graph_tools.tool(
    find_decisions,
    description="Use when an issue may touch something already settled, to find past design decisions "
    "and documentation passages about it, with source links.",
    annotations=READ_ONLY,
)
graph_tools.tool(
    explain_path,
    description="Use to justify a claim by finding the shortest chain of graph relationships between "
    "two node keys, such as an issue and a suggested reviewer.",
    annotations=READ_ONLY,
)
graph_tools.tool(
    recall_run,
    description="Use to look back at a past triage run: what it concluded, the evidence it cited, "
    "its proposed actions and any feedback on them.",
    annotations=READ_ONLY,
)

mcp.mount(graph_tools)  # no namespace: the six tools keep their names on `mcp`
mcp.tool(
    apply_triage,
    description="Use only after a triage run is recorded, to preview what applying its proposed labels, "
    "assignees and comment to the sandbox issue would do; over MCP it is always a dry run.",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)


if __name__ == "__main__":
    from ingest.fetch import load_env

    load_env()
    # Load the embedding model in the background so the first affected_modules or find_decisions
    # call doesn't pay about 19 s, without delaying the client's initialize handshake.
    threading.Thread(target=embeddings.model, daemon=True).start()
    mcp.run(transport="stdio")
