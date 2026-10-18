from fastapi.testclient import TestClient

from api.config import Settings
from api.fixture_store import FixtureStore
from api.main import create_app

ALLOWED_VERCEL = "https://keystone.vercel.app"


def client() -> TestClient:
    settings = Settings(
        tenants=("falkordb",),
        cors_origins=("http://localhost:3001", ALLOWED_VERCEL),
    )
    return TestClient(create_app(settings=settings, fixture_store=FixtureStore.default()))


def preflight(origin: str):
    return client().options(
        "/tenants/falkordb/triage",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )


def test_allowed_local_and_vercel_preflights_are_exact_origin():
    for origin in ("http://localhost:3001", ALLOWED_VERCEL):
        response = preflight(origin)

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers.get("access-control-allow-credentials") != "true"


def test_disallowed_origins_are_not_permitted():
    for origin in (
        "https://attacker.example",
        "https://keystone.vercel.app.attacker.example",
        "http://localhost:3000",
        "null",
    ):
        response = preflight(origin)

        assert "access-control-allow-origin" not in response.headers


def test_same_origin_request_without_origin_still_returns_fixture():
    response = client().post("/tenants/falkordb/triage", json={"issue_number": 42})

    assert response.status_code == 200
    assert response.json()["run_key"] == "run:00000000-0000-4000-8000-000000000001"
