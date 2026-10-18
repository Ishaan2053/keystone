"""The live agent routes (A10) with the agent and the graph stubbed: no FalkorDB, no LLM."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import agent, memory, schemas
from api.config import Settings
from api.main import create_app

FIXTURES = Path(__file__).resolve().parent.parent / "api" / "fixtures"
RUN = "00000000-0000-4000-8000-000000000001"
# A prefix-free run UUID, exactly as the wire carries a feedback parent.
FEEDBACK_PARENT_RUN = "00000000-0000-4000-8000-000000000009"


def fixture(name, model):
    return model.model_validate(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


class ExplodingStore:
    def load(self, *args, **kwargs):
        raise AssertionError("live mode must not serve fixtures")


@pytest.fixture
def app():
    settings = Settings(tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live")
    return create_app(settings=settings, fixture_store=ExplodingStore())


@pytest.fixture
def client(app):
    return TestClient(app)


def test_triage_runs_the_agent(client, monkeypatch):
    seen = {}

    async def run_triage(tenant, request):
        seen["args"] = (tenant, request.issue_number)
        return fixture("triage_result", schemas.TriageResult)

    monkeypatch.setattr(agent, "run_triage", run_triage)
    response = client.post("/tenants/falkordb/triage", json={"issue_number": 42})
    assert response.status_code == 200
    assert seen["args"] == ("falkordb", 42)
    schemas.TriageResult.model_validate(response.json())


@pytest.mark.parametrize(("error", "status"), [
    (agent.AmbiguousIssue(42, ["issue:o/a#42", "issue:o/b#42"]), 409),
    (agent.IssueNotFound("no issue number 42 in the graph"), 404),
    (RuntimeError("graph down"), 503),
    (KeyError("module"), 503),  # a bug is not a missing issue
])
def test_triage_errors(client, monkeypatch, error, status):
    async def run_triage(tenant, request):
        raise error

    monkeypatch.setattr(agent, "run_triage", run_triage)
    response = client.post("/tenants/falkordb/triage", json={"issue_number": 42})
    assert response.status_code == status
    assert "graph down" not in response.text


def test_unknown_tenant_is_404_before_the_agent(client, monkeypatch):
    async def run_triage(tenant, request):
        raise AssertionError("must not run")

    monkeypatch.setattr(agent, "run_triage", run_triage)
    assert client.post("/tenants/nope/triage", json={"issue_number": 42}).status_code == 404


@pytest.mark.parametrize("payload", [
    # One half of the pair, or a blank reference, is not lineage.
    {"issue_key": "issue:acme/widgets#1", "feedback_key": "feedback:ui:1"},
    {"issue_key": "issue:acme/widgets#1", "feedback_parent_run": FEEDBACK_PARENT_RUN},
    {"issue_key": "issue:acme/widgets#1", "feedback_parent_run": FEEDBACK_PARENT_RUN, "feedback_key": "  "},
    {"issue_key": "issue:acme/widgets#1", "feedback_parent_run": f"run:{FEEDBACK_PARENT_RUN}", "feedback_key": "feedback:ui:1"},
    # Context is only meaningful for one exact issue.
    {"issue_number": 42, "feedback_parent_run": FEEDBACK_PARENT_RUN, "feedback_key": "feedback:ui:1"},
    {"title": "Widget crashes", "feedback_parent_run": FEEDBACK_PARENT_RUN, "feedback_key": "feedback:ui:1"},
])
def test_triage_rejects_incomplete_or_mismatched_feedback_context(client, monkeypatch, payload):
    async def run_triage(tenant, request):
        raise AssertionError("an invalid request must not reach the agent")

    monkeypatch.setattr(agent, "run_triage", run_triage)
    assert client.post("/tenants/falkordb/triage", json=payload).status_code == 422


def test_triage_without_a_coordinator_is_silent_manual_triage(app, monkeypatch):
    """No ``app.state.notifications``: the context is accepted, and triage stays ordinary."""
    seen = []

    async def run_triage(tenant, request):
        seen.append((tenant, request.issue_key, request.feedback_key))
        return fixture("triage_result", schemas.TriageResult)

    monkeypatch.setattr(agent, "run_triage", run_triage)
    app.state.notifications = None
    response = TestClient(app).post("/tenants/falkordb/triage", json={
        "issue_key": "issue:acme/widgets#1",
        "feedback_parent_run": FEEDBACK_PARENT_RUN,
        "feedback_key": "feedback:ui:1",
    })
    assert response.status_code == 200
    assert seen == [("falkordb", "issue:acme/widgets#1", "feedback:ui:1")]


def test_runs_come_from_the_graph(client, monkeypatch):
    runs = fixture("runs", schemas.RunsResponse).runs
    monkeypatch.setattr(memory, "list_runs", lambda tenant, limit: runs[:limit])
    response = client.get("/tenants/falkordb/runs?limit=1")
    assert response.status_code == 200
    assert response.json() == {"runs": [runs[0].model_dump()]}


def test_a_run_is_recall_run(client, monkeypatch):
    stored = fixture("recall_run", schemas.ToolResult)
    asked = {}

    def recall_run(tenant, run_key):
        asked["key"] = run_key
        return stored

    monkeypatch.setattr("api.tools.recall_run", recall_run)
    response = client.get(f"/tenants/falkordb/runs/{RUN}")
    assert response.status_code == 200
    assert asked["key"] == f"run:{RUN}"
    assert response.json()["tool"] == "recall_run"


def test_an_unknown_run_is_404(client, monkeypatch):
    empty = schemas.ToolResult(tool="recall_run", data=None, cypher=["MATCH"], params={}, evidence=[], ms=1, empty=True)
    monkeypatch.setattr("api.tools.recall_run", lambda tenant, run_key: empty)
    assert client.get(f"/tenants/falkordb/runs/{RUN}").status_code == 404


def test_feedback_records_through_memory(client, monkeypatch):
    seen = {}

    def record_feedback(tenant, run_key, request, *, given_by):
        seen["args"] = (tenant, run_key, request.action_key, request.verdict, request.scope, given_by)
        return schemas.FeedbackResult(feedback_key="feedback:1", action_key=request.action_key,
                                      verdict="reject", rule_key="rule:1")

    monkeypatch.setattr(memory, "record_feedback", record_feedback)
    response = client.post(f"/tenants/falkordb/runs/{RUN}/feedback",
                           json={"action_key": "action:1", "verdict": "reject", "reason": "on leave"})
    assert response.status_code == 200
    assert response.json()["rule_key"] == "rule:1"
    assert seen["args"] == ("falkordb", f"run:{RUN}", "action:1", "reject", "module", "ui")


@pytest.mark.parametrize(("error", "status", "leaks"), [
    (memory.FeedbackNotFound("unknown run: run:x"), 404, "run:x"),
    (memory.FeedbackRefused("assign action action:1 has no module for a module-scoped rule"), 422, None),
    (RuntimeError("graph down"), 503, "graph down"),
    (KeyError("x"), 503, "'x'"),  # a bug is not a missing run
    (ValueError("too many values to unpack"), 503, "unpack"),  # nor a refused request
])
def test_feedback_errors(client, monkeypatch, error, status, leaks):
    def record_feedback(*args, **kwargs):
        raise error

    monkeypatch.setattr(memory, "record_feedback", record_feedback)
    response = client.post(f"/tenants/falkordb/runs/{RUN}/feedback", json={"action_key": "action:1", "verdict": "accept"})
    assert response.status_code == status
    if leaks:
        assert leaks not in response.text
    else:
        assert response.json() == {"detail": str(error)}


def test_ask_runs_the_agent(client, monkeypatch):
    seen = {}

    def ask(tenant, question):
        seen["args"] = (tenant, question)
        return fixture("ask", schemas.AskResult)

    monkeypatch.setattr(agent, "ask", ask)
    response = client.post("/tenants/falkordb/ask", json={"question": "Why graph?"})
    assert response.status_code == 200
    assert seen["args"] == ("falkordb", "Why graph?")
    schemas.AskResult.model_validate(response.json())


def test_ask_errors_are_sanitized_and_unknown_tenants_404(client, monkeypatch):
    def ask(tenant, question):
        raise RuntimeError("graph down")

    monkeypatch.setattr(agent, "ask", ask)
    response = client.post("/tenants/falkordb/ask", json={"question": "Why graph?"})
    assert response.status_code == 503
    assert "graph down" not in response.text
    assert client.post("/tenants/nope/ask", json={"question": "Why graph?"}).status_code == 404


def test_feedback_openapi_has_no_409(client):
    responses = client.get("/openapi.json").json()["paths"]["/tenants/{tenant}/runs/{run}/feedback"]["post"]["responses"]
    assert {"404", "422", "503"} <= responses.keys()
    assert "409" not in responses
