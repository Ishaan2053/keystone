"""Live graph routes: DB-free artifact regressions and optional FalkorDB checks."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ingest.fetch import load_env

load_env()  # FALKORDB_* only under pytest (tests/conftest.py)

from api import schemas  # noqa: E402
from api.config import Settings  # noqa: E402
from api.db import graph  # noqa: E402
from api.fixture_store import FixtureStore  # noqa: E402
from api.main import create_app  # noqa: E402

TENANT = "falkordb"
SEED = "issue:FalkorDB/FalkorDB#1333"

@pytest.fixture(scope="module")
def client():
    try:
        graph(TENANT).ro_query("RETURN 1")
    except Exception as exc:
        pytest.skip(f"FalkorDB not reachable: {exc}")

    class ExplodingStore:
        def load(self, *args, **kwargs):
            raise AssertionError("live mode must not read fixtures")

    settings = Settings(tenants=(TENANT,), cors_origins=("http://localhost:3001",), api_mode="live")
    with TestClient(create_app(settings=settings, fixture_store=ExplodingStore())) as client:
        yield client


def test_live_stats(client):
    response = client.get(f"/tenants/{TENANT}/stats")

    assert response.status_code == 200
    stats = schemas.Stats.model_validate(response.json())
    assert stats.node_counts["Issue"] > 0 and stats.node_counts["Person"] > 0
    assert "Node" not in stats.node_counts
    assert stats.edge_counts["AUTHORED"] > 0


def test_live_subgraph_and_path(client):
    response = client.get(f"/tenants/{TENANT}/subgraph", params={"keys": json.dumps([SEED])})

    assert response.status_code == 200
    subgraph = schemas.Subgraph.model_validate(response.json())
    assert subgraph.nodes[0].key == SEED
    assert 1 < len(subgraph.nodes) <= 150 and subgraph.edges
    assert "embedding" not in subgraph.nodes[0].properties
    # explain_path walks evidence relationships only (not HAS_LABEL), so aim at the author.
    neighbour = next(e.source for e in subgraph.edges if e.type == "AUTHORED" and e.target == SEED)

    response = client.get(f"/tenants/{TENANT}/path", params={"from": SEED, "to": neighbour})

    assert response.status_code == 200
    path = schemas.ToolResult.model_validate(response.json())
    data = schemas.PathData.model_validate(path.data)
    assert path.tool == "explain_path" and not path.empty
    assert data.keys[0] == SEED and data.keys[-1] == neighbour and len(data.rels) == 1
    assert path.cypher and "MATCH" in path.cypher[0]
    assert path.params["from"] == SEED and path.params["to"] == neighbour


def test_live_unknown_keys_are_empty_not_errors(client):
    subgraph = client.get(f"/tenants/{TENANT}/subgraph", params={"keys": "issue:none/none#0"})
    path = client.get(f"/tenants/{TENANT}/path", params={"from": "issue:none/none#0", "to": SEED})

    assert subgraph.status_code == 200 and subgraph.json() == {"nodes": [], "edges": []}
    assert path.status_code == 200 and path.json()["empty"] is True


@pytest.fixture
def artifact_client(tmp_path, monkeypatch):
    from api import graph_reads

    class FakeGraph:
        count = 3

        def ro_query(self, query):
            assert query == graph_reads.cypher.query("stats")
            return SimpleNamespace(result_set=[("node", "Issue", self.count), ("edge", "AUTHORED", 2)])

    fake = FakeGraph()
    monkeypatch.setattr(graph_reads, "graph", lambda tenant: fake)
    reader = graph_reads.LiveGraphReads(data_dir=tmp_path)
    settings = Settings(tenants=(TENANT,), api_mode="live")
    with TestClient(create_app(settings=settings, graph_reads=reader)) as test_client:
        yield test_client, tmp_path, fake


def test_stats_reads_new_artifacts_on_next_request(artifact_client):
    client, root, fake = artifact_client
    url = f"/tenants/{TENANT}/stats"
    first = client.get(url)
    assert first.status_code == 200
    assert first.json()["proof"] is None
    assert first.json()["latencies"] == []
    assert first.json()["unavailable"] == []
    rates = {"reviewer_hit_at_3": 0, "module_hit_at_1": 0.1, "module_hit_at_3": 0.2, "label_precision_at_2": 0.3}
    proof = {"n": 1, "methods": {method: rates for method in ("graph", "codeowners", "similar_issues")}, "rows": []}
    for kind, payload in [("proof", proof), ("bench", {"latencies": [{"tool": "x", "p50_ms": 1, "p95_ms": 2}], "node_counts": {"Issue": 999}})]:
        (root / kind).mkdir()
        (root / kind / f"{TENANT}.json").write_text(json.dumps(payload))
    fake.count = 4
    second = client.get(url)
    assert second.status_code == 200
    assert second.json()["proof"] == proof
    assert second.json()["latencies"] == [{"tool": "x", "p50_ms": 1, "p95_ms": 2}]
    assert second.json()["unavailable"] == []
    assert second.json()["node_counts"] == {"Issue": 4}
    assert second.json()["edge_counts"] == {"AUTHORED": 2}

    (root / "proof" / f"{TENANT}.json").write_text("corrupt")
    (root / "bench" / f"{TENANT}.json").write_text("{}")
    third = client.get(url)
    assert third.status_code == 200
    assert third.json()["node_counts"] == {"Issue": 4}
    assert third.json()["edge_counts"] == {"AUTHORED": 2}
    assert third.json()["proof"] is None
    assert third.json()["latencies"] == []
    assert third.json()["unavailable"] == ["proof", "latencies"]


@pytest.mark.parametrize("field", ["p50_ms", "p95_ms"])
@pytest.mark.parametrize("number", ["1e999", "Infinity"])
def test_stats_nonfinite_benchmark_preserves_proof_and_fresh_counts(artifact_client, field, number):
    client, root, fake = artifact_client
    rates = {"reviewer_hit_at_3": 0, "module_hit_at_1": 0.1, "module_hit_at_3": 0.2, "label_precision_at_2": 0.3}
    proof = {"n": 1, "methods": {method: rates for method in ("graph", "codeowners", "similar_issues")}, "rows": []}
    (root / "proof").mkdir()
    (root / "proof" / f"{TENANT}.json").write_text(json.dumps(proof))
    (root / "bench").mkdir()
    row = '{"tool":"x","p50_ms":1,"p95_ms":2}'
    row = row.replace(f'"{field}":' + ("1" if field == "p50_ms" else "2"), f'"{field}":{number}')
    (root / "bench" / f"{TENANT}.json").write_text('{"latencies":[' + row + ']}')
    fake.count = 4

    response = client.get(f"/tenants/{TENANT}/stats")

    assert response.status_code == 200
    assert response.json()["latencies"] == []
    assert response.json()["unavailable"] == ["latencies"]
    assert response.json()["proof"] == proof
    assert response.json()["node_counts"] == {"Issue": 4}
    assert response.json()["edge_counts"] == {"AUTHORED": 2}


@pytest.mark.parametrize("tenant", ["unknown", "bad%5Ctenant", "bad%20tenant"])
def test_invalid_stats_tenant_never_accesses_artifacts(artifact_client, monkeypatch, tenant):
    from pathlib import Path

    client, _, _ = artifact_client

    def forbidden(*args, **kwargs):
        pytest.fail("invalid tenant accessed an artifact")

    monkeypatch.setattr(Path, "read_text", forbidden)
    assert client.get(f"/tenants/{tenant}/stats").status_code == 404


def test_default_artifact_directory_is_repository_relative(tmp_path, monkeypatch):
    from api import graph_reads

    root = tmp_path / "repo"
    (root / "data" / "bench").mkdir(parents=True)
    (root / "data" / "bench" / f"{TENANT}.json").write_text(
        '{"latencies": [{"tool": "x", "p50_ms": 0, "p95_ms": 0}]}'
    )
    monkeypatch.setattr(graph_reads, "__file__", str(root / "api" / "graph_reads.py"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(graph_reads, "graph", lambda tenant: SimpleNamespace(ro_query=lambda query: SimpleNamespace(result_set=[])))
    assert graph_reads.LiveGraphReads().stats(TENANT).latencies[0].tool == "x"
