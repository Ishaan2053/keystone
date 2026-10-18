"""The agent's checks that need no graph or LLM: citation validation, absences, the LLM budget."""
import pytest

from api import agent
from api.schemas import Claim, ToolResult

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
    assert "1. [`ann`](https://github.com/ann) <sup>[1](https://github.com/ann)</sup>" in md
    assert "@ann" not in md and "<summary>Evidence: 1 graph nodes</summary>" in md


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
    {"decision": "decision:1", "summary": "Use X.", "sources": [{"key": "pr:o/r#7", "url": "https://github.com/o/r/pull/7"}],
     "modules": ["module:o/r/src/a"], "score": 0.3}],
    "chunks": []}, ["decision:1", "pr:o/r#7", "module:o/r/src/a"])


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


def found(decision_score, chunk_score):
    d = DECISIONS.data["decisions"][0] | {"score": decision_score}
    c = {"chunk": "chunk:doc:o/r/README.md#0", "doc": "doc:o/r/README.md", "text": "Why\n  X:   because.",
         "score": chunk_score}
    return tool("find_decisions", {"decisions": [d], "chunks": [c]},
                ["decision:1", "pr:o/r#7", "module:o/r/src/a", c["chunk"], c["doc"]])


def ask(monkeypatch, result, reply):
    def generate_json(*args):
        if isinstance(reply, Exception):
            raise reply
        return reply, "m"

    monkeypatch.setattr(agent, "find_decisions", lambda tenant, query: result)
    monkeypatch.setattr(agent.llm, "generate_json", generate_json)
    return [(c.text, c.citations) for c in agent.ask("t", "Why X?").answer]


def test_ask_falls_back_to_the_nearest_chunk_when_no_decision_is_near(monkeypatch):
    out = ask(monkeypatch, found(0.8, 0.4), agent.llm.LLMError("quota"))
    assert out == [("Why X: because.", ["chunk:doc:o/r/README.md#0", "doc:o/r/README.md"])]


def test_ask_falls_back_to_nothing_beyond_the_cutoff(monkeypatch):
    assert ask(monkeypatch, found(0.8, 0.8), agent.llm.LLMError("quota")) == []


def test_ask_drops_a_claim_citing_only_a_module(monkeypatch):
    reply = {"answer": [{"text": "X was chosen.", "citations": ["decision:1"]},
                        {"text": "It is in src/a.", "citations": ["module:o/r/src/a"]}]}
    assert ask(monkeypatch, DECISIONS, reply) == [("X was chosen.", ["decision:1"])]


def test_ask_falls_back_when_no_model_claim_holds(monkeypatch):
    reply = {"answer": [{"text": "Made up.", "citations": ["pr:o/r#404"]}]}
    assert ask(monkeypatch, DECISIONS, reply) == [("Use X.", ["decision:1", "pr:o/r#7"])]


def test_ask_keeps_an_empty_model_answer(monkeypatch):
    assert ask(monkeypatch, DECISIONS, {"answer": []}) == []


def test_citation_numbers_link_to_the_source_else_to_the_run():
    out = {"labels": [], "reviewers": [], "decisions": [],
           "risks": [Claim(text="module:o/r/src/a has one author holding a share of 0.305555555555556",
                           citations=["module:o/r/src/a", "pr:o/r#7"])]}
    md = agent.compose_comment(out, "https://ui/t/x?run=1", "Short  summary.")
    assert "> Short summary." in md
    assert "- `module:o/r/src/a` has one author holding a share of 31% <sup>" in md
    assert "[1](https://github.com/o/r/tree/HEAD/src/a) [2](https://ui/t/x?run=1)</sup>" in md
    assert "github.com/o/r/pull" not in md  # an upstream PR link would backlink from the sandbox


def test_source_links_reach_code_docs_labels_and_profiles_only():
    assert agent._source_url("person:ann") == "https://github.com/ann"
    assert agent._source_url("module:o/r/(root)") == "https://github.com/o/r"
    assert agent._source_url("file:o/r/src/a b.c") == "https://github.com/o/r/blob/HEAD/src/a%20b.c"
    assert agent._source_url("label:o/r/component: build") == "https://github.com/o/r/labels/component%3A%20build"
    assert agent._source_url("chunk:doc:o/r/docs/x.md#2") == "https://github.com/o/r/blob/HEAD/docs/x.md"
    assert agent._source_url("issue:o/r#1") is None and agent._source_url("pr:o/r#2") is None
