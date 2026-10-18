from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api import schemas
from api.actions import (
    ActionContextNotFound,
    ActionError,
    ActionServiceUnavailable,
    ActionStoreError,
)
from api.config import Settings
from api.fixture_store import require_run
from api.routes import (
    ApplyServiceDep,
    FixtureStoreDep,
    SettingsDep,
    checked_tenant,
    load_fixture,
)

router = APIRouter(prefix="/tenants/{tenant}/runs/{run}", tags=["actions"])


def _check_tenant(tenant: str, settings: Settings) -> None:
    if settings.api_mode == "live":
        if tenant not in settings.tenants:
            raise HTTPException(status_code=404, detail="Unknown tenant")
        return
    checked_tenant(tenant, settings)


def _sanitized(exc: Exception) -> str:
    return "Apply service unavailable"


@router.post(
    "/apply",
    response_model=schemas.ApplyResult,
    description="Fixture mode; no persistence/no writes. dry_run=false is refused in fixture mode.",
    responses={
        503: {"description": "Service unavailable"},
        403: {"description": "Public live writes refused"},
    },
)
def apply(
    tenant: str,
    run: UUID,
    request: schemas.ApplyRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
    service: ApplyServiceDep,
) -> schemas.ApplyResult:
    _check_tenant(tenant, settings)

    if settings.api_mode != "live":
        require_run(run)
        name = "apply_dry_run" if request.dry_run else "apply_refused"
        return load_fixture(store, name, schemas.ApplyResult)

    # Public HTTP is dry-run only. Refuse before touching the service.
    if request.dry_run is False:
        raise HTTPException(
            status_code=403,
            detail="Live GitHub writes are not available through the public API; dry_run only",
        )

    if service is None:
        raise HTTPException(status_code=503, detail="Apply service unavailable")

    run_key = f"run:{run}"
    try:
        return service.apply_triage(tenant, run_key, dry_run=bool(request.dry_run))
    except ActionContextNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ActionServiceUnavailable, ActionStoreError, ActionError) as exc:
        raise HTTPException(status_code=503, detail=_sanitized(exc)) from exc
