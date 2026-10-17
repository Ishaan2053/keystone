"""#68: the default app (``api.main:app``) is wired to A10's agent and A11's memory in live mode.

Builds the app the way ``uvicorn api.main:app`` does, through ``_default_app``, with a full live
environment. No FalkorDB is needed: the adapters connect only when called, and the lifespan (which
would start the worker thread) never runs here, so nothing is triaged.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api import agent, main, memory

SECRET = "s3cret"
SANDBOX = "owner/sandbox"
LIVE_ENV = {
    "API_MODE": "live",
    "TENANTS": "falkordb",
    "SANDBOX_REPO": SANDBOX,
    "GITHUB_WEBHOOK_SECRET": SECRET,
    "GITHUB_WRITE_TOKEN": "dummy",
    "KEYSTONE_BOT_LOGIN": "keystone-bot",
    "PUBLIC_BASE_URL": "https://example.com",
    "FALKORDB_HOST": "127.0.0.1",
    "FALKORDB_PASSWORD": "x",
}


def _post(client: TestClient, event: str, payload: dict):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/github", content=body, headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
    })


@pytest.fixture
def live_app(monkeypatch):
    for key, value in LIVE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PUBLIC_MCP_TENANTS", raising=False)
    return main._default_app()


def test_default_app_wires_the_real_adapters(live_app):
    worker = live_app.state.webhook_worker
    service = live_app.state.apply_service
    assert worker is not None and service is not None
    assert isinstance(service.store, memory.GraphActionStore)
    assert service.writer is not None and service.writer.repo == SANDBOX
    pipeline = worker._runner
    assert pipeline._triage is agent.triage_issue
    assert pipeline._commands._record_feedback is memory.record_feedback


def test_signed_webhook_is_not_503(live_app, monkeypatch):
    admitted = []
    monkeypatch.setattr(live_app.state.webhook_worker, "enqueue", lambda job: admitted.append(job) or True)
    client = TestClient(live_app)  # no `with`: the lifespan, and so the worker thread, never starts

    ping = _post(client, "ping", {"zen": "Keep it logically awesome."})
    assert ping.status_code == 202, ping.text

    opened = _post(client, "issues", {
        "action": "opened",
        "repository": {"full_name": SANDBOX},
        "sender": {"login": "octocat"},
        "issue": {
            "number": 42, "title": "Graph traversal is slow", "body": "It hangs.", "state": "open",
            "created_at": "2026-10-16T00:00:00Z",
            "user": {"login": "octocat", "html_url": "https://github.com/octocat", "type": "User"},
        },
    })
    assert opened.status_code == 202, opened.text
    assert opened.json() == {"status": "accepted"}
    assert [job.issue_number for job in admitted] == [42]


def test_missing_write_token_logs_why_webhooks_answer_503(monkeypatch, caplog):
    for key, value in LIVE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_WRITE_TOKEN", "")
    with caplog.at_level("WARNING", logger="api.main"):
        app = main._default_app()
    assert app.state.webhook_worker is None
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("GITHUB_WRITE_TOKEN" in w and "503" in w for w in warnings), warnings
    assert not any(SECRET in w for w in warnings)


def test_fixture_mode_default_app_has_no_live_adapters(monkeypatch, caplog):
    monkeypatch.setenv("API_MODE", "fixtures")
    with caplog.at_level("WARNING", logger="api.main"):
        app = main._default_app()
    assert app.state.webhook_worker is None
    assert app.state.apply_service is None
    assert not [r for r in caplog.records if "webhook worker not built" in r.getMessage()]
