"""B-owned graph read seam for live tenant-scoped graph routes (B4 Task 1).

Live routes depend on the :class:`GraphReads` protocol. :class:`LiveGraphReads`
implements it on A's public modules: the named queries in ``cypher/``
(``subgraph.cypher``, ``stats.cypher``) run through ``api.db``, and ``/path`` is
A's ``explain_path`` tool. ``stats()`` also reads the ``data/proof`` and
``data/bench`` artifacts from ``data_dir`` (default ``<repo>/data``, mounted
read-only in compose). A failing reader is a sanitized 503 at the route;
live mode never falls back to fixtures.

The mapping helpers below (``build_subgraph``, ``stats_counts``, ``build_stats``)
turn A's query rows into the shared response models, enforcing the plan's global
constraints in one place: stable keys and the 150-node cap with no dangling edges.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import HTTPException, Request

from api import cypher, schemas
from api.config import Settings
from api.db import graph
from api.metric_artifacts import read_metric_artifacts
from api.tools import explain_path

# One-hop neighbourhood cap (spec section 9 / plan global constraints).
MAX_SEED_KEYS = 150
SUBNODE_CAP = 150


class GraphReadsUnavailable(RuntimeError):
    """Raised when the live reader cannot be constructed.

    Startup leaves the reader unset, so routes return a sanitized 503 and
    fixtures are never substituted for live data.
    """


@runtime_checkable
class GraphReads(Protocol):
    """Tenant-scoped graph reads backing `/subgraph`, `/path` and `/stats`."""

    def subgraph(self, tenant: str, keys: list[str]) -> schemas.Subgraph: ...

    def path(self, tenant: str, from_key: str, to_key: str) -> schemas.ToolResult: ...

    def stats(self, tenant: str) -> schemas.Stats: ...


def get_graph_reads(request: Request) -> GraphReads:
    """Return the reader injected on app state, or fail closed with a 503.

    Fixture mode never stores a reader, so this also guarantees fixture mode can
    never accidentally serve live reads.
    """

    reader = getattr(request.app.state, "graph_reads", None)
    if reader is None:
        raise HTTPException(status_code=503, detail="Live graph reader unavailable")
    return reader


class LiveGraphReads:
    """Reads one tenant graph, ``tenant_<slug>``, with A's named queries and
    ``explain_path``; ``stats()`` adds the tenant's Proof and bench artifacts
    from ``data_dir`` (default ``<repo>/data``; mounted read-only in compose).

    Constructing it connects to nothing: ``api.db`` opens its client on the
    first query, with ``FALKORDB_*`` from the environment.
    """

    def __init__(self, *, data_dir: Path | None = None) -> None:
        self.data_dir = (
            data_dir if data_dir is not None else Path(__file__).resolve().parents[1] / "data"
        )

    def subgraph(self, tenant: str, keys: list[str]) -> schemas.Subgraph:
        # subgraph.cypher always returns one row, [nodes, edges].
        res = graph(tenant).ro_query(cypher.query("subgraph"), {"keys": keys})
        nodes, edges = res.result_set[0] if res.result_set else ([], [])
        return build_subgraph(keys, nodes, edges)

    def path(self, tenant: str, from_key: str, to_key: str) -> schemas.ToolResult:
        return explain_path(tenant, from_key, to_key)

    def stats(self, tenant: str) -> schemas.Stats:
        proof, latencies, unavailable = read_metric_artifacts(tenant, self.data_dir)
        res = graph(tenant).ro_query(cypher.query("stats"))
        return build_stats(stats_counts(res.result_set)).model_copy(
            update={"proof": proof, "latencies": latencies, "unavailable": unavailable}
        )


def build_live_graph_reads(settings: Settings) -> GraphReads:
    """The live reader for the configured tenants; lazy, so startup never connects."""

    return LiveGraphReads()


def parse_seed_keys(raw: str) -> list[str]:
    """Parse the `keys` query parameter into a stable, deduplicated key list.

    Live mode pins the key list to a JSON array string
    (``keys=["issue:owner/repo#1"]``) so keys containing commas stay
    unambiguous; the existing single-key form is still accepted. Blank elements,
    malformed arrays and more than ``MAX_SEED_KEYS`` distinct keys are rejected
    with 422 before any graph access.
    """

    text = raw.strip()
    if text.startswith("[") or text.startswith("{"):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422, detail="keys must be a JSON array of strings or a single key"
            ) from exc
        if not isinstance(decoded, list):
            raise HTTPException(status_code=422, detail="keys must be a JSON array of strings")
        candidates: Sequence[Any] = decoded
    else:
        candidates = [raw]

    if not candidates:
        raise HTTPException(status_code=422, detail="keys must include at least one key")

    keys: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, str) or not item.strip():
            raise HTTPException(status_code=422, detail="keys must be nonblank strings")
        if item in seen:
            continue
        seen.add(item)
        keys.append(item)

    if len(keys) > MAX_SEED_KEYS:
        raise HTTPException(
            status_code=422, detail=f"at most {MAX_SEED_KEYS} distinct keys are allowed"
        )

    return keys


def build_subgraph(
    seed_keys: Sequence[str],
    nodes: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
    *,
    cap: int = SUBNODE_CAP,
) -> schemas.Subgraph:
    """Map query rows into a capped subgraph with no dangling edges.

    Seeds are kept first (in request order) so a truncated neighbourhood still
    contains the requested anchors; remaining nodes follow in query order. Edges
    are only kept when both endpoints survive the cap.
    """

    node_rows = list(nodes)

    ordered: list[schemas.GraphNode] = []
    seen: set[str] = set()
    rows_by_key: dict[str, Mapping[str, Any]] = {}
    for row in node_rows:
        rows_by_key.setdefault(str(row["key"]), row)

    def add(row: Mapping[str, Any]) -> None:
        key = str(row["key"])
        if key in seen or len(ordered) >= cap:
            return
        seen.add(key)
        ordered.append(
            schemas.GraphNode(
                key=key,
                kind=str(row["kind"]),
                label=str(row["label"]),
                properties=dict(row.get("properties") or {}),
            )
        )

    for key in seed_keys:
        row = rows_by_key.get(key)
        if row is not None:
            add(row)
    for row in node_rows:
        add(row)

    kept = {node.key for node in ordered}
    kept_edges: list[schemas.GraphEdge] = []
    edge_seen: set[tuple[str, str, str]] = set()
    for row in edges:
        source, target, rel_type = str(row["source"]), str(row["target"]), str(row["type"])
        if source not in kept or target not in kept:
            continue
        signature = (source, target, rel_type)
        if signature in edge_seen:
            continue
        edge_seen.add(signature)
        kept_edges.append(schemas.GraphEdge(source=source, target=target, type=rel_type))

    return schemas.Subgraph(nodes=ordered, edges=kept_edges)


def stats_counts(rows: Iterable[Sequence[Any]]) -> dict[str, Any]:
    """Pivot ``stats.cypher`` rows, ``(kind, name, n)`` with kind ``node`` or ``edge``."""

    counts: dict[str, Any] = {"node_counts": {}, "edge_counts": {}, "last_ingest_at": None}
    for kind, name, n in rows:
        counts["node_counts" if kind == "node" else "edge_counts"][name] = n
    return counts


def build_stats(counts: Mapping[str, Any]) -> schemas.Stats:
    """Map A's counts (see ``stats_counts``) into ``Stats``.

    ``proof``, ``latencies`` and ``unavailable`` stay empty here; the live
    reader enriches them from optional artifacts, never fixture stats.
    ``stats.cypher`` already leaves out the auxiliary ``Node`` label.
    """

    raw_nodes = counts.get("node_counts") or {}
    raw_edges = counts.get("edge_counts") or {}
    node_counts = {str(kind): int(count) for kind, count in raw_nodes.items()}
    edge_counts = {str(rel_type): int(count) for rel_type, count in raw_edges.items()}
    last_ingest_at = counts.get("last_ingest_at")
    return schemas.Stats(
        node_counts=node_counts,
        edge_counts=edge_counts,
        last_ingest_at=last_ingest_at,
        proof=None,
        latencies=[],
    )
