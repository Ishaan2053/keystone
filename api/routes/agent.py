from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from api import schemas
from api.config import Settings
from api.fixture_store import require_run
from api.notifications import DEPENDENCY_WARNING
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


def _begin_ui_followup(notifications, tenant: str, request: schemas.TriageRequest):
    """Prove the feedback lineage of one explicit UI Re-triage, or ``None``.

    Only the canonical full-issue mode with both lineage fields is considered, and the
    prefix-free wire UUID is expanded to the canonical ``run:<uuid>``. An unprovable
    reference or failing adapter yields no attempt, so triage stays ordinary and silent.
    """
    if notifications is None:
        return None
    if not request.issue_key or request.feedback_parent_run is None or not request.feedback_key:
        return None
    try:
        return notifications.begin_ui_feedback(
            tenant=tenant,
            issue_key=request.issue_key,
            parent_run_key=f"run:{request.feedback_parent_run}",
            feedback_key=request.feedback_key,
        )
    except Exception:  # noqa: BLE001 - a notification failure never changes triage
        log.warning(DEPENDENCY_WARNING)
        return None


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
    http_request: Request,
) -> schemas.TriageResult:
    if settings.api_mode == "live":
        _live_tenant(tenant, settings)
        from api import agent  # the agent loads the MCP server and embeddings; fixture mode never does

        # The Slack coordinator is optional and read off app state defensively: without one
        # (or without a provable reference) this is ordinary silent manual triage.
        notifications = getattr(http_request.app.state, "notifications", None)
        attempt = _begin_ui_followup(notifications, tenant, request)

        def run() -> schemas.TriageResult:
            def triage_failed() -> None:
                # A failed attempt is reported only when it was already proven eligible.
                if attempt is not None and notifications is not None:
                    try:
                        notifications.failed(attempt, stage="triage")
                    except Exception:  # noqa: BLE001 - preserve the original triage failure
                        log.warning(DEPENDENCY_WARNING)

            try:
                # A sync route runs in a worker thread, which has no event loop of its own.
                result = asyncio.run(agent.run_triage(tenant, request))
            # Client errors (409, 404) are not processing failures: no alert, no "Recovered" later.
            except agent.AmbiguousIssue as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except agent.IssueNotFound:
                raise HTTPException(status_code=404, detail="Unknown issue") from None
            except Exception:
                triage_failed()
                raise
            if attempt is not None and notifications is not None:
                # Record lineage for the persisted run; a UI follow-up applies nothing.
                try:
                    notifications.complete(attempt, run_key=result.run_key, applied=None)
                except Exception:  # noqa: BLE001 - preserve the persisted triage result
                    log.warning(DEPENDENCY_WARNING)
            return result

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
