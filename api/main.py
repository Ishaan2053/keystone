from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import schemas
from api.config import Settings, load_settings
from api.fixture_store import FixtureStore
from api.graph_reads import GraphReads, build_live_graph_reads
from api.routes import actions, agent, graph, reserved, tenants
from api.actions import ActionError, ActionService, ActionStore
from api.github import SandboxGitHub

log = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Read the repo ``.env`` like the CLIs do (the shell wins), so a local
    ``API_MODE=live uvicorn api.main:app`` can authenticate to FalkorDB.

    The API image ships without ``ingest/``; Compose passes its environment.
    """
    try:
        from ingest import fetch
    except ImportError:
        return
    fetch.load_env()


def _resolve_graph_reads(settings: Settings, injected: GraphReads | None) -> GraphReads | None:
    """Select the live reader, or None in fixture mode.

    Fixture mode never constructs or connects a reader. Live mode uses an
    injected reader when provided, otherwise the lazy live reader; if building
    it fails, the reader stays unset so routes return a sanitized 503 instead of
    fixtures, and startup goes on.

    This runs from lifespan *startup* (never from ``create_app``) so a live
    deployment connects through the app lifecycle rather than at import time.
    """

    if settings.api_mode != "live":
        return None
    if injected is not None:
        return injected
    try:
        return build_live_graph_reads(settings)
    except Exception:  # noqa: BLE001 - logged here; routes answer a sanitized 503
        log.exception("live graph reader unavailable; graph routes will return 503")
        return None


def _resolve_apply_service(
    settings: Settings, service: ActionService | None, store: ActionStore | None
) -> ActionService | None:
    """The live apply service, or None so the apply route answers 503.

    Fixture mode never has one. In live mode an injected service wins;
    otherwise one is built around ``store`` (A10's run-context lookup and
    ``record_actions.cypher`` executor) and a writer bound to ``SANDBOX_REPO``
    when ``GITHUB_WRITE_TOKEN`` is set. Without a store or a sandbox repo there
    is no service.
    """

    if settings.api_mode != "live":
        return None
    if service is not None:
        return service
    if store is None or not settings.sandbox_repo:
        return None
    token = os.getenv("GITHUB_WRITE_TOKEN", "").strip()
    writer = None
    if token:
        client = httpx.Client(headers={"Authorization": f"token {token}"}, timeout=30.0)
        writer = SandboxGitHub(settings.sandbox_repo, client)
    try:
        return ActionService(
            store=store,
            sandbox_repo=settings.sandbox_repo,
            writer=writer,
            api_mode="live",
            public_base_url=settings.public_base_url,
        )
    except ActionError:
        log.exception("apply service misconfigured; apply will return 503")
        return None


def create_app(
    settings: Settings | None = None,
    fixture_store: FixtureStore | None = None,
    graph_reads: GraphReads | None = None,
    apply_service: ActionService | None = None,
    apply_store: ActionStore | None = None,
) -> FastAPI:
    _load_dotenv()
    resolved_settings = settings or load_settings()
    injected_reader = graph_reads if resolved_settings.api_mode == "live" else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if injected_reader is None and app.state.graph_reads is None:
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
    app.state.apply_service = _resolve_apply_service(resolved_settings, apply_service, apply_store)
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
