"""Claims scoped to what the graph shows: affected_modules searches the issue's own repo, and a label
claim cites only issues that carry that label. The live test is skipped when FalkorDB isn't reachable."""
import os

import pytest

from api import agent
from api.schemas import ToolResult


def tool(name, data, evidence, empty=False, params=None):
    return ToolResult(tool=name, data=data, cypher=["RETURN 1"], params=params or {}, evidence=evidence,
                      ms=1, empty=empty)


def lookup(key, replay_of=None):
    return tool("issue_context", {"issue": key, "replay_of": replay_of}, [k for k in (key, replay_of) if k])


ISSUE = agent.Issue(key="issue:o/r#9", title="t", body="b", authors=["author"], exclude=["issue:o/r#9"],
                    lookup=lookup("issue:o/r#9"))
MODULES = tool("affected_modules", {"modules": [{
    "module": "module:o/r/src/a", "path": "src/a", "evidence": 2, "prs": ["pr:o/r#5"],
    "similar": ["issue:o/r#1", "issue:o/r#2"],
    "labels": [{"key": "label:o/r/bug", "name": "bug", "issues": ["issue:o/r#1"]},
               {"key": "label:o/r/perf", "name": "perf", "issues": ["issue:o/r#2"]}]}]},
    ["module:o/r/src/a", "pr:o/r#5", "issue:o/r#1", "issue:o/r#2", "label:o/r/bug", "label:o/r/perf"])
TRACE = [ISSUE.lookup, MODULES]


def labels(*items):
    raw = {s: [] for s in agent.SECTIONS}
    raw["labels"] = [{"text": t, "citations": c} for t, c in items]
    return agent.validate(raw, TRACE, ISSUE)["labels"]


def test_a_label_claim_citing_an_issue_without_that_label_is_dropped():
    out = labels(("bug", ["label:o/r/bug", "issue:o/r#2"]),   # #2 carries perf, not bug
                 ("perf", ["label:o/r/perf", "issue:o/r#2"]))
    assert [(c.text, c.citations) for c in out] == [("perf", ["label:o/r/perf", "issue:o/r#2"])]


def test_a_label_claim_citing_its_own_issues_or_only_its_key_is_kept():
    out = labels(("bug", ["label:o/r/bug", "issue:o/r#1"]), ("perf", ["label:o/r/perf"]))
    assert [c.text for c in out] == ["bug", "perf"]


def test_the_issue_itself_is_not_a_carrier():
    assert labels(("bug", ["label:o/r/bug", "issue:o/r#9"])) == []


@pytest.mark.parametrize("issue, repo", [
    (ISSUE, "o/r"),
    (agent.Issue(key="issue:me/sandbox#3", title="t", body="", incoming=True,
                 lookup=lookup("issue:me/sandbox#3", "issue:Org/Up-stream.js#42")), "Org/Up-stream.js"),
    (agent.Issue(key="issue:me/sandbox#4", title="t", body="", incoming=True,
                 lookup=lookup("issue:me/sandbox#4")), None),   # nothing replayed: search every repo
    (agent.Issue(key=None, title="t", body=""), None),           # raw title and body
])
def test_affected_modules_gets_the_issue_repo(issue, repo):
    assert agent._repo(issue) == repo
    assert agent._tool_args("affected_modules", {"repo": "x/y"}, "t", issue, None)["repo"] == repo


def test_the_model_never_sees_the_repo_parameter():
    class Tool:
        name, description = "affected_modules", ""
        input_schema = {"properties": {"tenant": {}, "title": {}, "repo": {}, "k": {}}, "required": ["tenant", "repo"]}

    spec = agent._llm_tool(Tool())
    assert set(spec["parameters"]["properties"]) == {"k"} and spec["parameters"]["required"] == []


def test_fallback_labels_cite_only_their_own_issues(monkeypatch):
    calls = {}

    def fake_modules(tenant, title, body, exclude, before, repo):
        calls["repo"] = repo
        return MODULES

    ranked = tool("rank_reviewers", {"module": "module:o/r/src/a", "reviewers": []}, [], empty=True,
                  params={"module": "module:o/r/src/a"})
    bus = tool("bus_factor", None, [], empty=True, params={"module": "module:o/r/src/a"})
    found = tool("find_decisions", {"decisions": [], "chunks": []}, [], empty=True)
    monkeypatch.setattr(agent, "affected_modules", fake_modules)
    monkeypatch.setattr(agent, "rank_reviewers", lambda *a: ranked)
    monkeypatch.setattr(agent, "bus_factor", lambda *a: bus)
    monkeypatch.setattr(agent, "find_decisions", lambda *a: found)
    claims, _, trace = agent._fallback("t", ISSUE, None)
    assert calls["repo"] == "o/r"
    assert [(c.text, c.citations) for c in claims["labels"]] == [
        ("bug", ["label:o/r/bug", "issue:o/r#1"]), ("perf", ["label:o/r/perf", "issue:o/r#2"])]
    assert agent.check_citations(claims, trace) == []


def test_live_affected_modules_keeps_to_the_repo():
    from ingest.fetch import load_env

    before = set(os.environ)
    load_env()
    for key in set(os.environ) - before:
        if not key.startswith("FALKORDB_"):
            del os.environ[key]
    from api.db import graph
    from api.tools import affected_modules

    try:
        graph("falkordb").ro_query("RETURN 1")
    except Exception as exc:
        pytest.skip(f"FalkorDB not reachable: {exc}")
    res = affected_modules("falkordb", "Support table extraction from PDF documents",
                           repo="FalkorDB/GraphRAG-SDK")
    assert not res.empty and res.params["repo"] == "FalkorDB/GraphRAG-SDK"
    for m in res.data["modules"]:
        assert m["module"].startswith("module:FalkorDB/GraphRAG-SDK/")
        assert all(i.startswith("issue:FalkorDB/GraphRAG-SDK#") for i in m["similar"])
        for l in m["labels"]:
            assert set(l["issues"]) <= set(res.evidence) and l["key"].startswith("label:FalkorDB/GraphRAG-SDK/")
