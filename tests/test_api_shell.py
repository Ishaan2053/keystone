import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import schemas
from api.config import Settings
from api.fixture_store import FixtureStore
from api.main import create_app
from api.webhooks import WebhookWorker


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "api" / "fixtures"
RUN_UUID = "00000000-0000-4000-8000-000000000001"
ACTION_KEY = "action:00000000-0000-4000-8000-000000000002"


@pytest.fixture
def client() -> TestClient:
    settings = Settings(tenants=("falkordb", "ghost"), cors_origins=("http://localhost:3001",))
    return TestClient(create_app(settings=settings, fixture_store=FixtureStore.default()))


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "fixture_name", "model"),
    [
        ("post", "/tenants/falkordb/triage", {"json": {"issue_number": 42}}, "triage_result", schemas.TriageResult),
        ("get", f"/tenants/falkordb/runs/{RUN_UUID}", {}, "recall_run", schemas.ToolResult),
        ("post", "/tenants/falkordb/ask", {"json": {"question": "Why graph?"}}, "ask", schemas.AskResult),
        ("get", "/tenants/falkordb/path?from=issue%3AFalkorDB%2FFalkorDB%23fixture-demo&to=label%3AFalkorDB%2FFalkorDB%2Fgraph", {}, "path", schemas.ToolResult),
        ("get", "/tenants/falkordb/subgraph?keys=issue%3AFalkorDB%2FFalkorDB%23fixture-demo", {}, "subgraph", schemas.Subgraph),
        ("get", "/tenants/falkordb/stats", {}, "stats", schemas.Stats),
    ],
)
def test_json_routes_return_exact_fixtures(client, method, path, kwargs, fixture_name, model):
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 200
    assert response.json() == fixture(fixture_name)
    model.model_validate(response.json())


def test_tenants_match_configured_slug_names(client):
    response = client.get("/tenants")

    assert response.status_code == 200
    assert response.json() == {
        "tenants": [
            {"slug": "falkordb", "name": "FalkorDB"},
            {"slug": "ghost", "name": "Ghost"},
        ]
    }


def test_runs_list_slices_and_sorts_descending(client):
    response = client.get("/tenants/falkordb/runs?limit=1")

    body = response.json()

    assert response.status_code == 200
    assert body == fixture("runs")
    assert len(body["runs"]) == 1
    assert [run["at"] for run in body["runs"]] == sorted(
        [run["at"] for run in body["runs"]], reverse=True
    )


def test_apply_empty_body_defaults_to_dry_run_fixture(client):
    response = client.post(f"/tenants/falkordb/runs/{RUN_UUID}/apply", json={})

    assert response.status_code == 200
    assert response.json() == fixture("apply_dry_run")
    schemas.ApplyResult.model_validate(response.json())


