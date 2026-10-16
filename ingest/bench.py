"""Latency bench (spec section 14): each tool's Cypher queries, warm, against one tenant graph.

    python -m ingest.bench --tenant falkordb

Inputs are fixed so every run repeats the same queries. The incoming issue is the newest one from
the Proof sample (`backtest_sample.cypher`), triaged as of now with that issue itself excluded. Its
top module from `affected_modules` feeds `rank_reviewers` and `bus_factor`, and `explain_path` goes
from the top reviewer to that module. `find_decisions` runs on the issue title. Query vectors are
encoded once, outside the timer: only the queries are timed, each through the same `rows()` round
trip the tools use. A tool with two queries (`find_decisions`, `find_chunks`) is timed as one call.
`recall_run` uses the newest AgentRun, or a missing key when there is none; `inputs` records it.

Each tool runs WARMUP untimed times, then RUNS timed times; p50 and p95 are in ms. Output is
data/bench/<tenant>.json: `node_counts` and `edge_counts` (`stats.cypher`) and `latencies` (one
`ToolLatency` each) map onto `api.schemas.Stats`; `runs`, `warmup`, `measured_at` and `inputs`
(the parameters, the vector redacted, and whether the answer was empty) are for the README.
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
from api.tools._run import DAY, VECTOR, now, rows
from ingest.fetch import ROOT, load_env
from ingest.proof import WARMUP as PROOF_WARMUP

RUNS = 50
WARMUP = 5
MISSING_RUN = "run:bench-missing"


def plan(tenant: str) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """The queries each tool runs, as (cypher name, params), with fixed inputs from the graph."""
    issues, _ = rows(tenant, "backtest_sample", {"n": 1_000_000, "warmup": PROOF_WARMUP})
    if not issues:
        raise SystemExit("bench: the Proof sample is empty; load the tenant first")
    issue = max(issues, key=lambda i: i["before"])
    before = now()
    am = {
        "q": encode([issue_text(issue["title"], issue["body"])])[0],
        "exclude_issues": [issue["issue"]],
        "before": before,
    }
    modules, _ = rows(tenant, "affected_modules", am)
    if not modules:
        raise SystemExit(f"bench: affected_modules found no module for {issue['issue']}")
    module = modules[0]["module"]
    rr = {"module": module, "k": 5, "before": before, "recent": before - 180 * DAY, "exclude": []}
    reviewers, _ = rows(tenant, "rank_reviewers", rr)
    if not reviewers:
        raise SystemExit(f"bench: rank_reviewers found nobody for {module}")
    fd = {"q": encode([issue["title"]])[0]}
    runs, _ = rows(tenant, "latest_run", {})
    return {
        "affected_modules": [("affected_modules", am)],
        "rank_reviewers": [("rank_reviewers", rr)],
        "bus_factor": [("bus_factor", {"module": module, "since": before - 365 * DAY, "before": before})],
        "explain_path": [("explain_path", {"from": reviewers[0]["person"], "to": module})],
        "find_decisions": [("find_decisions", fd), ("find_chunks", fd)],
        "recall_run": [("recall_run", {"run": runs[0]["run"] if runs else MISSING_RUN})],
    }


def time_tool(tenant: str, queries: list[tuple[str, dict[str, Any]]]) -> tuple[list[float], bool]:
    """RUNS timings in ms of one tool's queries run back to back, after WARMUP untimed runs, and
    whether every query came back empty."""
    empty = True
    for _ in range(WARMUP):
        for name, params in queries:
            found, _ = rows(tenant, name, params)
            empty = empty and not found
    times = []
    for _ in range(RUNS):
        started = time.perf_counter()
        for name, params in queries:
            rows(tenant, name, params)
        times.append((time.perf_counter() - started) * 1000)
    return times, empty


def bench(tenant: str) -> dict[str, Any]:
    latencies, inputs = [], {}
    for tool, queries in plan(tenant).items():
        times, empty = time_tool(tenant, queries)
        cuts = statistics.quantiles(times, n=100, method="inclusive")
        latencies.append(ToolLatency(tool=tool, p50_ms=round(cuts[49], 2), p95_ms=round(cuts[94], 2)))
        inputs[tool] = {
            "queries": [name for name, _ in queries],
            "params": {k: VECTOR if k == "q" else v for k, v in queries[0][1].items()},
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


if __name__ == "__main__":
    main()
