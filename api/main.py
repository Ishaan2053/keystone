from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import schemas
from api.config import Settings, load_settings
from api.fixture_store import FixtureStore
from api.routes import actions, agent, graph, reserved, tenants


def create_app(
    settings: Settings | None = None,
    fixture_store: FixtureStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Keystone API")
    app.state.settings = settings or load_settings()
    app.state.fixture_store = fixture_store or FixtureStore.default()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app.state.settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.get("/health", response_model=schemas.HealthResponse)
    def health() -> schemas.HealthResponse:
        return schemas.HealthResponse(status="ok")

    app.include_router(tenants.router)
    app.include_router(agent.router)
    app.include_router(actions.router)
    app.include_router(graph.router)
    app.include_router(reserved.router)
    return app


app = create_app()