def test_feedback_accept_and_reject_fixtures_differ(client):
    accepted = client.post(
        f"/tenants/falkordb/runs/{RUN_UUID}/feedback",
        json={"action_key": ACTION_KEY, "verdict": "accept"},
    )
    rejected = client.post(
        f"/tenants/falkordb/runs/{RUN_UUID}/feedback",
        json={"action_key": ACTION_KEY, "verdict": "reject", "reason": "wrong owner"},
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 200
    assert accepted.json() == fixture("feedback_accept")
    assert rejected.json() == fixture("feedback_reject")
    assert accepted.json()["rule_key"] is None
    assert rejected.json()["rule_key"] is not None


def test_recall_uses_tool_result_wrapper_with_triage(client):
    response = client.get(f"/tenants/falkordb/runs/{RUN_UUID}")

    assert response.status_code == 200
    body = response.json()
    assert body["tool"] == "recall_run"
    assert body["data"]["triage"] == fixture("triage_result")


def test_unknown_tenant_and_run_are_404(client):
    assert client.post("/tenants/unknown/triage", json={"issue_number": 42}).status_code == 404
    assert client.get("/tenants/unknown/runs").status_code == 404
    assert client.get("/tenants/unknown/stats").status_code == 404
    assert client.get("/tenants/unknown/path?from=a&to=b").status_code == 404
    assert client.get("/tenants/unknown/subgraph?keys=a").status_code == 404
    assert client.post("/tenants/unknown/ask", json={"question": "x"}).status_code == 404
    assert client.post(f"/tenants/unknown/runs/{RUN_UUID}/apply", json={}).status_code == 404

    other = "00000000-0000-4000-8000-000000000099"
    assert client.get(f"/tenants/falkordb/runs/{other}").status_code == 404
    assert client.post(f"/tenants/falkordb/runs/{other}/apply", json={}).status_code == 404
    assert client.post(
        f"/tenants/falkordb/runs/{other}/feedback",
        json={"action_key": ACTION_KEY, "verdict": "accept"},
    ).status_code == 404


def test_configured_tenant_without_fixture_dataset_is_503(client):
    response = client.post("/tenants/ghost/triage", json={"issue_number": 42})

    assert response.status_code == 503


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/tenants/falkordb/triage", {"json": {}}),
        ("post", "/tenants/falkordb/triage", {"json": {"issue_number": 1, "title": "x"}}),
        ("post", "/tenants/falkordb/triage", {"json": {"issue_number": 1, "body": "x"}}),
        ("post", "/tenants/falkordb/triage", {"json": {"issue_number": 0}}),
        ("post", "/tenants/falkordb/triage", {"json": {"title": " "}}),
        ("post", "/tenants/falkordb/triage", {"json": {"title": "x", "extra": True}}),
        ("post", "/tenants/falkordb/ask", {"json": {"question": " "}}),
        ("post", "/tenants/falkordb/ask", {"json": {"question": "x", "extra": True}}),
        ("post", f"/tenants/falkordb/runs/{RUN_UUID}/feedback", {"json": {"action_key": ACTION_KEY, "verdict": "reject"}}),
        ("post", f"/tenants/falkordb/runs/{RUN_UUID}/feedback", {"json": {"action_key": ACTION_KEY, "verdict": "accept", "scope": "tenant"}}),
        ("post", f"/tenants/falkordb/runs/{RUN_UUID}/feedback", {"json": {"action_key": " ", "verdict": "accept"}}),
        ("post", f"/tenants/falkordb/runs/{RUN_UUID}/feedback", {"json": {"action_key": ACTION_KEY, "verdict": "maybe"}}),
        ("post", f"/tenants/falkordb/runs/{RUN_UUID}/apply", {"json": {"dry_run": True, "extra": True}}),
        ("get", "/tenants/falkordb/path?from=&to=b", {}),
        ("get", "/tenants/falkordb/path?from=a&to=", {}),
        ("get", "/tenants/falkordb/path?from=a", {}),
        ("get", "/tenants/falkordb/subgraph", {}),
        ("get", "/tenants/falkordb/subgraph?keys=", {}),
        ("get", f"/tenants/falkordb/runs/{RUN_UUID}?extra=true", {}),
        ("get", "/tenants/falkordb/runs?limit=1&extra=true", {}),
        ("get", "/tenants/falkordb/path?from=a&to=b&extra=true", {}),
        ("get", "/tenants/falkordb/subgraph?keys=a&extra=true", {}),
        ("get", "/tenants/falkordb/stats?extra=true", {}),
        ("get", "/tenants/falkordb/runs?limit=0", {}),
        ("get", "/tenants/falkordb/runs?limit=101", {}),
        ("get", "/tenants/falkordb/runs?limit=abc", {}),
        ("get", "/tenants/falkordb/runs/not-a-uuid", {}),
    ],
)
def test_invalid_requests_are_422(client, method, path, kwargs):
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 422


