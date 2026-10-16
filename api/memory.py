"""Agent memory in the tenant graph (spec section 8). Episodic: each AgentRun with its evidence and
actions, recorded by `record_run` and read back by `recall_run`. `GraphActionStore` is the
`api.actions.ActionStore` that B6's apply service runs on: the run context it applies and the
`record_actions.cypher` executor. Procedural: `record_feedback` turns a reject on an assign Action
into an 'avoid' Rule on that Person, which rank_reviewers.cypher applies itself, so the next triage
scores them 0 for the module (or everywhere, for a global rule). No LLM call. The same function backs
POST /tenants/{t}/runs/{run}/feedback and B8's `/keystone reject`.
"""
from __future__ import annotations

import time
import uuid

from pydantic import ValidationError

from api import cypher
from api.actions import ActionContextNotFound, ApplyContext, parse_issue_key
from api.db import graph
from api.schemas import FeedbackRequest, FeedbackResult, RecordedAction, RunSummary, ToolResult, TriageResult
from api.tools._run import check_tenant, rows

ADHOC = "adhoc"  # AgentRun.issue_key for a raw title and body: there is no Issue to handle


def proposed_actions(result: TriageResult) -> list[dict]:
    """The actions a run proposes, as record_run.cypher rows: a label per label claim, an assign per
    reviewer claim (with the module rank_reviewers ranked them for), and the one comment."""
    label_keys = {l["name"]: l["key"] for r in result.trace if r.tool == "affected_modules" and not r.empty
                  for m in r.data["modules"] for l in m["labels"]}
    acts = []
    for c in result.labels:  # validation keeps only labels affected_modules found, so the name has a key
        acts.append({"kind": "label", "value": c.text, "module": None, "target": label_keys.get(c.text)})
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
    """The newest runs first. A run missing a summary field is skipped, so it never hides the rest."""
    found, _ = rows(tenant, "list_runs", {"limit": limit})
    runs = []
    for row in found:
        try:
            runs.append(RunSummary(**row))
        except ValidationError:
            continue
    return runs


class GraphActionStore:
    """B6's ActionStore over the tenant graph."""

    def load_context(self, tenant: str, run_key: str) -> ApplyContext:
        found, _ = rows(tenant, "run_context", {"run": run_key})
        if not found:
            raise ActionContextNotFound("unknown run")
        row = found[0]
        try:
            actions = [RecordedAction(**a) for a in row["actions"]]
            comment = TriageResult.model_validate_json(row["triage"]).comment_markdown
        except (ValidationError, TypeError):  # no stored TriageResult: not a run that can be applied
            raise ActionContextNotFound("run has no stored triage") from None
        # A run with no handled Issue (a raw title and body) gets an empty repo and number 0, which
        # apply refuses: only incoming sandbox issues are ever written to. B6 checks that the key,
        # repo and number agree (validate_apply_context).
        repo, _ = parse_issue_key(row["issue_key"] or "") or ("", 0)
        return ApplyContext(
            run_key=row["run_key"],
            issue_key=row["issue_key"] or ADHOC,
            repo=repo,
            issue_number=row["number"] if isinstance(row["number"], int) else 0,
            incoming=row["incoming"] is True,
            actions=actions,
            comment_markdown=comment,
        )

    def record_results(self, tenant: str, run_key: str, results) -> None:
        """Write each result onto the run's own Action; raise when any key is not one of them."""
        check_tenant(tenant)
        rows_ = [{"key": a.action_key, "status": a.status, "note": a.note} for a in results]
        res = graph(tenant).query(cypher.query("record_actions"), {
            "run": run_key, "results": rows_, "keys": list(dict.fromkeys(r["key"] for r in rows_))})
        updated = res.result_set[0][0] if res.result_set else 0
        if updated != len(results):
            raise RuntimeError(f"recorded {updated} of {len(results)} action results for {run_key}")


def record_feedback(tenant: str, run_key: str, request: FeedbackRequest, *, given_by: str) -> FeedbackResult:
    """Write Feedback on an Action that `run_key` proposed and, for a reject on an assign, the Rule.

    Raises LookupError when the run or the action (as proposed by that run) doesn't exist, or an
    assign action targets no Person, and ValueError for a module-scoped reject on an assign action
    with no module. A reject of an Action that is already rejected returns that Feedback and Rule.
    """
    check_tenant(tenant)
    g = graph(tenant)
    params = {"run": run_key, "action": request.action_key}
    found = g.ro_query(cypher.query("feedback_target"), params).result_set
    if not found:
        raise LookupError(f"unknown run: {run_key}")
    _, action, kind, module, person, login, rejected = found[0]
    if action is None:
        raise LookupError(f"run {run_key} did not propose action {request.action_key}")

    if request.verdict == "reject" and rejected is not None:
        return FeedbackResult(
            feedback_key=rejected["feedback"], action_key=action, verdict="reject", rule_key=rejected["rule"]
        )

    rule = scope = text = None
    if request.verdict == "reject" and kind == "assign":
        if person is None:
            raise LookupError(f"assign action {action} targets no Person")
        if request.scope == "module" and not module:
            raise ValueError(f"assign action {action} has no module for a module-scoped rule")
        rule = f"rule:{uuid.uuid4()}"
        scope = module if request.scope == "module" else None
        text = f"avoid {login} for {scope or 'every module'}: {request.reason.strip()}"

    res = g.query(cypher.query("record_feedback"), {
        **params,
        "feedback": f"feedback:{uuid.uuid4()}",
        "verdict": request.verdict,
        "reason": request.reason.strip(),
        "given_by": given_by,
        "at": int(time.time()),
        "rule": rule,
        "scope": scope,
        "text": text,
    }).result_set
    if not res:  # the action went away between the read and the write
        raise LookupError(f"run {run_key} did not propose action {request.action_key}")
    feedback_key, action_key, verdict, rule_key = res[0]
    return FeedbackResult(feedback_key=feedback_key, action_key=action_key, verdict=verdict, rule_key=rule_key)
