"""The triage agent (spec section 7): an in-memory MCP client of Keystone's own server.

1. Plan: the model sees the issue and the read-only graph tools and calls one or more (LLM call 1).
2. Follow-up: it sees the results and may call more, for at most 3 tool rounds (calls 2 to 3).
3. Compose: `llm.generate_json` returns the claims as JSON (the last call). Gemini ignores
   tool_choice "none" (#16), so the answer is never asked for through `chat`.
4. Validate in code: citations must be keys some ToolResult returned as evidence, claims left
   without one are dropped, labels must be labels the tools found, reviewers must be people
   rank_reviewers scored above 0, and every empty ToolResult is stated as a claim.
5. Fallback: if any LLM call fails, a fixed pipeline (affected_modules, then rank_reviewers and
   bus_factor on the top module, then find_decisions) fills the same claims from a template.

The agent fixes the arguments the model must not choose: the tenant, the issue's own text, the
issues to exclude (the issue and its REPLAY_OF original), the logins to exclude (both authors) and
`before`. At most MAX_LLM_CALLS calls are made per triage; the budget is enforced here, not in the
prompt. Recording the run is `record_run` (A10b); this module returns the result and run fields.

    python -m api.agent --tenant falkordb --issue 'issue:FalkorDB/FalkorDB#3343'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import fastmcp
from fastmcp.exceptions import ToolError

from api import llm
from api.mcp_server import mcp
from api.schemas import Claim, ToolResult, TriageResult
from api.tools import affected_modules, bus_factor, find_decisions, rank_reviewers
from api.tools._run import result, rows

MAX_LLM_CALLS = 5   # Rule 7
MAX_ROUNDS = 3      # tool rounds (spec section 7)
MAX_CALLS_PER_ROUND = 6
MAX_CLAIMS = 3      # per section
BODY_CHARS = 4000   # of the issue body sent to the model
SECTIONS = ("labels", "reviewers", "risks", "decisions")
# Parameters the agent sets itself; the model never sees them.
INJECTED = {"tenant", "title", "body", "exclude_issues", "exclude_logins", "before", "since"}
AGENT_TOOLS = ("affected_modules", "rank_reviewers", "bus_factor", "find_decisions", "explain_path")

SYSTEM = (
    "You triage GitHub issues for an open-source org using tools over a graph of its history. "
    "Use only tool results. Cite evidence keys exactly as the tools returned them. "
    "Prefer saying there is no evidence over guessing. "
    "Pick labels only from labels that exist in the repo, as the tools returned them."
)

PLAN = (
    "Triage this issue. Start with affected_modules, then rank reviewers and check the bus factor "
    "for the most likely module, and look for past decisions that bear on it.\n\n"
    "Title: {title}\n\nBody:\n{body}"
)

COMPOSE = (
    "Write the triage for this issue from the tool results below.\n\n"
    "Title: {title}\n\nBody:\n{body}\n\n"
    "Tool results (JSON):\n{results}\n\n"
    "Rules:\n"
    "- labels: up to 3; `text` is a label name from this list only: {labels}.\n"
    "- reviewers: up to 3; `text` is a login from rank_reviewers with a score above 0.\n"
    "- risks: up to 3; state bus_factor's numbers (the top author's share of merged PRs and the number "
    "of active authors) as a risk when one person wrote half or more, and any other ownership risk.\n"
    "- decisions: up to 3; past decisions or doc passages from find_decisions that bear on this issue, "
    "one sentence each; leave it empty when none does.\n"
    "- Every claim cites the evidence keys that support it, copied exactly from the `evidence` lists "
    "above: a label with its label key and the similar issues that carry it, a reviewer with their "
    "person key, the PRs in `via` and the module, a risk with the module and the person, a decision "
    "with its decision key and its sources.\n"
    "- `summary` is one sentence on what the issue touches and who should look at it."
)

_CLAIMS = {"type": "array", "items": {
    "type": "object",
    "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "citations"],
}}
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, **{s: _CLAIMS for s in SECTIONS}},
    "required": ["summary", *SECTIONS],
}


class BudgetExceeded(llm.LLMError):
    """A triage tried to make more than MAX_LLM_CALLS calls."""


@dataclass
class Issue:
    key: str | None          # None for a raw title and body
    title: str
    body: str
    authors: list[str] = field(default_factory=list)   # the issue's and its original's
    exclude: list[str] = field(default_factory=list)   # the issue and its REPLAY_OF original
    number: int | None = None
    incoming: bool = False
    lookup: ToolResult | None = None  # the issue_context query, first in the trace


@dataclass
class Triage:
    """A TriageResult plus the AgentRun fields that record_run stores."""
    result: TriageResult
    summary: str
    model: str
    llm_calls: int
    ms: int
    fallback: bool
    at: int
    issue_key: str | None


class _Budget:
    def __init__(self, limit: int = MAX_LLM_CALLS):
        self.limit, self.used = limit, 0

    def spend(self) -> None:
        if self.used >= self.limit:
            raise BudgetExceeded(f"more than {self.limit} LLM calls")
        self.used += 1


# --- the issue ---------------------------------------------------------------------------------

def load_issue(tenant: str, issue_key: str) -> Issue:
    """The issue by key, with its authors and what to exclude. Raises LookupError when unknown.
    The lookup is kept as a ToolResult, so the issue's own key is evidence a claim may cite."""
    started = time.perf_counter()
    params = {"issue": issue_key}
    found, text = rows(tenant, "issue_context", params)
    if not found:
        raise LookupError(f"unknown issue: {issue_key}")
    r = found[0]
    authors = [f"person:{a}" for a in (r["author"], r["replay_author"]) if a]
    lookup = result("issue_context", tenant, started, r, [text], params, [r["issue"], r["replay_of"], *authors], False)
    return Issue(
        key=r["issue"], title=r["title"] or "", body=r["body"] or "",
        authors=[a for a in dict.fromkeys([r["author"], r["replay_author"]]) if a],
        exclude=[k for k in (r["issue"], r["replay_of"]) if k],
        number=r["number"], incoming=bool(r["incoming"]), lookup=lookup,
    )


