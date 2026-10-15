from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import schemas
from api.config import Settings, load_settings
from api.fixture_store import FixtureStore
from api.graph_reads import GraphReads, GraphReadsUnavailable, build_live_graph_reads
from api.routes import actions, agent, graph, reserved, tenants
from api.actions import ActionError, ActionService, ActionStore


def _build_live_action_service(settings: Settings, store: ActionStore | None) -> ActionService | None:
    if store is None or settings.api_mode != "live":
        return None
    if not settings.sandbox_repo:
        return None
    try:
        import os as _os

        import httpx

        from api.github import SandboxGitHub

        token = _os.getenv("GITHUB_WRITE_TOKEN", "").strip()
        writer = None
        if token:
            writer = SandboxGitHub(settings.sandbox_repo, httpx.Client(headers={"Authorization": f"token {token}"}, timeout=30.0))
        return ActionService(
            store=store,
            sandbox_repo=settings.sandbox_repo,
            writer=writer,
            api_mode="live",
            public_base_url=settings.public_base_url,
        )
    except ActionError:
        return None


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


def _resolve_apply_service(
    settings: Settings, injected_service: ActionService | None, injected_store: ActionStore | None
) -> ActionService | None:
    if settings.api_mode != "live":
        return None
    if injected_service is not None:
        return injected_service
    if injected_store is not None:
        return _build_live_action_service(settings, injected_store)
    # A's concrete store factory is not available in this worktree; fail closed.
    try:
        from api.actions import build_live_action_service
    except ImportError:
        return None
    try:
        return build_live_action_service(settings)
    except Exception:  # noqa: BLE001 - sanitized boundary
        return None


def create_app(
    settings: Settings | None = None,
    fixture_store: FixtureStore | None = None,
    graph_reads: GraphReads | None = None,
    apply_service: ActionService | None = None,
    apply_store: ActionStore | None = None,
    action_service: ActionService | None = None,
    service: ActionService | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    injected_reader = graph_reads if resolved_settings.api_mode == "live" else None
    injected_service = (
        apply_service if apply_service is not None
        else action_service if action_service is not None
        else service
    )
    if resolved_settings.api_mode != "live":
        injected_service = None
        injected_store = None
    else:
        injected_store = apply_store

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if injected_reader is None and app.state.graph_reads is None:
            app.state.graph_reads = _resolve_graph_reads(resolved_settings, None)
        if getattr(app.state, "apply_service", None) is None:
            app.state.apply_service = _resolve_apply_service(resolved_settings, injected_service, injected_store)
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
    app.state.apply_service = injected_service if resolved_settings.api_mode == "live" else None
    app.state.apply_store = injected_store if resolved_settings.api_mode == "live" else None
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
