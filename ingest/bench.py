"""Latency bench (spec section 14): each tool's Cypher queries, warm, against one tenant graph.

    python -m ingest.bench --tenant falkordb

Inputs are picked once per bench run, so its warm runs repeat the same queries. The incoming issue
is the newest one in the Proof sample (`ingest.proof.sample`), triaged as of now with that issue
itself excluded. Its top module from `affected_modules` feeds `rank_reviewers` and `bus_factor`, and
`explain_path` goes from the top reviewer to that module. `find_decisions` runs on the issue title.
`recall_run` uses the newest AgentRun; with none it is skipped, not timed. Each tool is called once
with these inputs, and `empty` is that call's own `ToolResult.empty`. Query vectors are encoded once,
outside the timer: only the queries are timed, each through the same `rows()` round trip the tools
use. A tool with two queries (`find_decisions`, `find_chunks`) is timed as one call, and
`explain_path` as the same 2-, 3-, 4-hop searches the tool ran before it found the path.

Each tool runs WARMUP untimed times, then RUNS timed times; p50 and p95 are in ms. Output is
data/bench/<tenant>.json: `node_counts` and `edge_counts` (`stats.cypher`) and `latencies` (one
`ToolLatency` each) map onto those fields of `api.schemas.Stats`; its `last_ingest_at` and `proof`
come from elsewhere (proof from data/proof/<tenant>.json). `runs`, `warmup`, `measured_at`,
`inputs` (the parameters, the vector redacted, and the tool's `empty`) and `skipped` (tool -> why it
was not timed) are for the README.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

from api.embeddings import encode, issue_text
from api.graph_reads import stats_counts
from api.schemas import ToolLatency
from api.tools import affected_modules, bus_factor, explain_path, find_decisions, rank_reviewers, recall_run
from api.tools.explain_path import MAX_LENS
from api.tools._run import BUS_WINDOW, RECENT, VECTOR, now, rows
from ingest.fetch import ROOT, load_env
from ingest.proof import sample

RUNS = 50
WARMUP = 5


Queries = list[tuple[str, dict[str, Any]]]


def plan(tenant: str) -> tuple[dict[str, tuple[Queries, bool]], dict[str, str]]:
    """Per tool, the queries it runs as (cypher name, params) with fixed inputs from the graph, and
    the `empty` the real tool returns for those inputs; plus the tools skipped, with why."""
    issues = sample(tenant)
    if not issues:
        raise SystemExit("bench: the Proof sample is empty; load the tenant first")
    issue = max(issues, key=lambda i: i["before"])
    title, body, before = issue["title"], issue["body"] or "", now()
    repo = issue["issue"].split(":", 1)[1].rsplit("#", 1)[0]  # the issue's own repo, as the agent and Proof search it
    am_tool = affected_modules(tenant, title, body, exclude_issues=[issue["issue"]], before=before, repo=repo)
    if am_tool.empty:
        raise SystemExit(f"bench: affected_modules found no module for {issue['issue']}")
    module = am_tool.data["modules"][0]["module"]
    rr_tool = rank_reviewers(tenant, module, k=5, before=before)
    if rr_tool.empty:
        raise SystemExit(f"bench: rank_reviewers found nobody for {module}")
    person = rr_tool.data["reviewers"][0]["person"]
    runs, _ = rows(tenant, "latest_run", {})
    am = {"q": encode([issue_text(title, body)])[0], "exclude_issues": [issue["issue"]], "before": before, "repo": repo}
    rr = {"module": module, "k": 5, "before": before, "recent": before - RECENT, "exclude": []}
    bf = {"module": module, "since": before - BUS_WINDOW, "before": before}
    fd = {"q": encode([title])[0]}
    ep_tool = explain_path(tenant, person, module)
    ep = [("explain_path", {"from": person, "to": module, "max_len": n})
          for n in MAX_LENS if n <= ep_tool.params["max_len"]]
    tools = {
        "affected_modules": ([("affected_modules", am)], am_tool.empty),
        "rank_reviewers": ([("rank_reviewers", rr)], rr_tool.empty),
        "bus_factor": ([("bus_factor", bf)], bus_factor(tenant, module, before=before).empty),
        "explain_path": (ep, ep_tool.empty),
        "find_decisions": ([("find_decisions", fd), ("find_chunks", fd)], find_decisions(tenant, title).empty),
    }
    if not runs:
        return tools, {"recall_run": "no AgentRun yet"}
    run_key = runs[0]["run"]
    tools["recall_run"] = ([("recall_run", {"run": run_key})], recall_run(tenant, run_key).empty)
    return tools, {}


def time_tool(tenant: str, queries: Queries) -> list[float]:
    """RUNS timings in ms of one tool's queries run back to back, after WARMUP untimed runs."""
    for _ in range(WARMUP):
        for name, params in queries:
            rows(tenant, name, params)
    times = []
    for _ in range(RUNS):
        started = time.perf_counter()
        for name, params in queries:
            rows(tenant, name, params)
        times.append((time.perf_counter() - started) * 1000)
    return times


def bench(tenant: str) -> dict[str, Any]:
    latencies, inputs = [], {}
    tools, skipped = plan(tenant)
    for tool, (queries, empty) in tools.items():
        times = time_tool(tenant, queries)
        cuts = statistics.quantiles(times, n=100, method="inclusive")
        latencies.append(ToolLatency(tool=tool, p50_ms=round(cuts[49], 2), p95_ms=round(cuts[94], 2)))
        inputs[tool] = {
            "queries": [name for name, _ in queries],
            "params": {k: VECTOR if k == "q" else v for k, v in queries[-1][1].items()},
            "empty": empty,
        }
    counts, _ = rows(tenant, "stats", {})
    pivot = stats_counts((r["kind"], r["name"], r["n"]) for r in counts)
    return {
        "node_counts": pivot["node_counts"],
        "edge_counts": pivot["edge_counts"],
        "latencies": [lat.model_dump() for lat in latencies],
        "runs": RUNS,
        "warmup": WARMUP,
        "measured_at": now(),
        "inputs": inputs,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", required=True)
    args = ap.parse_args(argv)
    load_env()
    out = ROOT / "data" / "bench" / f"{args.tenant}.json"
    result = bench(args.tenant)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    nodes, edges = sum(result["node_counts"].values()), sum(result["edge_counts"].values())
    print(f"{nodes} nodes, {edges} edges, {RUNS} warm runs per tool -> {out.relative_to(ROOT)}")
    print(f"{'tool':<18}{'p50 ms':>10}{'p95 ms':>10}  empty")
    for lat in result["latencies"]:
        print(f"{lat['tool']:<18}{lat['p50_ms']:>10.2f}{lat['p95_ms']:>10.2f}  {result['inputs'][lat['tool']]['empty']}")
    for tool, why in result["skipped"].items():
        print(f"{tool} skipped: {why}")


if __name__ == "__main__":
    main()
