"""Live graph route adapter tests (B4 Task 1).

These tests exercise the B-owned seam: settings validation, dependency
injection, tenant/input validation, route dispatch, the mapping of the exact
rows ``subgraph.cypher`` and ``stats.cypher`` return, and lifecycle. They use an
injected fake reader or a fake graph; tests/test_live_graph_routes.py runs the
real reader against the live ``tenant_falkordb`` graph.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api import schemas
from api.config import Settings, load_settings
from api.fixture_store import FixtureStore
from api import cypher
from api.graph_reads import (
    MAX_SEED_KEYS,
    LiveGraphReads,
    build_stats,
    build_subgraph,
    stats_counts,
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
        return schemas.ToolResult(
            tool="explain_path", data={"keys": [], "rels": []}, cypher=["MATCH ..."],
            params={"tenant": tenant, "from": from_key, "to": to_key}, evidence=[], ms=0, empty=True,
        )

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


def test_second_tenant_dispatch(monkeypatch):
    """B9: the existing dispatch routes per-tenant with no new API code.

    Routes check ``Settings.tenants`` while tools separately read process
    ``TENANTS`` (``api/tools/_run.py``), so both are configured together.
    Regression-only: the full subgraph/path/stats call tuples pin the exact
    tenant argument reaching the reader for each configured slug.
    """
    monkeypatch.setenv("TENANTS", "falkordb,pallets")
    reader = FakeGraphReads()
    client = live_client(reader, tenants=("falkordb", "pallets"))

    responses = [
        client.get("/tenants/falkordb/subgraph", params={"keys": "a"}),
        client.get("/tenants/falkordb/path", params={"from": "a", "to": "b"}),
        client.get("/tenants/pallets/subgraph", params={"keys": "a"}),
        client.get("/tenants/pallets/path", params={"from": "a", "to": "b"}),
        client.get("/tenants/pallets/stats"),
    ]
    unknown = client.get("/tenants/unknown/stats")

    assert {t["slug"] for t in client.get("/tenants").json()["tenants"]} == {"falkordb", "pallets"}
    assert all(response.status_code == 200 for response in responses)
    assert reader.calls[-1] == ("stats", "pallets")
    assert unknown.status_code == 404

    assert reader.calls == [
        ("subgraph", "falkordb", ["a"]),
        ("path", "falkordb", "a", "b"),
        ("subgraph", "pallets", ["a"]),
        ("path", "pallets", "a", "b"),
        ("stats", "pallets"),
    ]


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


def test_live_reader_is_built_at_startup_not_create_app(monkeypatch):
    """The live reader is constructed through the app lifecycle, not at import.

    Regression: live mode used to call ``build_live_graph_reads`` inside
    ``create_app`` (and therefore at import via the module-level ``app``),
    connecting before the app ever started. It must instead be built on lifespan
    startup and closed on shutdown.
    """

    events: list[str] = []

    class LifecycleReader(FakeGraphReads):
        def close(self) -> None:
            events.append("close")
            super().close()

    reader = LifecycleReader()

    def spy_build(settings):  # pragma: no cover - only counted, never asserted on
        events.append("build")
        return reader

    monkeypatch.setattr("api.main.build_live_graph_reads", spy_build)

    settings = Settings(
        tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live"
    )
    app = create_app(settings=settings, fixture_store=FixtureStore.default())

    # create_app() (and the module-level ``app = create_app()`` at import) must
    # not construct or connect the live reader.
    assert events == []
    assert app.state.graph_reads is None

    with TestClient(app) as client:
        assert events == ["build"]
        assert app.state.graph_reads is reader
        assert client.get("/tenants/falkordb/stats").status_code == 200
        assert reader.closed is False

    assert events == ["build", "close"]
    assert reader.closed is True


def test_live_without_reader_fails_closed_through_lifespan(monkeypatch):
    """A reader that cannot be built leaves startup running and fails closed with a 503."""

    def broken(settings):
        raise RuntimeError("falkordb://admin:s3cret@db:6379 unreachable")

    monkeypatch.setattr("api.main.build_live_graph_reads", broken)
    settings = Settings(
        tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live"
    )
    app = create_app(settings=settings, fixture_store=FixtureStore.default())

    with TestClient(app) as client:
        assert app.state.graph_reads is None
        response = client.get("/tenants/falkordb/stats")

    assert response.status_code == 503
    assert "fixture" not in response.text.lower()
    assert "s3cret" not in response.text


def test_live_reader_construction_does_not_connect(monkeypatch):
    def no_connection(*args, **kwargs):  # pragma: no cover - only called on regression
        raise AssertionError("building the live reader must not connect")

    monkeypatch.setattr("api.graph_reads.graph", no_connection)
    settings = Settings(
        tenants=("falkordb",), cors_origins=("http://localhost:3001",), api_mode="live"
    )
    app = create_app(settings=settings, fixture_store=FixtureStore.default())

    with TestClient(app):
        assert isinstance(app.state.graph_reads, LiveGraphReads)


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

    reader = FakeGraphReads()
    client = live_client(reader)
    empty_subgraph = client.get("/tenants/falkordb/subgraph", params={"keys": "a"})
    empty_path = client.get("/tenants/falkordb/path", params={"from": "a", "to": "b"})
    assert empty_subgraph.status_code == 200
    assert empty_subgraph.json() == {"nodes": [], "edges": []}
    assert empty_path.status_code == 200
    assert empty_path.json()["empty"] is True


# The exact rows the queries return (checked against tenant_falkordb): stats.cypher
# is a UNION of (kind, name, n) rows; subgraph.cypher is one [nodes, edges] row
# whose node properties are [name, value] pairs.
STATS_ROWS = [
    ["node", "Issue", 3],
    ["node", "Person", 4],
    ["edge", "AUTHORED", 5],
    ["edge", "HAS_LABEL", 2],
]
SUBGRAPH_ROW = [
    [
        {
            "key": "issue:owner/repo#1",
            "kind": "Issue",
            "label": "Crash on empty graph",
            "properties": [["key", "issue:owner/repo#1"], ["number", 1], ["incoming", False]],
        },
        {
            "key": "person:octocat",
            "kind": "Person",
            "label": "@octocat",
            "properties": [["key", "person:octocat"], ["login", "octocat"]],
        },
    ],
    [{"source": "person:octocat", "target": "issue:owner/repo#1", "type": "AUTHORED"}],
]


def test_stats_rows_pivot_into_node_and_edge_counts():
    stats = build_stats(stats_counts(STATS_ROWS))

    assert stats.node_counts == {"Issue": 3, "Person": 4}
    assert stats.edge_counts == {"AUTHORED": 5, "HAS_LABEL": 2}
    assert stats.last_ingest_at is None
    assert stats.proof is None
    assert stats.latencies == []
    assert build_stats(stats_counts([])).node_counts == {}


def test_subgraph_row_maps_property_pairs_and_edges():
    nodes, edges = SUBGRAPH_ROW

    subgraph = build_subgraph(["issue:owner/repo#1"], nodes, edges)

    assert [n.key for n in subgraph.nodes] == ["issue:owner/repo#1", "person:octocat"]
    assert subgraph.nodes[0].properties == {"key": "issue:owner/repo#1", "number": 1, "incoming": False}
    assert subgraph.nodes[1].label == "@octocat"
    assert [(e.source, e.target, e.type) for e in subgraph.edges] == [
        ("person:octocat", "issue:owner/repo#1", "AUTHORED")
    ]


class FakeResult:
    def __init__(self, result_set):
        self.result_set = result_set


class FakeGraph:
    """Stands in for ``api.db.graph(tenant)``: records each read-only query."""

    def __init__(self, calls, results):
        self.calls, self.results = calls, results

    def ro_query(self, text, params=None):
        self.calls.append((text, params))
        return FakeResult(self.results.pop(0))


def test_live_reader_runs_named_queries_on_the_tenant_graph(monkeypatch):
    calls, tenants = [], []
    results = [[SUBGRAPH_ROW], STATS_ROWS, [[[], []]]]

    def fake_graph(tenant):
        tenants.append(tenant)
        return FakeGraph(calls, results)

    monkeypatch.setattr("api.graph_reads.graph", fake_graph)
    path = schemas.ToolResult(
        tool="explain_path", data={"keys": ["a", "b"], "rels": ["R"]}, cypher=["MATCH p"],
        params={"tenant": "ghost", "from": "a", "to": "b"}, evidence=["a", "b"], ms=1, empty=False,
    )
    path_calls = []
    monkeypatch.setattr("api.graph_reads.explain_path", lambda *args: path_calls.append(args) or path)
    reader = LiveGraphReads()

    subgraph = reader.subgraph("ghost", ["issue:owner/repo#1"])
    stats = reader.stats("ghost")
    empty = reader.subgraph("ghost", ["missing"])

    assert tenants == ["ghost", "ghost", "ghost"]
    assert calls[0] == (cypher.query("subgraph"), {"keys": ["issue:owner/repo#1"]})
    assert calls[1] == (cypher.query("stats"), None)
    assert len(subgraph.nodes) == 2 and stats.edge_counts["AUTHORED"] == 5
    assert empty.nodes == [] and empty.edges == []
    assert reader.path("ghost", "a", "b") is path
    assert path_calls == [("ghost", "a", "b")]


def test_stats_without_ingest_timestamp_is_null():
    stats = build_stats({"node_counts": {}, "edge_counts": {}})

    assert stats.node_counts == {}
    assert stats.edge_counts == {}
    assert stats.last_ingest_at is None
    assert stats.proof is None
    assert stats.latencies == []
