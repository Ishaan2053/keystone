from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from api import schemas
from api.config import Settings
from api.fixture_store import require_run
from api.routes import FixtureStoreDep, SettingsDep, checked_tenant, load_fixture, reject_unexpected_query

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants/{tenant}", tags=["agent"])

_LIVE = {
    404: {"description": "Unknown tenant, issue or run"},
    409: {"description": "The issue number matches issues in more than one repo"},
    503: {"description": "Fixture unavailable, or the live graph failed"},
}


# Who gave feedback through the API. The UI has no sign-in; B8's `/keystone reject` passes the commenter's login.
UI_GIVEN_BY = "ui"


def _live_tenant(tenant: str, settings: Settings) -> None:
    if tenant not in settings.tenants:
        raise HTTPException(status_code=404, detail="Unknown tenant")


def _live(call):
    """Run a live agent or graph call; every backend failure is a sanitized 503, never a fixture."""
    try:
        return call()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - sanitized error boundary
        log.exception("live agent route failed")
        raise HTTPException(status_code=503, detail="Live agent or graph call failed") from exc


@router.post(
    "/triage",
    response_model=schemas.TriageResult,
    summary="Triage an issue",
    description="Live mode runs the agent and records the run (no GitHub writes). Fixture mode returns "
    "a fixed preview with no persistence and no writes.",
    responses=_LIVE,
)
def triage(
    tenant: str,
    request: schemas.TriageRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
) -> schemas.TriageResult:
    if settings.api_mode == "live":
        _live_tenant(tenant, settings)
        from api import agent  # the agent loads the MCP server and embeddings; fixture mode never does

        def run() -> schemas.TriageResult:
            try:
                # A sync route runs in a worker thread, which has no event loop of its own.
                return asyncio.run(agent.run_triage(tenant, request))
            except agent.AmbiguousIssue as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except agent.IssueNotFound:
                raise HTTPException(status_code=404, detail="Unknown issue") from None

        return _live(run)
    checked_tenant(tenant, settings)
    return load_fixture(store, "triage_result", schemas.TriageResult)


@router.get(
    "/runs",
    response_model=schemas.RunsResponse,
    description="Recent runs, newest first: live from the graph, or fixtures.",
    responses=_LIVE,
)
def list_runs(
    tenant: str,
    settings: SettingsDep,
    store: FixtureStoreDep,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> schemas.RunsResponse:
    reject_unexpected_query(request, {"limit"})
    if settings.api_mode == "live":
        _live_tenant(tenant, settings)
        from api import memory

        return _live(lambda: schemas.RunsResponse(runs=memory.list_runs(tenant, limit)))
    checked_tenant(tenant, settings)
    runs = load_fixture(store, "runs", schemas.RunsResponse)
    ordered = sorted(runs.runs, key=lambda run: run.at, reverse=True)[:limit]
    return schemas.RunsResponse(runs=ordered)


@router.get(
    "/runs/{run}",
    response_model=schemas.ToolResult,
    description="recall_run: ToolResult(tool='recall_run', data=RecallRunData), live from the graph, or fixtures.",
    responses=_LIVE,
)
def get_run(
    tenant: str,
    run: UUID,
    settings: SettingsDep,
    store: FixtureStoreDep,
    request: Request,
) -> schemas.ToolResult:
    reject_unexpected_query(request, set())
    if settings.api_mode == "live":
        _live_tenant(tenant, settings)
        from api.tools import recall_run

        found = _live(lambda: recall_run(tenant, f"run:{run}"))
        if found.empty:
            raise HTTPException(status_code=404, detail="Unknown run")
        return found
    checked_tenant(tenant, settings)
    require_run(run)
    return load_fixture(store, "recall_run", schemas.ToolResult)


@router.post(
    "/runs/{run}/feedback",
    response_model=schemas.FeedbackResult,
    description="Live mode records the Feedback on the run's Action; a reject on an assign also creates the "
    "avoid Rule that rank_reviewers applies. Fixture mode returns fixed examples with no persistence and no writes.",
    responses={
        404: {"description": "Unknown tenant, run, or an action the run did not propose"},
        422: {"description": "Invalid request, or a module-scoped reject on an assign with no module"},
        503: {"description": "Fixture unavailable, or the live graph failed"},
    },
)
def feedback(
    tenant: str,
    run: UUID,
    request: schemas.FeedbackRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
) -> schemas.FeedbackResult:
    if settings.api_mode == "live":
        _live_tenant(tenant, settings)
        from api import memory

        def record() -> schemas.FeedbackResult:
            try:
                return memory.record_feedback(tenant, f"run:{run}", request, given_by=UI_GIVEN_BY)
            except memory.FeedbackNotFound:
                raise HTTPException(status_code=404, detail="Unknown run or action") from None
            except memory.FeedbackRefused as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            # Anything else (a bug, a malformed graph row, a graph failure) is a sanitized 503 from _live.

        return _live(record)
    checked_tenant(tenant, settings)
    require_run(run)
    name = "feedback_reject" if request.verdict == "reject" else "feedback_accept"
    return load_fixture(store, name, schemas.FeedbackResult)


@router.post(
    "/ask",
    response_model=schemas.AskResult,
    description="Live mode answers from find_decisions plus one compose call; every claim cites evidence the "
    "trace returned. Fixture mode returns a fixed example. Neither persists or writes.",
    responses={
        404: {"description": "Unknown tenant"},
        503: {"description": "Fixture unavailable, or the live graph failed"},
    },
)
def ask(
    tenant: str,
    request: schemas.AskRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
) -> schemas.AskResult:
    if settings.api_mode == "live":
        _live_tenant(tenant, settings)
        from api import agent

        return _live(lambda: agent.ask(tenant, request.question))
    checked_tenant(tenant, settings)
    return load_fixture(store, "ask", schemas.AskResult)
