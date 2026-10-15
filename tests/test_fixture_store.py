import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from api import schemas
from api.config import Settings, load_settings
from api.fixture_store import FixtureLoadError, FixtureStore, require_run, require_tenant


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "api" / "fixtures"


FIXTURE_MODELS: dict[str, type[BaseModel]] = {
    "tool_result": schemas.ToolResult,
    "claim": schemas.Claim,
    "triage_result": schemas.TriageResult,
    "run_summary": schemas.RunSummary,
    "subgraph": schemas.Subgraph,
    "stats": schemas.Stats,
    "proof": schemas.Proof,
    "tenants": schemas.TenantsResponse,
    "runs": schemas.RunsResponse,
    "recall_run": schemas.ToolResult,
    "path": schemas.ToolResult,
    "apply_dry_run": schemas.ApplyResult,
    "apply_refused": schemas.ApplyResult,
    "feedback_accept": schemas.FeedbackResult,
    "feedback_reject": schemas.FeedbackResult,
    "ask": schemas.AskResult,
}


def test_t0_fixtures_validate():
    for name, model in FIXTURE_MODELS.items():
        source = (FIXTURE_ROOT / f"{name}.json").read_text()
        parsed = model.model_validate_json(source)

        assert parsed.model_dump(mode="json") == json.loads(source)


def test_claim_citations_are_trace_evidence():
    triage = schemas.TriageResult.model_validate_json(
        (FIXTURE_ROOT / "triage_result.json").read_text()
    )
    trace_evidence = {evidence for tool in triage.trace for evidence in tool.evidence}
    cited = {
        citation
        for group in (triage.labels, triage.reviewers, triage.risks, triage.decisions)
        for claim in group
        for citation in claim.citations
    }

    assert cited <= trace_evidence


def test_missing_or_invalid_fixture_is_clear_error(tmp_path):
    store = FixtureStore(tmp_path)

    with pytest.raises(FixtureLoadError) as missing:
        store.load("triage_result", schemas.TriageResult)
    assert "triage_result" in str(missing.value)
    assert "run_key" not in str(missing.value)

    (tmp_path / "triage_result.json").write_text('{"run_key":"leaked-source"')
    with pytest.raises(FixtureLoadError) as invalid:
        store.load("triage_result", schemas.TriageResult)
    assert "triage_result" in str(invalid.value)
    assert "leaked-source" not in str(invalid.value)


def test_tenant_allowlist():
    settings = Settings(tenants=("falkordb", "ghost"), cors_origins=("http://localhost:3001",))

    require_tenant("falkordb", settings)

    with pytest.raises(HTTPException) as unknown:
        require_tenant("unknown", settings)
    assert unknown.value.status_code == 404

    with pytest.raises(HTTPException) as unavailable:
        require_tenant("ghost", settings)
    assert unavailable.value.status_code == 503


def test_load_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    triage = FixtureStore.default().load("triage_result", schemas.TriageResult)

    assert triage.run_key == "run:00000000-0000-4000-8000-000000000001"


def test_load_settings_trims_deduplicates_and_rejects_invalid(monkeypatch):
    monkeypatch.setenv("TENANTS", " falkordb, falkordb , ghost ")
    monkeypatch.setenv("CORS_ORIGINS", " http://localhost:3001,https://keystone.vercel.app/,https://keystone.vercel.app ")
    monkeypatch.setenv("API_MODE", "fixtures")

    settings = load_settings()

    assert settings.tenants == ("falkordb", "ghost")
    assert settings.cors_origins == ("http://localhost:3001", "https://keystone.vercel.app")
    assert settings.api_mode == "fixtures"

    monkeypatch.setenv("API_MODE", "live")
    assert load_settings().api_mode == "live"

    monkeypatch.setenv("API_MODE", "staging")
    with pytest.raises(ValueError, match="API_MODE"):
        load_settings()

    monkeypatch.setenv("API_MODE", "fixtures")
    for origin in ("*", "https://user@example.com", "https://example.com/path"):
        monkeypatch.setenv("CORS_ORIGINS", origin)
        with pytest.raises(ValueError, match="CORS_ORIGINS"):
            load_settings()


def test_fixture_names_are_allowlisted(tmp_path):
    store = FixtureStore(tmp_path)
    (tmp_path / "../secret.json").write_text("{}")

    with pytest.raises(FixtureLoadError):
        store.load("../secret", schemas.TriageResult)


def test_require_run_accepts_only_fixture_uuid():
    assert (
        require_run("00000000-0000-4000-8000-000000000001")
        == "run:00000000-0000-4000-8000-000000000001"
    )

    with pytest.raises(HTTPException) as unknown:
        require_run("00000000-0000-4000-8000-000000000099")
    assert unknown.value.status_code == 404