# --- tools -------------------------------------------------------------------------------------

def _llm_tool(tool) -> dict:
    """An MCP tool as an LLM function, without the parameters the agent injects."""
    schema = json.loads(json.dumps(tool.input_schema))
    schema["properties"] = {k: v for k, v in schema.get("properties", {}).items() if k not in INJECTED}
    schema["required"] = [k for k in schema.get("required", []) if k not in INJECTED]
    return {"name": tool.name, "description": tool.description or "", "parameters": schema}


def _tool_args(name: str, args: dict, tenant: str, issue: Issue, before: int | None) -> dict:
    """The model's arguments for a tool, with the injected ones set by the agent."""
    if name == "affected_modules":
        return {"tenant": tenant, "title": issue.title, "body": issue.body,
                "exclude_issues": issue.exclude, "before": before}
    if name == "rank_reviewers":
        k = args.get("k") if isinstance(args.get("k"), int) and 0 < args["k"] <= 10 else 5
        return {"tenant": tenant, "module_key": args.get("module_key"), "k": k,
                "exclude_logins": issue.authors, "before": before}
    if name == "bus_factor":
        return {"tenant": tenant, "module_key": args.get("module_key"), "before": before}
    if name == "find_decisions":
        return {"tenant": tenant, "query": args.get("query") or issue.title}
    return {"tenant": tenant, "from_key": args.get("from_key"), "to_key": args.get("to_key")}


def _brief(r: ToolResult) -> dict:
    """What the model sees of a ToolResult: the answer and its evidence, not the Cypher."""
    return {"tool": r.tool, "data": r.data, "evidence": r.evidence, "empty": r.empty}


async def _call(client: fastmcp.Client, name: str, args: dict) -> ToolResult | str:
    """A ToolResult, or the error text when the tool failed (a failed tool is not a crash)."""
    res = await client.call_tool(name, args, raise_on_error=False)
    if res.is_error:
        text = " ".join(getattr(c, "text", "") for c in res.content).strip()
        return text or f"{name} failed"
    return ToolResult.model_validate(res.structured_content)


