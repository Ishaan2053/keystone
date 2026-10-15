from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import schemas
from api.config import Settings, load_settings
from api.fixture_store import FixtureStore
from api.graph_reads import GraphReads, GraphReadsUnavailable, build_live_graph_reads
from api.routes import actions, agent, graph, reserved, tenants


def _resolve_graph_reads(settings: Settings, injected: GraphReads | None) -> GraphReads | None:
    """Select the live reader, or None in fixture mode.

    Fixture mode never constructs or connects a reader. Live mode uses an
    injected reader when provided, otherwise attempts A's concrete reader; the
    fail-closed factory raises ``GraphReadsUnavailable`` until that lands, and we
    leave the reader unset so routes return a sanitized 503 instead of fixtures.

    This runs from lifespan *startup* (never from ``create_app``) so a live
    deployment connects through the app lifecycle rather than at import time.
    """

    if settings.api_mode != "live":
        return None
    if injected is not None:
        return injected
    try:
        return build_live_graph_reads(settings)
    except GraphReadsUnavailable:
        return None


def create_app(
    settings: Settings | None = None,
    fixture_store: FixtureStore | None = None,
    graph_reads: GraphReads | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    # An injected reader is already constructed by the caller, so it is used
    # as-is; fixture mode never holds a reader. A factory-built live reader is
    # constructed in lifespan startup below, so live mode does not connect at
    # import/create_app time.
    injected_reader = graph_reads if resolved_settings.api_mode == "live" else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if injected_reader is None:
            app.state.graph_reads = _resolve_graph_reads(resolved_settings, None)
        try:
            yield
        finally:
            close = getattr(getattr(app.state, "graph_reads", None), "close", None)
            if callable(close):
                close()

    app = FastAPI(title="Keystone API", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.fixture_store = fixture_store or FixtureStore.default()
    app.state.graph_reads = injected_reader
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
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
