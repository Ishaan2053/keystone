from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from api import schemas
from api.config import Settings
from api.fixture_store import require_run
from api.routes import FixtureStoreDep, SettingsDep, checked_tenant, load_fixture, reject_unexpected_query


router = APIRouter(prefix="/tenants/{tenant}", tags=["agent"])

PENDING_A10 = "Pending A10: live agent routes are not implemented yet"
_PENDING = {503: {"description": "Fixture unavailable, or live mode (pending A10)"}}


def _fixtures_only(settings: Settings) -> None:
    """Live mode has no agent behind these routes until A10; never serve fixtures as live runs."""
    if settings.api_mode == "live":
        raise HTTPException(status_code=503, detail=PENDING_A10)


@router.post(
    "/triage",
    response_model=schemas.TriageResult,
    summary="Fixture triage preview",
    description="Fixture mode; no persistence/no writes.",
    responses=_PENDING,
)
def triage(
    tenant: str,
    request: schemas.TriageRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
) -> schemas.TriageResult:
    _fixtures_only(settings)
    checked_tenant(tenant, settings)
    return load_fixture(store, "triage_result", schemas.TriageResult)


@router.get(
    "/runs",
    response_model=schemas.RunsResponse,
    description="Fixture mode; no persistence/no writes.",
    responses=_PENDING,
)
def list_runs(
    tenant: str,
    settings: SettingsDep,
    store: FixtureStoreDep,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> schemas.RunsResponse:
    reject_unexpected_query(request, {"limit"})
    _fixtures_only(settings)
    checked_tenant(tenant, settings)
    runs = load_fixture(store, "runs", schemas.RunsResponse)
    ordered = sorted(runs.runs, key=lambda run: run.at, reverse=True)[:limit]
    return schemas.RunsResponse(runs=ordered)


@router.get(
    "/runs/{run}",
    response_model=schemas.ToolResult,
    description="Fixture mode; no persistence/no writes. Returns ToolResult(data.triage).",
    responses=_PENDING,
)
def get_run(
    tenant: str,
    run: UUID,
    settings: SettingsDep,
    store: FixtureStoreDep,
    request: Request,
) -> schemas.ToolResult:
    reject_unexpected_query(request, set())
    _fixtures_only(settings)
    checked_tenant(tenant, settings)
    require_run(run)
    return load_fixture(store, "recall_run", schemas.ToolResult)


@router.post(
    "/runs/{run}/feedback",
    response_model=schemas.FeedbackResult,
    description="Fixture mode; no persistence/no writes. Acceptance and rejection return fixed examples.",
    responses=_PENDING,
)
def feedback(
    tenant: str,
    run: UUID,
    request: schemas.FeedbackRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
) -> schemas.FeedbackResult:
    _fixtures_only(settings)
    checked_tenant(tenant, settings)
    require_run(run)
    name = "feedback_reject" if request.verdict == "reject" else "feedback_accept"
    return load_fixture(store, name, schemas.FeedbackResult)


@router.post(
    "/ask",
    response_model=schemas.AskResult,
    description="Fixture mode; no persistence/no writes.",
    responses=_PENDING,
)
def ask(
    tenant: str,
    request: schemas.AskRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
) -> schemas.AskResult:
    _fixtures_only(settings)
    checked_tenant(tenant, settings)
    return load_fixture(store, "ask", schemas.AskResult)
