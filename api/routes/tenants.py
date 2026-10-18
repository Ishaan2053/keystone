from __future__ import annotations

from fastapi import APIRouter

from api import schemas
from api.routes import SettingsDep

router = APIRouter(tags=["tenants"])


def _tenant_name(slug: str) -> str:
    if slug == "falkordb":
        return "FalkorDB"
    return slug.replace("-", " ").replace("_", " ").title()


@router.get(
    "/tenants", response_model=schemas.TenantsResponse, description="Configured fixture tenants."
)
def tenants(settings: SettingsDep) -> schemas.TenantsResponse:
    return schemas.TenantsResponse(
        tenants=[
            schemas.TenantSummary(slug=slug, name=_tenant_name(slug)) for slug in settings.tenants
        ]
    )
