"""Agent memory in the tenant graph (spec section 8). Episodic: each AgentRun with its evidence and
actions, recorded by `record_run` and read back by `recall_run`. `GraphActionStore` is the
`api.actions.ActionStore` that B6's apply service runs on: the run context it applies and the
`record_actions.cypher` executor. Feedback and rules (`record_feedback`) are A11.
"""
from __future__ import annotations

import uuid

from api import cypher
from api.actions import ActionContextNotFound, ApplyContext, parse_issue_key
from api.db import graph
from api.schemas import RecordedAction, RunSummary, ToolResult, TriageResult
from api.tools._run import check_tenant, rows

ADHOC = "adhoc"  # AgentRun.issue_key for a raw title and body: there is no Issue to handle


def proposed_actions(result: TriageResult) -> list[dict]:
    """The actions a run proposes, as record_run.cypher rows: a label per label claim, an assign per
    reviewer claim (with the module rank_reviewers ranked them for), and the one comment."""
    acts = []
    for c in result.labels:
        target = next((k for k in c.citations if k.startswith("label:") and k.endswith(f"/{c.text}")), None)
        acts.append({"kind": "label", "value": c.text, "module": None, "target": target})
    for c in result.reviewers:
        module = next((r.data["module"] for r in result.trace if _ranked(r, c.text)), None)
        acts.append({"kind": "assign", "value": c.text, "module": module, "target": f"person:{c.text}"})
    acts.append({"kind": "comment", "value": "triage comment", "module": None, "target": None})
    return [{"key": f"action:{uuid.uuid4()}", **a} for a in acts]


def _ranked(r: ToolResult, login: str) -> bool:
    return r.tool == "rank_reviewers" and not r.empty and any(
        p["login"] == login and p["score"] > 0 for p in r.data["reviewers"])


def record_run(tenant: str, triage) -> None:
    """Record an `api.agent.Triage` in one transaction (spec section 7, step 6). USED_EVIDENCE goes
    to every key the claims cite; a key that is not a node (none should be) is skipped."""
    check_tenant(tenant)
    r: TriageResult = triage.result
    cited = list(dict.fromkeys(k for s in ("labels", "reviewers", "risks", "decisions")
                               for c in getattr(r, s) for k in c.citations))
    graph(tenant).query(cypher.query("record_run"), {
        "run": r.run_key, "at": triage.at, "issue_key": triage.issue_key or ADHOC, "model": triage.model,
        "llm_calls": triage.llm_calls, "ms": triage.ms, "summary": triage.summary, "fallback": triage.fallback,
        "triage": r.model_dump_json(), "evidence": cited, "actions": proposed_actions(r),
    })


def list_runs(tenant: str, limit: int) -> list[RunSummary]:
    """The newest runs first."""
    found, _ = rows(tenant, "list_runs", {"limit": limit})
    return [RunSummary(**row) for row in found]


class GraphActionStore:
    """B6's ActionStore over the tenant graph."""

    def load_context(self, tenant: str, run_key: str) -> ApplyContext:
        found, _ = rows(tenant, "run_context", {"run": run_key})
        if not found:
            raise ActionContextNotFound("unknown run")
        row = found[0]
        # A run with no handled Issue (a raw title and body) gets an empty repo and number 0, which
        # apply refuses: only incoming sandbox issues are ever written to.
        repo, number = parse_issue_key(row["issue_key"] or "") or ("", 0)
        return ApplyContext(
            run_key=row["run_key"],
            issue_key=row["issue_key"] or ADHOC,
            repo=repo,
            issue_number=row["number"] if isinstance(row["number"], int) and row["number"] == number else 0,
            incoming=row["incoming"] is True,
            actions=[RecordedAction(**a) for a in row["actions"]],
            comment_markdown=TriageResult.model_validate_json(row["triage"]).comment_markdown,
        )

    def record_results(self, tenant: str, run_key: str, results) -> None:
        """Write each result onto the run's own Action; raise when any key is not one of them."""
        check_tenant(tenant)
        res = graph(tenant).query(cypher.query("record_actions"), {
            "run": run_key, "results": [{"key": a.action_key, "status": a.status, "note": a.note} for a in results]})
        updated = res.result_set[0][0] if res.result_set else 0
        if updated != len(results):
            raise RuntimeError(f"recorded {updated} of {len(results)} action results for {run_key}")
