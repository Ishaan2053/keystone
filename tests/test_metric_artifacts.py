"""Metric artifacts are optional, tenant-isolated, and never rewritten."""

import json
from pathlib import Path

import pytest

from api.metric_artifacts import read_metric_artifacts


@pytest.fixture
def proof_payload():
    return {
        "n": 2,
        "methods": {
            "graph": {"reviewer_hit_at_3": 0, "module_hit_at_1": 0.1, "module_hit_at_3": 0.2, "label_precision_at_2": 0.3},
            "codeowners": {"reviewer_hit_at_3": 0.9, "module_hit_at_1": 0.8, "module_hit_at_3": 0.7, "label_precision_at_2": 0.6},
            "similar_issues": {"reviewer_hit_at_3": 0.5, "module_hit_at_1": 0.4, "module_hit_at_3": 0.3, "label_precision_at_2": 0.2},
        },
        "rows": [{"issue": 1, "note": "graph loses"}],
    }


def write_artifact(root, kind, payload, tenant="falkordb"):
    path = root / kind / f"{tenant}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def test_exact_proof_round_trip_and_benchmark_metadata(tmp_path, proof_payload):
    write_artifact(tmp_path, "proof", proof_payload)
    latencies = [{"tool": "affected_modules", "p50_ms": 0, "p95_ms": 12.5}]
    write_artifact(tmp_path, "bench", {"latencies": latencies, "node_counts": {"Issue": 999}, "warm": 50})

    proof, actual_latencies, unavailable = read_metric_artifacts("falkordb", tmp_path)

    assert proof.model_dump() == proof_payload
    assert [row.model_dump() for row in actual_latencies] == latencies
    assert unavailable == []


def test_zero_sample_and_zero_rates_are_valid(tmp_path, proof_payload):
    proof_payload["n"] = 0
    proof_payload["rows"] = []
    for rates in proof_payload["methods"].values():
        for metric in rates:
            rates[metric] = 0
    write_artifact(tmp_path, "proof", proof_payload)
    proof, latencies, unavailable = read_metric_artifacts("falkordb", tmp_path)
    assert proof.model_dump() == proof_payload
    assert latencies == []
    assert unavailable == []


def test_no_files_and_tenant_isolation(tmp_path, proof_payload):
    assert read_metric_artifacts("falkordb", tmp_path) == (None, [], [])
    write_artifact(tmp_path, "proof", proof_payload, tenant="other")
    write_artifact(tmp_path, "bench", {"latencies": [{"tool": "x", "p50_ms": 1, "p95_ms": 2}]}, tenant="other")
    assert read_metric_artifacts("falkordb", tmp_path) == (None, [], [])
    proof, latencies, _ = read_metric_artifacts("other", tmp_path)
    assert proof.model_dump() == proof_payload
    assert latencies[0].tool == "x"


@pytest.mark.parametrize("kind", ["proof", "bench"])
@pytest.mark.parametrize("failure", ["missing", "json", "schema", "unreadable", "encoding"])
def test_artifact_failures_are_independent(tmp_path, proof_payload, monkeypatch, caplog, kind, failure):
    write_artifact(tmp_path, "proof", proof_payload)
    write_artifact(tmp_path, "bench", {"latencies": [{"tool": "x", "p50_ms": 1, "p95_ms": 2}]})
    path = tmp_path / kind / "falkordb.json"
    if failure == "missing":
        path.unlink()
    elif failure == "json":
        path.write_text("secret artifact contents: invalid JSON")
    elif failure == "schema":
        path.write_text('{"secret": "artifact contents"}')
    elif failure == "encoding":
        path.write_bytes(b"\xff")
    else:
        original = Path.read_text

        def read_text(self, *args, **kwargs):
            if self == path:
                raise PermissionError("secret artifact contents")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)

    proof, latencies, unavailable = read_metric_artifacts("falkordb", tmp_path)

    if kind == "proof":
        assert proof is None
        assert [row.model_dump() for row in latencies] == [{"tool": "x", "p50_ms": 1, "p95_ms": 2}]
    else:
        assert proof.model_dump() == proof_payload
        assert latencies == []
    # A missing file was never measured; a present but unusable one is unavailable.
    assert unavailable == ([] if failure == "missing" else [{"proof": "proof", "bench": "latencies"}[kind]])
    assert "secret" not in caplog.text
    assert "artifact contents" not in caplog.text
    assert caplog.records


@pytest.mark.parametrize("field", ["p50_ms", "p95_ms"])
@pytest.mark.parametrize("number", ["1e999", "Infinity"])
def test_nonfinite_benchmark_is_unavailable_without_hiding_proof(tmp_path, proof_payload, field, number):
    write_artifact(tmp_path, "proof", proof_payload)
    path = write_artifact(tmp_path, "bench", {})
    row = '{"tool":"x","p50_ms":1,"p95_ms":2}'
    row = row.replace(f'"{field}":' + ("1" if field == "p50_ms" else "2"), f'"{field}":{number}')
    path.write_text('{"latencies":[' + row + ']}')

    proof, latencies, unavailable = read_metric_artifacts("falkordb", tmp_path)

    assert proof.model_dump() == proof_payload
    assert latencies == []
    assert unavailable == ["latencies"]


def test_corrupt_nonfinite_and_missing_artifacts_are_distinct(tmp_path, proof_payload):
    write_artifact(tmp_path, "proof", proof_payload)
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "falkordb.json").write_text('{"latencies":[{"tool":"x","p50_ms":Infinity,"p95_ms":2}]}')
    assert read_metric_artifacts("falkordb", tmp_path)[1:] == ([], ["latencies"])

    (tmp_path / "proof" / "falkordb.json").write_text("{corrupt")
    assert read_metric_artifacts("falkordb", tmp_path) == (None, [], ["proof", "latencies"])

    (tmp_path / "proof" / "falkordb.json").unlink()
    (tmp_path / "bench" / "falkordb.json").unlink()
    assert read_metric_artifacts("falkordb", tmp_path) == (None, [], [])


@pytest.mark.parametrize("tenant", ["../escape", "a/b", "a\\b", "..", "", "/absolute", "a\n"])
def test_unsafe_tenant_rejected_before_filesystem_access(tmp_path, monkeypatch, tenant):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid tenant touched the filesystem")

    monkeypatch.setattr(Path, "read_text", forbidden)
    with pytest.raises(ValueError):
        read_metric_artifacts(tenant, tmp_path)
