"""The agent's checks that need no graph or LLM: citation validation, absences, the LLM budget."""
import pytest

from api import agent
from api.schemas import ToolResult

ISSUE = agent.Issue(key="issue:o/r#9", title="t", body="b", authors=["author"], exclude=["issue:o/r#9"])


def tool(name, data, evidence, empty=False, params=None):
    return ToolResult(tool=name, data=data, cypher=["RETURN 1"], params=params or {}, evidence=evidence,
                      ms=1, empty=empty)


LOOKUP = tool("issue_context", {}, ["issue:o/r#9", "person:author"])
MODULES = tool("affected_modules", {"modules": [{"module": "module:o/r/src/a", "labels": [
    {"key": "label:o/r/bug", "name": "bug"}]}]}, ["module:o/r/src/a", "label:o/r/bug", "issue:o/r#1"])
RANKED = tool("rank_reviewers", {"module": "module:o/r/src/a", "reviewers": [
    {"person": "person:ann", "login": "ann", "score": 2.0},
    {"person": "person:bob", "login": "bob", "score": 0.0}]},
    ["module:o/r/src/a", "person:ann", "person:bob", "pr:o/r#5"])
TRACE = [LOOKUP, MODULES, RANKED]


def claims(section, *items):
    raw = {s: [] for s in agent.SECTIONS}
    raw[section] = [{"text": t, "citations": c} for t, c in items]
    return raw


def test_citations_outside_the_evidence_are_dropped_then_uncited_claims():
    out = agent.validate(claims("risks", ("real", ["module:o/r/src/a", "pr:o/r#404"]), ("made up", ["pr:o/r#404"])),
                         TRACE, ISSUE)
    assert [(c.text, c.citations) for c in out["risks"]] == [("real", ["module:o/r/src/a"])]


def test_labels_must_be_ones_the_tools_found():
    out = agent.validate(claims("labels", ("bug", ["label:o/r/bug"]), ("feature", ["label:o/r/bug"])), TRACE, ISSUE)
    assert [c.text for c in out["labels"]] == ["bug"]


def test_reviewers_need_a_score_above_zero_and_their_own_person_key():
    raw = claims("reviewers", ("@ann", ["person:ann", "pr:o/r#5"]), ("bob", ["person:bob"]),
                 ("ann", ["pr:o/r#5"]))
    out = agent.validate(raw, TRACE, ISSUE)
    assert [(c.text, c.citations) for c in out["reviewers"]] == [("ann", ["person:ann", "pr:o/r#5"])]


def test_every_empty_result_becomes_a_claim():
    empty_bus = tool("bus_factor", None, [], empty=True, params={"module": "module:o/r/src/a"})
    no_decisions = tool("find_decisions", {"decisions": [], "chunks": []}, [], empty=True)
    out = agent.validate(claims("risks"), [*TRACE, empty_bus, no_decisions], ISSUE)
    assert out["risks"][0].citations == ["module:o/r/src/a"]
    assert out["decisions"][0].citations == ["issue:o/r#9"]
    assert agent.check_citations(out, [*TRACE, empty_bus, no_decisions]) == []


def test_the_budget_stops_the_sixth_call():
    budget = agent._Budget()
    for _ in range(agent.MAX_LLM_CALLS):
        budget.spend()
    with pytest.raises(agent.BudgetExceeded):
        budget.spend()
    assert issubclass(agent.BudgetExceeded, agent.llm.LLMError)  # so it falls back like any LLM failure


def test_the_comment_numbers_citations_and_never_pings():
    out = agent.validate(claims("reviewers", ("@ann", ["person:ann"])), TRACE, ISSUE)
    md = agent.compose_comment(out)
    assert "`ann` [1]" in md and "@ann" not in md and "[1] `person:ann`" in md


def test_an_absence_about_a_key_no_tool_returned_is_dropped_and_repeats_once():
    made_up = tool("bus_factor", None, [], empty=True, params={"module": "module:o/r/nope"})
    empty_bus = tool("bus_factor", None, [], empty=True, params={"module": "module:o/r/src/a"})
    out = agent.validate(claims("risks"), [*TRACE, made_up, empty_bus, empty_bus], ISSUE)
    assert [c.citations for c in out["risks"]] == [["module:o/r/src/a"]]


def test_k_from_the_model_accepts_whole_floats_only():
    def k(value):
        return agent._tool_args("rank_reviewers", {"module_key": "m", "k": value}, "t", ISSUE, None)["k"]
    assert (k(3), k(3.0), k(2.5), k(True), k(50), k(None)) == (3, 3, 5, 5, 5, 5)


DECISIONS = tool("find_decisions", {"decisions": [
    {"decision": "decision:1", "summary": "Use X.", "sources": [{"key": "pr:o/r#7", "url": "https://github.com/o/r/pull/7"}]}],
    "chunks": []}, ["decision:1", "pr:o/r#7"])


def test_ask_keeps_only_claims_the_trace_supports(monkeypatch):
    calls = []

    def generate_json(prompt, schema, system):
        calls.append(prompt)
        return {"answer": [{"text": "X was chosen.", "citations": ["decision:1", "pr:o/r#7", "pr:o/r#404"]},
                           {"text": "Made up.", "citations": ["pr:o/r#404"]}]}, "m"

    monkeypatch.setattr(agent, "find_decisions", lambda tenant, query: DECISIONS)
    monkeypatch.setattr(agent.llm, "generate_json", generate_json)
    out = agent.ask("t", "Why X?")
    assert [(c.text, c.citations) for c in out.answer] == [("X was chosen.", ["decision:1", "pr:o/r#7"])]
    assert out.trace == [DECISIONS] and len(calls) == 1


def test_ask_with_nothing_found_makes_no_llm_call(monkeypatch):
    empty = tool("find_decisions", {"decisions": [], "chunks": []}, [], empty=True)

    def generate_json(*args):
        raise AssertionError("no LLM call without evidence")

    monkeypatch.setattr(agent, "find_decisions", lambda tenant, query: empty)
    monkeypatch.setattr(agent.llm, "generate_json", generate_json)
    assert agent.ask("t", "Why X?").answer == []


def test_ask_falls_back_to_the_decisions_own_words(monkeypatch):
    def generate_json(*args):
        raise agent.llm.LLMError("quota")

    monkeypatch.setattr(agent, "find_decisions", lambda tenant, query: DECISIONS)
    monkeypatch.setattr(agent.llm, "generate_json", generate_json)
    out = agent.ask("t", "Why X?")
    assert [(c.text, c.citations) for c in out.answer] == [("Use X.", ["decision:1", "pr:o/r#7"])]


def test_citation_numbers_link_to_the_run_when_given():
    out = agent.validate(claims("reviewers", ("ann", ["person:ann"])), TRACE, ISSUE)
    assert "`ann` [[1]](https://ui/t/x?run=1)" in agent.compose_comment(out, "https://ui/t/x?run=1")