def test_fixture_live_apply_is_refused(client, monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - only called on regression
        raise AssertionError("fixture mode must not open outbound sockets")

    monkeypatch.setattr("socket.socket.connect", explode)
    response = client.post(f"/tenants/falkordb/runs/{RUN_UUID}/apply", json={"dry_run": False})

    assert response.status_code == 200
    assert response.json() == fixture("apply_refused")
    assert {action["status"] for action in response.json()["actions"]} == {"refused"}


class _FakeWriter:
    """A stand-in sandbox writer (only ``writer is not None`` is inspected)."""

    def __init__(self, repo: str = "owner/repo"):
        self.repo = repo


class _FakeApplyService:
    """A stand-in shared apply service with an inspectable writer."""

    def __init__(self, *, writer=None):
        self.writer = writer

    def apply_triage(self, tenant, run_key, dry_run=True):  # pragma: no cover - wiring only
        raise AssertionError("the wiring tests never run the pipeline")


async def _fake_triage(tenant, issue_key):  # pragma: no cover - wiring only
    raise AssertionError("the wiring tests never run the pipeline")


def _live_webhook_settings(**overrides) -> Settings:
    values = dict(
        tenants=("falkordb",),
        cors_origins=("http://localhost:3001",),
        api_mode="live",
        sandbox_repo="owner/repo",
        bot_login="keystone-bot",
        webhook_secret="webhook-secret",
        public_base_url="https://keystone.example",
    )
    values.update(overrides)
    return Settings(**values)


def test_live_webhook_worker_is_built_with_every_real_adapter_and_shares_apply():
    service = _FakeApplyService(writer=_FakeWriter())

    app = create_app(
        settings=_live_webhook_settings(),
        fixture_store=FixtureStore.default(),
        apply_service=service,
        agent_triage=_fake_triage,
    )

    assert isinstance(app.state.webhook_worker, WebhookWorker)
    # The public /apply route and the webhook pipeline share one service.
    assert app.state.apply_service is service


@pytest.mark.parametrize("missing", ["triage", "writer", "public_base_url", "webhook_secret", "bot_login"])
def test_missing_live_webhook_adapter_builds_no_worker(missing):
    service = _FakeApplyService(writer=_FakeWriter())
    kwargs: dict = {"agent_triage": _fake_triage}
    settings = _live_webhook_settings()
    if missing == "triage":
        kwargs["agent_triage"] = None
    elif missing == "writer":
        service = _FakeApplyService(writer=None)
    else:
        settings = _live_webhook_settings(**{missing: None})

    app = create_app(
        settings=settings,
        fixture_store=FixtureStore.default(),
        apply_service=service,
        **kwargs,
    )

    assert app.state.webhook_worker is None


def test_mcp_reservations_return_501(client):
    for method in (client.get, client.post, client.delete):
        response = method("/mcp")
        assert response.status_code == 501
        assert response.json() == {"detail": "Hosted MCP not implemented"}


def test_webhook_receiver_fails_closed_without_a_configured_secret(client):
    # This shell client has no GITHUB_WEBHOOK_SECRET, so the verified receiver
    # cannot validate anything and answers a sanitized 503 (spec section 13).
    response = client.post("/webhooks/github", json={})

    assert response.status_code == 503
    assert response.json() == {"detail": "Webhook processing is not available"}


def test_openapi_lists_spec_surface(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    expected = {
        ("/health", "get", "200"),
        ("/tenants", "get", "200"),
        ("/tenants/{tenant}/triage", "post", "200"),
        ("/tenants/{tenant}/runs/{run}/apply", "post", "200"),
        ("/tenants/{tenant}/runs/{run}/feedback", "post", "200"),
        ("/tenants/{tenant}/runs/{run}", "get", "200"),
        ("/tenants/{tenant}/ask", "post", "200"),
        ("/tenants/{tenant}/path", "get", "200"),
        ("/tenants/{tenant}/subgraph", "get", "200"),
        ("/tenants/{tenant}/runs", "get", "200"),
        ("/tenants/{tenant}/stats", "get", "200"),
        ("/webhooks/github", "post", "202"),
        ("/webhooks/github", "post", "401"),
        ("/webhooks/github", "post", "503"),
        ("/mcp", "post", "501"),
        ("/mcp", "get", "501"),
        ("/mcp", "delete", "501"),
    }
    for path, method, status in expected:
        assert status in paths[path][method]["responses"]

    for path, method, status in expected:
        response = paths[path][method]["responses"][status]
        if status == "200":
            ref = response["content"]["application/json"]["schema"].get("$ref")
            assert ref is not None
            assert ref.rsplit("/", 1)[-1] in schema["components"]["schemas"]


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/tenants/falkordb/triage", {"json": {"issue_number": 42}}),
        ("get", "/tenants/falkordb/runs", {}),
        ("get", f"/tenants/falkordb/runs/{RUN_UUID}", {}),
        ("post", f"/tenants/falkordb/runs/{RUN_UUID}/feedback", {"json": {"action_key": ACTION_KEY, "verdict": "accept"}}),
        ("post", "/tenants/falkordb/ask", {"json": {"question": "Why graph?"}}),
    ],
)
def test_live_agent_routes_are_pending_a10_not_fixtures(method, path, kwargs):
    class ExplodingStore:
        def load(self, *args, **kwargs):
            raise AssertionError("live mode must not serve fixtures")

    settings = Settings(tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live")
    client = TestClient(create_app(settings=settings, fixture_store=ExplodingStore()))

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 503
    assert response.json() == {"detail": "Pending A10: live agent routes are not implemented yet"}
