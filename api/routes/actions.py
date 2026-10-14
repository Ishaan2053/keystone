from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from api import schemas
from api.fixture_store import require_run
from api.routes import FixtureStoreDep, SettingsDep, checked_tenant, load_fixture


router = APIRouter(prefix="/tenants/{tenant}/runs/{run}", tags=["actions"])


@router.post(
    "/apply",
    response_model=schemas.ApplyResult,
    description="Fixture mode; no persistence/no writes. dry_run=false is refused in fixture mode.",
    responses={503: {"description": "Fixture unavailable"}},
)
def apply(
    tenant: str,
    run: UUID,
    request: schemas.ApplyRequest,
    settings: SettingsDep,
    store: FixtureStoreDep,
) -> schemas.ApplyResult:
    checked_tenant(tenant, settings)
    require_run(run)
    name = "apply_dry_run" if request.dry_run else "apply_refused"
    return load_fixture(store, name, schemas.ApplyResult)