# --- validation --------------------------------------------------------------------------------

def _eligible(trace: list[ToolResult]) -> tuple[set[str], set[str]]:
    """Label names the tools found, and logins rank_reviewers scored above 0."""
    labels, logins = set(), set()
    for r in trace:
        if r.tool == "affected_modules" and not r.empty:
            labels |= {l["name"] for m in r.data["modules"] for l in m["labels"]}
        if r.tool == "rank_reviewers" and not r.empty:
            logins |= {p["login"] for p in r.data["reviewers"] if p["score"] > 0}
    return labels, logins


def _absences(trace: list[ToolResult], issue: Issue) -> dict[str, list[Claim]]:
    """A claim for every empty ToolResult, citing the key the tool was asked about when some
    ToolResult returned it as evidence (a raw title and body has no issue key to cite)."""
    evidence = {k for r in trace for k in r.evidence}
    out: dict[str, list[Claim]] = {"risks": [], "decisions": []}
    for r in trace:
        if not r.empty:
            continue
        p = r.params
        if r.tool == "affected_modules":
            text, cite, section = "No merged PR fixed a similar past issue, so no affected module was found.", issue.key, "risks"
        elif r.tool == "rank_reviewers":
            text, cite, section = f"No one has merged or reviewed a PR touching {p['module']} yet.", p["module"], "risks"
        elif r.tool == "bus_factor":
            text, cite, section = f"No merged PR touched {p['module']} in the 12 months before this issue.", p["module"], "risks"
        elif r.tool == "find_decisions":
            text, cite, section = "No recorded decision or doc passage bears on this issue.", issue.key, "decisions"
        else:
            text, cite, section = f"No graph path within 4 hops links {p['from']} and {p['to']}.", p["from"], "risks"
        out[section].append(Claim(text=text, citations=[cite] if cite in evidence else []))
    return out


def validate(raw: dict, trace: list[ToolResult], issue: Issue) -> dict[str, list[Claim]]:
    """The model's claims, kept only where they hold (spec section 7, step 5).

    Citations not in any ToolResult's evidence are dropped, then claims left with none. Labels must
    be ones the tools found and reviewers must be scored above 0 by rank_reviewers (an avoid rule
    zeroes a score). Empty results are added as claims (`_absences`).
    """
    evidence = {k for r in trace for k in r.evidence}
    labels, logins = _eligible(trace)
    out: dict[str, list[Claim]] = {}
    for section in SECTIONS:
        kept, seen = [], set()
        for c in raw.get(section) or []:
            if not isinstance(c, dict) or not isinstance(c.get("text"), str):
                continue
            text = " ".join(c["text"].split())
            if section == "reviewers":
                text = text.lstrip("@")
            cites = [k for k in dict.fromkeys(c.get("citations") or []) if isinstance(k, str) and k in evidence]
            if not text or not cites or text in seen:
                continue
            if section == "labels" and text not in labels:
                continue
            if section == "reviewers" and (text not in logins or f"person:{text}" not in cites):
                continue
            seen.add(text)
            kept.append(Claim(text=text, citations=cites))
        out[section] = kept[:MAX_CLAIMS]
    for section, claims in _absences(trace, issue).items():
        out[section] += claims
    return out


# --- the comment -------------------------------------------------------------------------------

