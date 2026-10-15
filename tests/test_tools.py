"""Each tool against the live tenant_falkordb graph; skipped when FalkorDB isn't reachable."""
import json
import uuid
from pathlib import Path

import pytest

from ingest.fetch import load_env

load_env()

from api.db import graph  # noqa: E402
from api.schemas import ToolResult  # noqa: E402
from api.tools import affected_modules, bus_factor, explain_path, find_decisions, rank_reviewers, recall_run  # noqa: E402

TENANT = "falkordb"
HERE = Path(__file__).parent

try:
    graph(TENANT).ro_query("RETURN 1")
except Exception as exc:  # no server, wrong password: nothing here can run
    pytest.skip(f"FalkorDB not reachable: {exc}", allow_module_level=True)


def check(res: ToolResult, tool: str) -> ToolResult:
    """Done-when for A5: a valid ToolResult with the query text, and evidence unless it says empty."""
    ToolResult.model_validate(res.model_dump())
    assert res.tool == tool
    assert res.cypher and all(q.strip() for q in res.cypher)
    assert res.evidence or res.empty
    assert "q" not in res.params or res.params["q"] == "<384-d vector>"
    return res


@pytest.fixture(scope="module")
def top_module() -> str:
    res = check(affected_modules(TENANT, "Crash when running MATCH with variable length path and OPTIONAL MATCH"), "affected_modules")
    assert not res.empty
    return res.data["modules"][0]["module"]


def test_affected_modules(top_module):
    res = check(affected_modules(TENANT, "Crash with OPTIONAL MATCH", exclude_issues=["issue:FalkorDB/FalkorDB#1333"]), "affected_modules")
    assert "issue:FalkorDB/FalkorDB#1333" not in res.evidence
    assert res.params["q"] == "<384-d vector>"


def test_affected_modules_sees_nothing_before_history():
    res = check(affected_modules(TENANT, "Crash with OPTIONAL MATCH", before=0), "affected_modules")
    assert res.empty and res.data == {"modules": []} and res.evidence == []


def test_rank_reviewers(top_module):
    res = check(rank_reviewers(TENANT, top_module, k=3), "rank_reviewers")
    assert not res.empty and 0 < len(res.data["reviewers"]) <= 3
    assert top_module in res.evidence
    scores = [r["score"] for r in res.data["reviewers"]]
    assert scores == sorted(scores, reverse=True)


def test_rank_reviewers_excludes_logins(top_module):
    first = rank_reviewers(TENANT, top_module, k=1).data["reviewers"][0]["login"]
    res = check(rank_reviewers(TENANT, top_module, exclude_logins=[first]), "rank_reviewers")
    assert first not in [r["login"] for r in res.data["reviewers"]]


def test_rank_reviewers_unknown_module():
    res = check(rank_reviewers(TENANT, "module:none/none/none"), "rank_reviewers")
    assert res.empty and res.data["reviewers"] == []


def test_bus_factor(top_module):
    res = check(bus_factor(TENANT, top_module, since=0), "bus_factor")
    assert not res.empty
    assert res.data["total"] > 0 and 0 < res.data["top_share"] <= 1
    assert res.data["top"]["key"] in res.evidence


def test_bus_factor_empty_window(top_module):
    res = check(bus_factor(TENANT, top_module, since=0, before=1), "bus_factor")
    assert res.empty and res.data["total"] == 0 and res.data["top"] is None


def test_find_decisions():
    # No Decisions until A7 runs, so `decisions` is [] while doc chunks still match.
    res = check(find_decisions(TENANT, "how do I configure the query timeout"), "find_decisions")
    assert res.data["decisions"] == [] or res.data["decisions"][0]["decision"] in res.evidence
    assert 0 < len(res.data["chunks"]) <= 5 and not res.empty
    scores = [c["score"] for c in res.data["chunks"]]
    assert scores == sorted(scores)  # cosine distance: nearest first
    assert res.params["q"] == "<384-d vector>" and len(res.cypher) == 2


def test_explain_path_reaches_a_module():
    issue, module = "issue:FalkorDB/FalkorDB#1330", "module:FalkorDB/FalkorDB/src/execution_plan"
    res = check(explain_path(TENANT, issue, module), "explain_path")
    assert not res.empty
    assert res.data["keys"][0] == issue and res.data["keys"][-1] == module
    assert len(res.data["rels"]) == len(res.data["keys"]) - 1 <= 4
    assert res.evidence == res.data["keys"]


def test_explain_path_no_path():
    res = check(explain_path(TENANT, "issue:FalkorDB/FalkorDB#1330", "person:nobody-at-all"), "explain_path")
    assert res.empty and res.data == {"keys": [], "rels": []}


@pytest.fixture
def fixture_run(request):
    """An AgentRun with evidence, an assign Action with reject Feedback and its Rule, and a label
    Action; deleted afterwards. The Rule is inactive so it can't change other tests' rankings.
    Parametrized indirectly with False, the run is recorded without its TriageResult."""
    keys = {k: f"{prefix}:{uuid.uuid4()}" for k, prefix in
            [("run", "run"), ("assign", "action"), ("label", "action"), ("feedback", "feedback"), ("rule", "rule")]}
    triage = json.loads((HERE.parent / "api/fixtures/triage_result.json").read_text(encoding="utf-8"))
    triage["run_key"] = keys["run"]
    g = graph(TENANT)
    g.query((HERE / "cypher/recall_fixture.cypher").read_text(encoding="utf-8"), {
        **keys, "at": 1792000000, "issue": "issue:FalkorDB/FalkorDB#1330", "person": "person:barakb",
        "module": "module:FalkorDB/FalkorDB/src/execution_plan",
        "triage": json.dumps(triage) if getattr(request, "param", True) else None,
    })
    try:
        yield keys
    finally:
        g.query((HERE / "cypher/delete_keys.cypher").read_text(encoding="utf-8"), {"keys": list(keys.values())})


def test_recall_run(fixture_run):
    res = check(recall_run(TENANT, fixture_run["run"]), "recall_run")
    assert not res.empty
    data = res.data
    assert data["run"]["run_key"] == fixture_run["run"] and data["triage"]["run_key"] == fixture_run["run"]
    assert set(data["evidence"]) == {"issue:FalkorDB/FalkorDB#1330", "person:barakb", "module:FalkorDB/FalkorDB/src/execution_plan"}
    actions = {a["action_key"]: a for a in data["actions"]}
    assign = actions[fixture_run["assign"]]
    assert assign["status"] == "rejected" and assign["module"] == "module:FalkorDB/FalkorDB/src/execution_plan"
    assert assign["feedback"] == [{"feedback_key": fixture_run["feedback"], "action_key": fixture_run["assign"],
                                   "verdict": "reject", "rule_key": fixture_run["rule"]}]
    assert actions[fixture_run["label"]]["feedback"] == []
    assert {fixture_run["run"], fixture_run["rule"], "person:barakb"} <= set(res.evidence)


def test_recall_run_unknown():
    res = check(recall_run(TENANT, f"run:{uuid.uuid4()}"), "recall_run")
    assert res.empty and res.data is None and res.evidence == []


@pytest.mark.parametrize("fixture_run", [False], indirect=True)
def test_recall_run_without_triage(fixture_run):
    res = check(recall_run(TENANT, fixture_run["run"]), "recall_run")
    assert res.empty and res.data is None and res.evidence == []
