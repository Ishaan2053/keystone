"""api.memory without a graph: the actions a run proposes, and the apply context the store builds."""
import pytest

from api import memory
from api.actions import ActionContextNotFound
from api.schemas import Claim, ToolResult, TriageResult

RANKED = ToolResult(tool="rank_reviewers", data={"module": "module:o/r/src/a", "reviewers": [
    {"person": "person:ann", "login": "ann", "score": 2.0}]}, cypher=["MATCH"], params={},
    evidence=["person:ann"], ms=1, empty=False)
MODULES = ToolResult(tool="affected_modules", data={"modules": [{"module": "module:o/r/src/a", "labels": [
    {"key": "label:o/r/area/core", "name": "area/core"}, {"key": "label:o/r/component: ci", "name": "component: ci"}]}]},
    cypher=["CALL"], params={}, evidence=["label:o/r/component: ci"], ms=1, empty=False)
RESULT = TriageResult(
    run_key="run:1",
    labels=[Claim(text="component: ci", citations=["issue:o/r#1"])],  # the label key comes from the trace
    reviewers=[Claim(text="ann", citations=["person:ann"])],
    risks=[], decisions=[], comment_markdown="**Keystone triage**", trace=[MODULES, RANKED])


def test_proposed_actions_carry_their_module_and_target():
    acts = memory.proposed_actions(RESULT)
    assert [(a["kind"], a["value"], a["module"], a["target"]) for a in acts] == [
        ("label", "component: ci", None, "label:o/r/component: ci"),
        ("assign", "ann", "module:o/r/src/a", "person:ann"),
        ("comment", "triage comment", None, None),
    ]
    assert all(a["key"].startswith("action:") for a in acts) and len({a["key"] for a in acts}) == 3


def row(**over):
    base = {"run_key": "run:1", "issue_key": "issue:Owner/Sandbox#7", "number": 7, "incoming": True,
            "triage": RESULT.model_dump_json(), "actions": [
                {"action_key": "action:1", "kind": "assign", "value": "ann", "module": "module:o/r/src/a",
                 "status": "proposed", "note": None, "feedback": []}]}
    return {**base, **over}


def test_load_context_for_an_incoming_issue(monkeypatch):
    monkeypatch.setattr(memory, "rows", lambda tenant, name, params: ([row()], "q"))
    ctx = memory.GraphActionStore().load_context("falkordb", "run:1")
    assert (ctx.issue_key, ctx.repo, ctx.issue_number, ctx.incoming) == ("issue:Owner/Sandbox#7", "owner/sandbox", 7, True)
    assert ctx.comment_markdown == "**Keystone triage**" and ctx.actions[0].module == "module:o/r/src/a"


def test_load_context_for_a_raw_run_cannot_be_applied(monkeypatch):
    monkeypatch.setattr(memory, "rows", lambda tenant, name, params: ([row(issue_key=None, number=None, incoming=False)], "q"))
    ctx = memory.GraphActionStore().load_context("falkordb", "run:1")
    assert (ctx.issue_key, ctx.repo, ctx.issue_number, ctx.incoming) == ("adhoc", "", 0, False)


def test_a_run_without_stored_triage_is_not_found(monkeypatch):
    monkeypatch.setattr(memory, "rows", lambda tenant, name, params: ([row(triage=None)], "q"))
    with pytest.raises(ActionContextNotFound):
        memory.GraphActionStore().load_context("falkordb", "run:1")


def test_list_runs_skips_a_malformed_run(monkeypatch):
    good = {"run_key": "run:1", "at": 2, "issue_key": "issue:o/r#1", "summary": "s", "fallback": False,
            "model": "m", "llm_calls": 4, "ms": 9}
    monkeypatch.setattr(memory, "rows", lambda tenant, name, params: ([{**good, "summary": None}, good], "q"))
    assert [r.run_key for r in memory.list_runs("falkordb", 20)] == ["run:1"]


def test_an_unknown_run_is_not_found(monkeypatch):
    monkeypatch.setattr(memory, "rows", lambda tenant, name, params: ([], "q"))
    with pytest.raises(ActionContextNotFound):
        memory.GraphActionStore().load_context("falkordb", "run:9")