def compose_comment(claims: dict[str, list[Claim]]) -> str:
    """The comment apply_triage posts: the claims with numbered citations, then the keys they cite.
    Logins and labels are code spans without @, so no one is pinged; apply_triage appends the run
    link and sanitizes the rest (B6)."""
    keys: list[str] = []

    def refs(c: Claim) -> str:
        nums = []
        for k in c.citations:
            if k not in keys:
                keys.append(k)
            nums.append(f"[{keys.index(k) + 1}]")
        return "".join(nums)

    def inline(cs: list[Claim]) -> str:
        return ", ".join(f"`{c.text}` {refs(c)}" for c in cs) or "none found"

    lines = ["**Keystone triage**, from this org's GitHub history in the graph.", "",
             f"- **Labels:** {inline(claims['labels'])}",
             f"- **Suggested reviewers:** {inline(claims['reviewers'])}"]
    for title, section in (("Risks", "risks"), ("Past decisions", "decisions")):
        if claims[section]:
            lines += ["", f"**{title}**"] + [f"- {c.text} {refs(c)}".rstrip() for c in claims[section]]
    if keys:
        lines += ["", "<sub>Evidence: " + " · ".join(f"[{i}] `{k}`" for i, k in enumerate(keys, 1)) + "</sub>"]
    return "\n".join(lines)


# --- the loop ----------------------------------------------------------------------------------

async def _agent(tenant: str, issue: Issue, before: int | None, budget: _Budget) -> tuple[dict, str, list[ToolResult]]:
    """The model's raw claims, the model that composed them, and the trace. Raises LLMError."""
    trace: list[ToolResult] = []
    async with fastmcp.Client(mcp) as client:
        tools = [_llm_tool(t) for t in await client.list_tools() if t.name in AGENT_TOOLS]
        names = {t["name"] for t in tools}
        body = issue.body[:BODY_CHARS]
        messages: list[dict] = [{"role": "user", "text": PLAN.format(title=issue.title, body=body)}]
        for rnd in range(MAX_ROUNDS):
            budget.spend()
            reply = await asyncio.to_thread(llm.chat, messages, tools, SYSTEM, "any" if rnd == 0 else "auto")
            if not reply.tool_calls:
                break
            messages.append(reply.turn)
            results, done = [], set()
            for call in reply.tool_calls:
                sig = json.dumps([call["name"], call["args"]], sort_keys=True, default=str)
                if call["name"] not in names:  # Gemini can call tools that were never declared (#16)
                    out: Any = {"error": f"unknown tool {call['name']}"}
                elif sig in done or len(done) >= MAX_CALLS_PER_ROUND:
                    out = {"error": "skipped: duplicate or over the per-round call limit"}
                else:
                    done.add(sig)
                    got = await _call(client, call["name"], _tool_args(call["name"], call["args"] or {}, tenant, issue, before))
                    if isinstance(got, ToolResult):
                        trace.append(got)
                        out = _brief(got)
                    else:
                        out = {"error": got}
                results.append({"id": call["id"], "name": call["name"], "result": out})
            messages.append({"role": "tool", "results": results})
        if not any(r.tool == "find_decisions" for r in trace):  # past decisions are always looked up
            got = await _call(client, "find_decisions", _tool_args("find_decisions", {}, tenant, issue, before))
            if isinstance(got, ToolResult):
                trace.append(got)
    labels, _ = _eligible(trace)
    prompt = COMPOSE.format(
        title=issue.title, body=body, labels=", ".join(sorted(labels)) or "(none found)",
        results=json.dumps([_brief(r) for r in trace], ensure_ascii=False, default=str))
    budget.spend()
    raw, model = await asyncio.to_thread(llm.generate_json, prompt, TRIAGE_SCHEMA, SYSTEM)
    return raw, model, trace


