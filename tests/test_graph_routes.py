"""Live graph route adapter tests (B4 Task 1).

These tests exercise the B-owned seam: settings validation, dependency
injection, tenant/input validation, route dispatch, and the fail-closed live
reader factory. A's concrete query/connection layer is a recorded handoff, so
the tests use an injected fake reader.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api import schemas
from api.config import Settings, load_settings
from api.fixture_store import FixtureStore
from api.graph_reads import (
    MAX_SEED_KEYS,
    build_path,
    build_stats,
    build_subgraph,
)
from api.main import create_app


class FakeGraphReads:
    """In-memory GraphReads implementation used to observe dispatch."""

    def __init__(self, *, subgraph=None, path=None, stats=None, error=None):
        self.calls: list[tuple] = []
        self.closed = False
        self._subgraph = subgraph
        self._path = path
        self._stats = stats
        self._error = error

    def subgraph(self, tenant: str, keys: list[str]) -> schemas.Subgraph:
        self.calls.append(("subgraph", tenant, list(keys)))
        self._raise()
        if self._subgraph is not None:
            return self._subgraph
        return schemas.Subgraph(nodes=[], edges=[])

    def path(self, tenant: str, from_key: str, to_key: str) -> schemas.ToolResult:
        self.calls.append(("path", tenant, from_key, to_key))
        self._raise()
        if self._path is not None:
            return self._path
        return build_path(tenant=tenant, from_key=from_key, to_key=to_key, keys=[], rels=[])

    def stats(self, tenant: str) -> schemas.Stats:
        self.calls.append(("stats", tenant))
        self._raise()
        if self._stats is not None:
            return self._stats
        return schemas.Stats(
            node_counts={}, edge_counts={}, last_ingest_at=None, proof=None, latencies=[]
        )

    def close(self) -> None:
        self.closed = True

    def _raise(self) -> None:
        if self._error is not None:
            raise self._error


def live_client(reader, tenants=("falkordb", "ghost")) -> TestClient:
    settings = Settings(
        tenants=tenants,
        cors_origins=("http://localhost:3001",),
        api_mode="live",
    )
    return TestClient(
        create_app(settings=settings, fixture_store=FixtureStore.default(), graph_reads=reader)
    )


# --------------------------------------------------------------------------
# Step 1: live routes use the selected tenant and reader
# --------------------------------------------------------------------------


def test_live_routes_use_selected_tenant_and_reader():
    node = schemas.GraphNode(
        key="issue:owner/repo#1", kind="Issue", label="1", properties={"number": 1}
    )
    reader = FakeGraphReads(
        subgraph=schemas.Subgraph(nodes=[node], edges=[]),
        stats=schemas.Stats(
            node_counts={"Issue": 1},
            edge_counts={},
            last_ingest_at=None,
            proof=None,
            latencies=[],
        ),
    )
    client = live_client(reader)

    subgraph = client.get(
        "/tenants/ghost/subgraph", params={"keys": json.dumps(["issue:owner/repo#1"])}
    )
    path = client.get(
        "/tenants/ghost/path",
        params={"from": "issue:owner/repo#1", "to": "label:owner/repo/graph"},
    )
    stats = client.get("/tenants/ghost/stats")

    assert subgraph.status_code == 200
    assert subgraph.json()["nodes"][0]["key"] == "issue:owner/repo#1"
    assert path.status_code == 200
    assert path.json()["data"] == {"keys": [], "rels": []}
    assert stats.status_code == 200
    assert stats.json()["node_counts"] == {"Issue": 1}

    assert reader.calls == [
        ("subgraph", "ghost", ["issue:owner/repo#1"]),
        ("path", "ghost", "issue:owner/repo#1", "label:owner/repo/graph"),
        ("stats", "ghost"),
    ]


def test_unknown_tenant_never_calls_reader():
    reader = FakeGraphReads()
    client = live_client(reader, tenants=("falkordb",))

    assert client.get("/tenants/unknown/subgraph", params={"keys": "a"}).status_code == 404
    assert client.get("/tenants/unknown/path", params={"from": "a", "to": "b"}).status_code == 404
    assert client.get("/tenants/unknown/stats").status_code == 404

    assert reader.calls == []


def test_live_reader_error_is_503_without_fixture_fallback():
    reader = FakeGraphReads(error=RuntimeError("falkordb://admin:s3cret@db:6379 unreachable"))
    client = live_client(reader)

    requests = [
        ("/tenants/falkordb/subgraph", {"keys": "a"}),
        ("/tenants/falkordb/path", {"from": "a", "to": "b"}),
        ("/tenants/falkordb/stats", {}),
    ]
    for url, params in requests:
        response = client.get(url, params=params)
        assert response.status_code == 503
        assert "s3cret" not in response.text
        assert "unreachable" not in response.text
        assert "nodes" not in response.text

    assert [call[0] for call in reader.calls] == ["subgraph", "path", "stats"]


def test_live_without_configured_reader_is_fail_closed_503():
    settings = Settings(
        tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live"
    )
    client = TestClient(create_app(settings=settings, fixture_store=FixtureStore.default()))

    response = client.get("/tenants/falkordb/stats")

    assert response.status_code == 503
    assert "fixture" not in response.text.lower()


def test_live_mode_does_not_read_fixtures():
    class ExplodingStore:
        def load(self, *args, **kwargs):
            raise AssertionError("fixture store must not be read in live mode")

    settings = Settings(
        tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live"
    )
    client = TestClient(
        create_app(settings=settings, fixture_store=ExplodingStore(), graph_reads=FakeGraphReads())
    )

    assert client.get("/tenants/falkordb/stats").status_code == 200
    assert client.get("/tenants/falkordb/subgraph", params={"keys": "a"}).status_code == 200
    assert client.get("/tenants/falkordb/path", params={"from": "a", "to": "b"}).status_code == 200


# --------------------------------------------------------------------------
# Step 3/5: settings + reader lifecycle
# --------------------------------------------------------------------------


def test_load_settings_accepts_exactly_fixtures_or_live(monkeypatch):
    monkeypatch.setenv("API_MODE", "fixtures")
    assert load_settings().api_mode == "fixtures"

    monkeypatch.setenv("API_MODE", "live")
    assert load_settings().api_mode == "live"


def test_load_settings_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("API_MODE", "staging")
    with pytest.raises(ValueError):
        load_settings()


def test_create_app_fails_startup_on_invalid_api_mode(monkeypatch):
    monkeypatch.setenv("API_MODE", "staging")
    with pytest.raises(ValueError):
        create_app()


def test_fixture_mode_does_not_construct_live_reader(monkeypatch):
    def explode(settings):  # pragma: no cover - only called on regression
        raise AssertionError("fixture mode must not construct a live reader")

    monkeypatch.setattr("api.main.build_live_graph_reads", explode)

    settings = Settings(tenants=("falkordb",), cors_origins=("http://localhost:3001",))
    client = TestClient(create_app(settings=settings, fixture_store=FixtureStore.default()))

    assert client.get("/tenants/falkordb/stats").status_code == 200


def test_fixture_mode_ignores_injected_reader():
    reader = FakeGraphReads(error=AssertionError("fixture mode must not call the live reader"))
    settings = Settings(tenants=("falkordb",), cors_origins=("http://localhost:3001",))
    client = TestClient(
        create_app(settings=settings, fixture_store=FixtureStore.default(), graph_reads=reader)
    )

    assert client.get("/tenants/falkordb/stats").status_code == 200
    assert client.get("/tenants/falkordb/subgraph", params={"keys": "a"}).status_code == 200
    assert client.get("/tenants/falkordb/path", params={"from": "a", "to": "b"}).status_code == 200
    assert reader.calls == []


def test_live_reader_is_closed_on_shutdown():
    reader = FakeGraphReads()
    settings = Settings(
        tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live"
    )

    with TestClient(
        create_app(settings=settings, fixture_store=FixtureStore.default(), graph_reads=reader)
    ) as client:
        assert client.get("/tenants/falkordb/stats").status_code == 200
        assert reader.closed is False

    assert reader.closed is True


# --------------------------------------------------------------------------
# Step 5: key-list encoding
# --------------------------------------------------------------------------


def test_encoded_keys_round_trip():
    reader = FakeGraphReads()
    client = live_client(reader)
    key = "issue:owner/repo#1"
    comma_key = "issue:owner/repo#1,2"

    assert client.get(
        "/tenants/falkordb/subgraph", params={"keys": json.dumps([key])}
    ).status_code == 200
    assert client.get("/tenants/falkordb/subgraph", params={"keys": key}).status_code == 200
    assert client.get(
        "/tenants/falkordb/subgraph", params={"keys": json.dumps([comma_key])}
    ).status_code == 200

    assert [call[2] for call in reader.calls] == [[key], [key], [comma_key]]


def test_duplicate_seed_keys_are_deduplicated_stably():
    reader = FakeGraphReads()
    client = live_client(reader)
    payload = ["issue:a#1", "issue:b#2", "issue:a#1"]

    response = client.get("/tenants/falkordb/subgraph", params={"keys": json.dumps(payload)})

    assert response.status_code == 200
    assert reader.calls == [("subgraph", "falkordb", ["issue:a#1", "issue:b#2"])]


@pytest.mark.parametrize(
    "raw",
    [
        "   ",
        json.dumps(["  "]),
        "[not json",
        json.dumps([1]),
        json.dumps({"key": "value"}),
        "[]",
    ],
)
def test_blank_or_malformed_keys_rejected(raw):
    reader = FakeGraphReads()
    client = live_client(reader)

    response = client.get("/tenants/falkordb/subgraph", params={"keys": raw})

    assert response.status_code == 422
    assert reader.calls == []


def test_too_many_distinct_seed_keys_rejected():
    reader = FakeGraphReads()
    client = live_client(reader)
    payload = [f"issue:owner/repo#{idx}" for idx in range(MAX_SEED_KEYS + 1)]

    response = client.get("/tenants/falkordb/subgraph", params={"keys": json.dumps(payload)})

    assert response.status_code == 422
    assert reader.calls == []


# --------------------------------------------------------------------------
# Step 4/6: cap, endpoints, empty models, stats mapping
# --------------------------------------------------------------------------


def test_subgraph_cap_and_endpoints():
    seed = "issue:owner/repo#1"
    nodes = [{"key": seed, "kind": "Issue", "label": "1", "properties": {}}]
    nodes += [
        {"key": f"person:p{idx}", "kind": "Person", "label": f"p{idx}", "properties": {}}
        for idx in range(200)
    ]
    edges = [
        {"source": seed, "target": "person:p0", "type": "AUTHORED"},
        {"source": seed, "target": "person:p199", "type": "MENTIONS"},
        {"source": "person:p0", "target": "person:p1", "type": "KNOWS"},
    ]

    subgraph = build_subgraph([seed], nodes, edges)

    keys = {node.key for node in subgraph.nodes}
    assert len(subgraph.nodes) <= 150
    assert subgraph.nodes[0].key == seed
    assert all(edge.source in keys and edge.target in keys for edge in subgraph.edges)
    assert all(edge.target != "person:p199" for edge in subgraph.edges)
    schemas.Subgraph.model_validate(subgraph.model_dump(mode="json"))


def test_empty_path_and_subgraph():
    subgraph = build_subgraph([], [], [])
    assert subgraph.nodes == [] and subgraph.edges == []

    path = build_path(tenant="falkordb", from_key="issue:a#1", to_key="label:b", keys=[], rels=[])
    assert path.empty is True
    assert path.tool == "explain_path"
    schemas.ToolResult.model_validate(path.model_dump(mode="json"))
    data = schemas.PathData.model_validate(path.data)
    assert data.keys == [] and data.rels == []

    reader = FakeGraphReads()
    client = live_client(reader)
    empty_subgraph = client.get("/tenants/falkordb/subgraph", params={"keys": "a"})
    empty_path = client.get("/tenants/falkordb/path", params={"from": "a", "to": "b"})
    assert empty_subgraph.status_code == 200
    assert empty_subgraph.json() == {"nodes": [], "edges": []}
    assert empty_path.status_code == 200
    assert empty_path.json()["empty"] is True


def test_stats_counts_are_not_double_counted_by_node_label():
    stats = build_stats(
        {
            "node_counts": {"Node": 42, "Issue": 3, "Person": 4},
            "edge_counts": {"HAS_LABEL": 2},
            "last_ingest_at": 1760400000,
        }
    )

    assert "Node" not in stats.node_counts
    assert stats.node_counts == {"Issue": 3, "Person": 4}
    assert stats.edge_counts == {"HAS_LABEL": 2}
    assert stats.last_ingest_at == 1760400000
    assert stats.proof is None
    assert stats.latencies == []


def test_stats_without_ingest_timestamp_is_null():
    stats = build_stats({"node_counts": {}, "edge_counts": {}})

    assert stats.node_counts == {}
    assert stats.edge_counts == {}
    assert stats.last_ingest_at is None
    assert stats.proof is None
    assert stats.latencies == []
