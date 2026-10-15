"""/stats, /subgraph and /path in API_MODE=live against tenant_falkordb; skipped when FalkorDB isn't reachable."""
import json

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

try:
    graph(TENANT).ro_query("RETURN 1")
except Exception as exc:  # no server, wrong password: nothing here can run
    pytest.skip(f"FalkorDB not reachable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def client():
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
