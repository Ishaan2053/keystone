from __future__ import annotations

from pathlib import Path
from typing import TypeVar
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from api.config import Settings


ModelT = TypeVar("ModelT", bound=BaseModel)

ALLOWED_FIXTURES = frozenset(
    {
        "tool_result",
        "claim",
        "triage_result",
        "run_summary",
        "subgraph",
        "stats",
        "proof",
        "tenants",
        "runs",
        "recall_run",
        "path",
        "apply_dry_run",
        "apply_refused",
        "feedback_accept",
        "feedback_reject",
        "ask",
    }
)
KNOWN_DATASET_TENANTS = frozenset({"falkordb"})
FIXTURE_RUN_UUID = UUID("00000000-0000-4000-8000-000000000001")


class FixtureLoadError(RuntimeError):
    pass


class FixtureStore:
    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def default(cls) -> "FixtureStore":
        return cls(Path(__file__).resolve().parent / "fixtures")

    def load(self, name: str, model: type[ModelT]) -> ModelT:
        if name not in ALLOWED_FIXTURES:
            raise FixtureLoadError(f"fixture '{name}' is not allowlisted")
        path = self.root / f"{name}.json"
        try:
            source = path.read_text()
        except OSError as exc:
            raise FixtureLoadError(f"fixture '{name}' is unavailable ({type(exc).__name__})") from exc
        try:
            return model.model_validate_json(source)
        except ValidationError as exc:
            raise FixtureLoadError(f"fixture '{name}' is invalid ({type(exc).__name__})") from exc
        except ValueError as exc:
            raise FixtureLoadError(f"fixture '{name}' is invalid ({type(exc).__name__})") from exc


def require_tenant(tenant: str, settings: Settings) -> None:
    if tenant not in settings.tenants:
        raise HTTPException(status_code=404, detail="Unknown tenant")
    if tenant not in KNOWN_DATASET_TENANTS:
        raise HTTPException(status_code=503, detail="Fixture dataset unavailable for tenant")


def require_run(run: UUID | str) -> str:
    parsed = run if isinstance(run, UUID) else UUID(str(run))
    if parsed != FIXTURE_RUN_UUID:
        raise HTTPException(status_code=404, detail="Unknown run")
    return f"run:{parsed}"
