from __future__ import annotations

import time

from pydantic import ValidationError

from api.schemas import RecallRunData, RunSummary, ToolResult, TriageRequest, TriageResult
from api.tools._run import result, rows


def recall_run_data(row: dict) -> RecallRunData | None:
    """Map one recall-run row (recall_run.cypher / webhook_latest_run.cypher) to ``RecallRunData``.

    Both queries return the same columns, except that only recall_run.cypher returns the raw run's
    ``raw_title`` and ``raw_body`` (a webhook run always handled an issue): a raw run recalls them as
    ``request`` so it can be re-triaged; an issue run, or a raw run recorded before they were kept,
    has ``request`` None. A row whose stored TriageResult is absent or invalid is ``None`` so the
    caller reports an absent run rather than a half-built one. The input is not mutated.
    """
    values = dict(row)
    raw_title, raw_body = values.pop("raw_title", None), values.pop("raw_body", None)
    request = (TriageRequest(title=raw_title, body=raw_body if isinstance(raw_body, str) else "")
               if isinstance(raw_title, str) and raw_title.strip() else None)
    try:
        triage = TriageResult.model_validate_json(values.pop("triage"))
        evidence, actions = values.pop("evidence"), values.pop("actions")
        return RecallRunData(run=RunSummary(**values), evidence=evidence, actions=actions, triage=triage,
                             request=request)
    except ValidationError:
        return None


def recall_run(tenant: str, run_key: str) -> ToolResult:
    """A past triage run: what it concluded, every key it cited, and its actions with any feedback
    and the rules that feedback created. The TriageResult is stored as JSON on AgentRun.triage.
    An unknown run, or one recorded without its TriageResult, summary or model, returns empty."""
    started = time.perf_counter()
    params = {"run": run_key}
    found, text = rows(tenant, "recall_run", params)
    if not found:
        return result("recall_run", tenant, started, None, [text], params, [], True)
    data = recall_run_data(found[0])
    if data is None:
        return result("recall_run", tenant, started, None, [text], params, [], True)
    cited = [run_key, *data.evidence]
    for a in data.actions:
        cited += [a.action_key, *(f.feedback_key for f in a.feedback), *(f.rule_key for f in a.feedback)]
    return result("recall_run", tenant, started, data.model_dump(), [text], params, cited, False)
