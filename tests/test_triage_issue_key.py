"""TriageRequest.issue_key: a full key triages exactly that issue (no FalkorDB, no LLM)."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api import agent, schemas
from api.config import Settings
from api.main import create_app

FIXTURES = Path(__file__).resolve().parent.parent / "api" / "fixtures"
KEY = "issue:FalkorDB/FalkorDB#12"


def triage_fixture() -> schemas.TriageResult:
    raw = json.loads((FIXTURES / "triage_result.json").read_text(encoding="utf-8"))
    return schemas.TriageResult.model_validate(raw)


class ExplodingStore:
    def load(self, *args, **kwargs):
        raise AssertionError("live mode must not serve fixtures")


@pytest.fixture
def client():
    settings = Settings(tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live")
    return TestClient(create_app(settings=settings, fixture_store=ExplodingStore()))


def test_issue_key_mode_parses_alone():
    parsed = schemas.TriageRequest.model_validate({"issue_key": KEY})
    assert (parsed.issue_key, parsed.issue_number, parsed.title, parsed.body) == (KEY, None, None, "")


@pytest.mark.parametrize("key", [
    "FalkorDB/FalkorDB#12",
    "issue:FalkorDB/FalkorDB",
    "issue:FalkorDB/FalkorDB#",
    "issue:FalkorDB/FalkorDB#0",
    "issue:FalkorDB/FalkorDB#012",
    "issue:FalkorDB/FalkorDB#fixture-demo",
    "issue:FalkorDB#12",
    "issue:FalkorDB/FalkorDB/extra#12",
    "issue:https://github.com/FalkorDB/FalkorDB#12",
    "issue:FalkorDB/FalkorDB#12 ",
    "adhoc",
    "",
])
def test_issue_key_format_is_validated(key):
    with pytest.raises(ValidationError):
        schemas.TriageRequest.model_validate({"issue_key": key})


@pytest.mark.parametrize("extra", [{"issue_number": 12}, {"title": "x"}, {"body": "x"}, {"body": ""}])
def test_issue_key_combined_with_another_mode_is_rejected(extra):
    with pytest.raises(ValidationError):
        schemas.TriageRequest.model_validate({"issue_key": KEY, **extra})


def test_run_triage_uses_the_key_directly(monkeypatch):
    seen = []

    async def triage_issue(tenant, issue_key):
        seen.append((tenant, issue_key))
        return triage_fixture()

    def resolve_issue_number(tenant, number):
        raise AssertionError("a full key must not be resolved by number")

    monkeypatch.setattr(agent, "triage_issue", triage_issue)
    monkeypatch.setattr(agent, "resolve_issue_number", resolve_issue_number)
    asyncio.run(agent.run_triage("falkordb", schemas.TriageRequest(issue_key=KEY)))
    assert seen == [("falkordb", KEY)]


def test_unknown_issue_key_raises_issue_not_found(monkeypatch):
    def load_issue(tenant, issue_key):
        raise agent.IssueNotFound(f"unknown issue: {issue_key}")

    monkeypatch.setattr(agent, "load_issue", load_issue)
    with pytest.raises(agent.IssueNotFound):
        asyncio.run(agent.run_triage("falkordb", schemas.TriageRequest(issue_key=KEY)))


def test_route_triages_by_key_and_unknown_key_is_404(client, monkeypatch):
    seen = []

    async def triage_issue(tenant, issue_key):
        seen.append(issue_key)
        if issue_key != KEY:
            raise agent.IssueNotFound(f"unknown issue: {issue_key}")
        return triage_fixture()

    monkeypatch.setattr(agent, "triage_issue", triage_issue)
    ok = client.post("/tenants/falkordb/triage", json={"issue_key": KEY})
    assert ok.status_code == 200
    schemas.TriageResult.model_validate(ok.json())
    missing = client.post("/tenants/falkordb/triage", json={"issue_key": "issue:FalkorDB/FalkorDB#99999"})
    assert missing.status_code == 404
    assert seen == [KEY, "issue:FalkorDB/FalkorDB#99999"]


@pytest.mark.parametrize("payload", [
    {"issue_key": "issue:FalkorDB/FalkorDB#fixture-demo"},
    {"issue_key": KEY, "issue_number": 12},
    {"issue_key": KEY, "title": "x"},
])
def test_route_rejects_invalid_issue_key_requests_with_422(client, monkeypatch, payload):
    async def run_triage(tenant, request):
        raise AssertionError("an invalid request must not reach the agent")

    monkeypatch.setattr(agent, "run_triage", run_triage)
    assert client.post("/tenants/falkordb/triage", json=payload).status_code == 422