def _fallback(tenant: str, issue: Issue, before: int | None) -> tuple[dict[str, list[Claim]], str, list[ToolResult]]:
    """The fixed pipeline with no LLM (spec section 7, step 7): claims from a template."""
    trace = ([issue.lookup] if issue.lookup else []) + [affected_modules(tenant, issue.title, issue.body, issue.exclude, before)]
    claims: dict[str, list[Claim]] = {s: [] for s in SECTIONS}
    modules = trace[-1].data["modules"]
    top = modules[0] if modules else None
    if top:
        claims["labels"] = [Claim(text=l["name"], citations=[l["key"], *top["similar"][:2]]) for l in top["labels"][:2]]
        ranked = rank_reviewers(tenant, top["module"], 5, before, issue.authors)
        bus = bus_factor(tenant, top["module"], None, before)
        trace += [ranked, bus]
        for p in [p for p in ranked.data["reviewers"] if p["score"] > 0][:2]:
            claims["reviewers"].append(Claim(text=p["login"], citations=[p["person"], *p["via"][:3], top["module"]]))
        if not bus.empty:
            d = bus.data
            claims["risks"].append(Claim(
                text=f"{d['top']['login']} authored {d['top_share']:.0%} of the {d['total']} merged PRs touching "
                     f"{top['path']} in the last 12 months; {d['active_authors']} people authored any.",
                citations=[d["module"], d["top"]["key"]]))
    found = find_decisions(tenant, issue.title)
    trace.append(found)
    for d in found.data["decisions"][:2]:
        claims["decisions"].append(Claim(text=d["summary"], citations=[d["decision"], *(s["key"] for s in d["sources"])]))
    for section, extra in _absences(trace, issue).items():
        claims[section] += extra
    if top:
        who = ", ".join(c.text for c in claims["reviewers"]) or "no eligible reviewer"
        summary = f"Fallback triage: most likely touches {top['path']}; suggested reviewers: {who}."
    else:
        summary = "Fallback triage: no affected module found in the graph."
    return claims, summary, trace


async def triage(tenant: str, issue: Issue, before: int | None = None) -> Triage:
    """Triage one issue: the agent loop, or the fixed pipeline if any LLM call fails."""
    started, at = time.perf_counter(), int(time.time())
    budget = _Budget()
    head = [issue.lookup] if issue.lookup else []
    try:
        raw, model, trace = await _agent(tenant, issue, before, budget)
        trace = head + trace
        claims = validate(raw, trace, issue)
        summary = " ".join(str(raw.get("summary") or "").split()) or "Triage composed from graph tool results."
        fallback = False
    except llm.LLMError:
        claims, summary, trace = await asyncio.to_thread(_fallback, tenant, issue, before)
        model, fallback = "none", True
    result = TriageResult(
        run_key=f"run:{uuid.uuid4()}",
        **claims,
        comment_markdown=compose_comment(claims),
        trace=trace,
    )
    return Triage(result=result, summary=summary, model=model, llm_calls=budget.used,
                  ms=round((time.perf_counter() - started) * 1000), fallback=fallback, at=at,
                  issue_key=issue.key)


def check_citations(result: TriageResult) -> list[str]:
    """Problems with a result's claims: a citation no ToolResult returned, or a claim with no citation."""
    evidence = {k for r in result.trace for k in r.evidence}
    problems = []
    for section in SECTIONS:
        for c in getattr(result, section):
            if not c.citations:
                problems.append(f"{section}: no citation: {c.text}")
            problems += [f"{section}: {k} not in any ToolResult evidence" for k in c.citations if k not in evidence]
    return problems


def main() -> None:
    from ingest.fetch import load_env

    p = argparse.ArgumentParser(description="Triage one issue in the graph and print the TriageResult.")
    p.add_argument("--tenant", required=True)
    p.add_argument("--issue", required=True, help="an issue key, e.g. 'issue:FalkorDB/FalkorDB#3343'")
    args = p.parse_args()
    load_env()
    try:
        t = asyncio.run(triage(args.tenant, load_issue(args.tenant, args.issue)))
    except (LookupError, ToolError) as e:
        raise SystemExit(str(e))
    r = t.result
    print(json.dumps({s: [c.model_dump() for c in getattr(r, s)] for s in SECTIONS}, indent=1, ensure_ascii=False))
    print(r.comment_markdown)
    print(f"\nrun {r.run_key}  model={t.model}  fallback={t.fallback}  llm_calls={t.llm_calls}  "
          f"network_calls={llm.network_calls()}  ms={t.ms}")
    print("tools:", ", ".join(f"{x.tool}{' (empty)' if x.empty else ''}" for x in r.trace))
    problems = check_citations(r)
    claims = sum(len(getattr(r, s)) for s in SECTIONS)
    print(f"claims={claims}  citation problems={len(problems)}")
    for line in problems:
        print("  ", line)


if __name__ == "__main__":
    main()
