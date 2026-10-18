from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from api import schemas
from api.config import Settings
from api.graph_reads import get_graph_reads, parse_seed_keys
from api.routes import (
    FixtureStoreDep,
    SettingsDep,
    checked_tenant,
    load_fixture,
    reject_unexpected_query,
)

router = APIRouter(prefix="/tenants/{tenant}", tags=["graph"])


def _check_tenant(tenant: str, settings: Settings) -> None:
    """Reject unknown tenants.

    Fixture mode keeps the fixture-dataset restriction; live mode serves any
    configured tenant from its ``tenant_<slug>`` graph.
    """

    if settings.api_mode == "live":
        if tenant not in settings.tenants:
            raise HTTPException(status_code=404, detail="Unknown tenant")
        return
    checked_tenant(tenant, settings)


def _live(request: Request, call):
    """Run a live read, sanitizing every backend failure as a 503.

    Never falls back to fixtures: a live dependency failure is an error, not a
    successful synthetic result.
    """

    reader = get_graph_reads(request)
    try:
        return call(reader)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - sanitized error boundary
        raise HTTPException(status_code=503, detail="Live graph query failed") from exc


@router.get(
    "/path",
    response_model=schemas.ToolResult,
    description="Fixture mode returns a fixed path; live mode queries A's explain_path.",
    responses={503: {"description": "Fixture unavailable or live graph reader failed"}},
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
    _check_tenant(tenant, settings)
    if settings.api_mode == "live":
        return _live(request, lambda reader: reader.path(tenant, from_key, to_key))
    return load_fixture(store, "path", schemas.ToolResult)


@router.get(
    "/subgraph",
    response_model=schemas.Subgraph,
    description="Fixture mode returns a fixed subgraph; live mode queries A's subgraph.",
    responses={503: {"description": "Fixture unavailable or live graph reader failed"}},
)
def subgraph(
    tenant: str,
    settings: SettingsDep,
    store: FixtureStoreDep,
    request: Request,
    keys: str = Query(min_length=1),
) -> schemas.Subgraph:
    reject_unexpected_query(request, {"keys"})
    _check_tenant(tenant, settings)
    if settings.api_mode == "live":
        seed_keys = parse_seed_keys(keys)
        return _live(request, lambda reader: reader.subgraph(tenant, seed_keys))
    return load_fixture(store, "subgraph", schemas.Subgraph)


@router.get(
    "/stats",
    response_model=schemas.Stats,
    description="Fixture mode returns a fixed stats fixture; live mode queries A's counts.",
    responses={503: {"description": "Fixture unavailable or live graph reader failed"}},
)
def stats(
    tenant: str, settings: SettingsDep, store: FixtureStoreDep, request: Request
) -> schemas.Stats:
    reject_unexpected_query(request, set())
    _check_tenant(tenant, settings)
    if settings.api_mode == "live":
        return _live(request, lambda reader: reader.stats(tenant))
    return load_fixture(store, "stats", schemas.Stats)
