"""A raw-title run keeps the title and body it was asked, so Re-triage works after a reload
(record_run -> AgentRun.raw_title/raw_body -> recall_run -> RecallRunData.request). No FalkorDB, no LLM."""
import asyncio
import importlib
import json
from pathlib import Path

from api import agent, cypher, memory, schemas
from api.tools.recall_run import recall_run_data

recall_tool = importlib.import_module("api.tools.recall_run")  # the module; api.tools re-exports the function

FIXTURES = Path(__file__).resolve().parent.parent / "api" / "fixtures"
KEY = "issue:FalkorDB/FalkorDB#12"


def triage_fixture() -> schemas.TriageResult:
    raw = json.loads((FIXTURES / "triage_result.json").read_text(encoding="utf-8"))
    return schemas.TriageResult.model_validate(raw)


def recorded(issue_key):
    return agent.Triage(result=triage_fixture(), summary="s", model="m", llm_calls=1, ms=5, fallback=False,
                        at=1760500000, issue_key=issue_key)


class FakeGraph:
    def __init__(self):
        self.calls = []

    def query(self, text, params):
        self.calls.append((text, params))


def capture_record(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(memory, "check_tenant", lambda tenant: None)
    monkeypatch.setattr(memory, "graph", lambda tenant: fake)
    monkeypatch.setattr(memory, "proposed_actions", lambda result: [])
    return fake


def test_record_run_stores_the_raw_title_and_body(monkeypatch):
    fake = capture_record(monkeypatch)
    memory.record_run("falkordb", recorded(None), ("Widget crashes on resize", "Steps: resize"))
    (text, params), = fake.calls
    assert text == cypher.query("record_run")
    assert (params["issue_key"], params["raw_title"], params["raw_body"]) == (
        memory.ADHOC, "Widget crashes on resize", "Steps: resize")


def test_record_run_stores_no_raw_request_for_an_issue_run(monkeypatch):
    fake = capture_record(monkeypatch)
    memory.record_run("falkordb", recorded(KEY))
    (_, params), = fake.calls
    assert (params["issue_key"], params["raw_title"], params["raw_body"]) == (KEY, None, None)


def test_record_and_recall_queries_carry_the_raw_request():
    assert "raw_title: $raw_title, raw_body: $raw_body" in cypher.query("record_run")
    assert "run.raw_title AS raw_title, run.raw_body AS raw_body" in cypher.query("recall_run")


def test_run_triage_records_a_raw_request_with_its_title_and_body(monkeypatch):
    seen = []

    async def triage(tenant, issue):
        return recorded(None)

    monkeypatch.setattr(agent, "triage", triage)
    monkeypatch.setattr(agent.memory, "record_run", lambda *args: seen.append(args))
    request = schemas.TriageRequest(title="Widget crashes on resize", body="Steps: resize")
    asyncio.run(agent.run_triage("falkordb", request))
    (tenant, _, raw), = seen
    assert (tenant, raw) == ("falkordb", ("Widget crashes on resize", "Steps: resize"))


def recall_row(**extra) -> dict:
    t = triage_fixture()
    return {"run_key": t.run_key, "at": 1760500000, "issue_key": memory.ADHOC, "summary": "s",
            "fallback": False, "model": "m", "llm_calls": 1, "ms": 5, "triage": t.model_dump_json(),
            "evidence": [], "actions": [], **extra}


def test_recall_returns_the_raw_request():
    data = recall_run_data(recall_row(raw_title="Widget crashes on resize", raw_body="Steps: resize"))
    assert data.request == schemas.TriageRequest(title="Widget crashes on resize", body="Steps: resize")


def test_recall_of_an_issue_run_or_an_older_raw_run_has_no_request():
    assert recall_run_data(recall_row(raw_title=None, raw_body=None)).request is None
    assert recall_run_data(recall_row()).request is None  # webhook_latest_run.cypher has no raw columns
    assert recall_run_data(recall_row(raw_title="  ", raw_body="x")).request is None


def test_recall_run_tool_returns_the_request_in_its_data(monkeypatch):
    row = recall_row(raw_title="Widget crashes on resize", raw_body="")
    monkeypatch.setattr(recall_tool, "rows", lambda tenant, name, params: ([row], "recall_run.cypher"))
    out = recall_tool.recall_run("falkordb", row["run_key"])
    assert not out.empty
    recalled = schemas.RecallRunData.model_validate(out.data)
    assert (recalled.request.title, recalled.request.body) == ("Widget crashes on resize", "")


def test_recall_run_data_contract_round_trips_with_and_without_request():
    raw = json.loads((FIXTURES / "recall_run.json").read_text(encoding="utf-8"))["data"]
    assert schemas.RecallRunData.model_validate(raw).request is None
    with_request = {**raw, "request": {"issue_key": None, "issue_number": None, "title": "t", "body": "b"}}
    assert schemas.RecallRunData.model_validate(with_request).model_dump(mode="json") == with_request
