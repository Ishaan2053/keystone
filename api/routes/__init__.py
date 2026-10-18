from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from api.actions import ActionService
from api.config import Settings
from api.fixture_store import FixtureLoadError, FixtureStore, require_tenant


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_fixture_store(request: Request) -> FixtureStore:
    return request.app.state.fixture_store


def get_apply_service(request: Request) -> ActionService | None:
    return getattr(request.app.state, "apply_service", None)


SettingsDep = Annotated[Settings, Depends(get_settings)]
FixtureStoreDep = Annotated[FixtureStore, Depends(get_fixture_store)]
ApplyServiceDep = Annotated[ActionService | None, Depends(get_apply_service)]


def checked_tenant(tenant: str, settings: Settings) -> None:
    require_tenant(tenant, settings)


def load_fixture(store: FixtureStore, name: str, model: type[BaseModel]):
    try:
        return store.load(name, model)
    except FixtureLoadError as exc:
        raise HTTPException(
            status_code=503, detail=f"Fixture '{name}' is unavailable or invalid"
        ) from exc


def reject_unexpected_query(request: Request, allowed: set[str]) -> None:
    unexpected = set(request.query_params.keys()) - allowed
    if unexpected:
        raise HTTPException(status_code=422, detail="Unexpected query parameters")
