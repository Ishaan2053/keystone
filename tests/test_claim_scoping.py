"""Claims scoped to what the graph shows: affected_modules searches the issue's own repo. The live
test is skipped when FalkorDB isn't reachable."""
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
        assert all(l["key"].startswith("label:FalkorDB/GraphRAG-SDK/") for l in m["labels"])
