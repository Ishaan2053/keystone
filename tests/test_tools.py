"""Each tool against the live tenant_falkordb graph; skipped when FalkorDB isn't reachable."""
import os

import pytest

from ingest.fetch import load_env

# Take only FALKORDB_* from .env, so other keys (DRY_RUN, API_MODE...) don't leak into later tests.
_before = set(os.environ)
load_env()
for _key in set(os.environ) - _before:
    if not _key.startswith("FALKORDB_"):
        del os.environ[_key]

from api.db import graph  # noqa: E402
from api.schemas import ToolResult  # noqa: E402
from api.tools import affected_modules, bus_factor, rank_reviewers  # noqa: E402

TENANT = "falkordb"

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
