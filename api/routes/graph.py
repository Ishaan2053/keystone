from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api import schemas
from api.routes import FixtureStoreDep, SettingsDep, checked_tenant, load_fixture, reject_unexpected_query


router = APIRouter(prefix="/tenants/{tenant}", tags=["graph"])


@router.get(
    "/path",
    response_model=schemas.ToolResult,
    description="Fixture mode; fixed path fixture regardless of valid query values until B4.",
    responses={503: {"description": "Fixture unavailable"}},
)
def path(
    tenant: str,
    settings: SettingsDep,
    store: FixtureStoreDep,
    request: Request,
    from_key: str = Query(alias="from", min_length=1),
    to_key: str = Query(alias="to", min_length=1),
) -> schemas.ToolResult:
    reject_unexpected_query(request, {"from", "to"})
    checked_tenant(tenant, settings)
    return load_fixture(store, "path", schemas.ToolResult)


@router.get(
    "/subgraph",
    response_model=schemas.Subgraph,
    description="Fixture mode; fixed subgraph fixture regardless of valid query values until B4.",
    responses={503: {"description": "Fixture unavailable"}},
)
def subgraph(
    tenant: str,
    settings: SettingsDep,
    store: FixtureStoreDep,
    request: Request,
    keys: str = Query(min_length=1),
) -> schemas.Subgraph:
    reject_unexpected_query(request, {"keys"})
    checked_tenant(tenant, settings)
    return load_fixture(store, "subgraph", schemas.Subgraph)


@router.get(
    "/stats",
    response_model=schemas.Stats,
    description="Fixture mode; no persistence/no writes.",
    responses={503: {"description": "Fixture unavailable"}},
)
def stats(tenant: str, settings: SettingsDep, store: FixtureStoreDep, request: Request) -> schemas.Stats:
    reject_unexpected_query(request, set())
    checked_tenant(tenant, settings)
    return load_fixture(store, "stats", schemas.Stats)
