from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api import schemas
from api.actions import ActionContextNotFound
from api.config import Settings
from api.fixture_store import FixtureStore
from api.main import create_app


RUN_UUID = "00000000-0000-4000-8000-000000000001"


class FakeApplyService:
    def __init__(self, *, result=None, error=None):
        self.calls = []
        self.result = result or schemas.ApplyResult(
            run_key=f"run:{RUN_UUID}",
            dry_run=True,
            actions=[],
        )
        self.error = error

    def apply_triage(self, tenant, run_key, dry_run=True):
        self.calls.append((tenant, run_key, bool(dry_run)))
        if self.error is not None:
            raise self.error
        return self.result


def live_client(service=None, tenants=("falkordb",), sandbox_repo="owner/repo"):
    settings = Settings(
        tenants=tenants,
        cors_origins=("http://localhost:3001",),
        api_mode="live",
        sandbox_repo=sandbox_repo,
    )
    return TestClient(
        create_app(settings=settings, fixture_store=FixtureStore.default(), apply_service=service)
    )


def test_live_apply_defaults_to_dry_run():
    service = FakeApplyService()
    client = live_client(service)
    response = client.post(f"/tenants/falkordb/runs/{RUN_UUID}/apply", json={})
    assert response.status_code == 200
    assert service.calls == [("falkordb", f"run:{RUN_UUID}", True)]


def test_public_live_write_refused_before_service_execution():
    service = FakeApplyService()
    client = live_client(service)
    response = client.post(f"/tenants/falkordb/runs/{RUN_UUID}/apply", json={"dry_run": False})
    assert response.status_code == 403
    assert service.calls == []


def test_unknown_run_is_404():
    service = FakeApplyService(error=ActionContextNotFound("missing"))
    client = live_client(service)
    response = client.post(f"/tenants/falkordb/runs/{RUN_UUID}/apply", json={})
    assert response.status_code == 404
    assert service.calls == [("falkordb", f"run:{RUN_UUID}", True)]


def test_fixture_apply_never_calls_service():
    service = FakeApplyService()
    settings = Settings(tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="fixtures")
    client = TestClient(create_app(settings=settings, fixture_store=FixtureStore.default(), apply_service=service))
    response = client.post(f"/tenants/falkordb/runs/{RUN_UUID}/apply", json={"dry_run": False})
    assert response.status_code == 200
    assert service.calls == []


def test_live_configured_non_fixture_tenant_allowed():
    service = FakeApplyService()
    client = live_client(service, tenants=("ghost",))
    response = client.post(f"/tenants/ghost/runs/{RUN_UUID}/apply", json={})
    assert response.status_code == 200
    assert service.calls == [("ghost", f"run:{RUN_UUID}", True)]
