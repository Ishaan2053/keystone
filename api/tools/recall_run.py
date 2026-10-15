from __future__ import annotations

import time

from pydantic import ValidationError

from api.schemas import RecallRunData, RunSummary, ToolResult, TriageResult
from api.tools._run import result, rows


def recall_run(tenant: str, run_key: str) -> ToolResult:
    """A past triage run: what it concluded, every key it cited, and its actions with any feedback
    and the rules that feedback created. The TriageResult is stored as JSON on AgentRun.triage.
    An unknown run, or one recorded without its TriageResult, summary or model, returns empty."""
    started = time.perf_counter()
    params = {"run": run_key}
    found, text = rows(tenant, "recall_run", params)
    if not found:
        return result("recall_run", tenant, started, None, [text], params, [], True)
    row = found[0]
    try:
        triage = TriageResult.model_validate_json(row.pop("triage"))
        evidence, actions = row.pop("evidence"), row.pop("actions")
        data = RecallRunData(run=RunSummary(**row), evidence=evidence, actions=actions, triage=triage)
    except ValidationError:
        return result("recall_run", tenant, started, None, [text], params, [], True)
    cited = [run_key, *evidence]
    for a in data.actions:
        cited += [a.action_key, *(f.feedback_key for f in a.feedback), *(f.rule_key for f in a.feedback)]
    return result("recall_run", tenant, started, data.model_dump(), [text], params, cited, False)
