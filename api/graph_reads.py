"""B-owned graph read seam for live tenant-scoped graph routes (B4 Task 1).

This module defines the response adapter that live routes depend on. It does not
implement A's query/connection layer: A owns the named queries in ``cypher/``
(``subgraph.cypher``, ``explain_path.cypher``, ``stats.cypher``) and the graph
client. ``build_live_graph_reads`` is the fail-closed seam where that concrete
reader will be wired; until A lands it, live requests return a sanitized 503 and
never fall back to fixtures.

The mapping helpers below (``build_subgraph``, ``build_path``, ``build_stats``)
turn A's query rows into the shared response models, enforcing the plan's global
constraints in one place: stable keys, the 150-node cap with no dangling edges,
the four-hop evidence path, and excluding the auxiliary ``Node`` label from kind
totals.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from fastapi import HTTPException, Request

from api import schemas
from api.config import Settings


# One-hop neighbourhood cap (spec section 9 / plan global constraints).
MAX_SEED_KEYS = 150
SUBNODE_CAP = 150
# The spec labels every node with the auxiliary `Node` label (see
# `explain_path.cypher`: `MATCH (a:Node {key: $from})`). Counting it as a kind
# would double-count every node, so it is excluded from node kind totals.
AUXILIARY_NODE_LABELS = frozenset({"Node"})
PATH_CYPHER = ("explain_path.cypher",)


class GraphReadsUnavailable(RuntimeError):
    """Raised when live reads are requested without a concrete reader.

    This is a server-side dependency error. Routes translate it (and any other
    backend failure) into a sanitized 503 so internals never leak and fixtures
    are never substituted for live data.
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


def build_live_graph_reads(settings: Settings) -> GraphReads:
    """Construct A's concrete reader for the selected tenant graphs.

    Handoff gate: A's graph adapter/query layer does not exist yet. Rather than
    invent A's query names or connection wiring, this seam fails closed so live
    mode returns a sanitized 503 and never silently serves fixtures.
    """

    raise GraphReadsUnavailable(
        "live graph reads are not configured: A's graph adapter/query layer is unavailable"
    )


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


def build_path(
    *,
    tenant: str,
    from_key: str,
    to_key: str,
    keys: Sequence[str],
    rels: Sequence[str],
    cypher: Sequence[str] | None = None,
    ms: int = 0,
) -> schemas.ToolResult:
    """Wrap A's evidence path into the shared ``ToolResult`` envelope.

    ``PathData`` enforces the four-hop cap and key/rel consistency; an empty path
    is a valid model with ``empty=True``.
    """

    key_list = list(keys)
    rel_list = list(rels)
    data = schemas.PathData(keys=key_list, rels=rel_list)
    return schemas.ToolResult(
        tool="explain_path",
        data=data.model_dump(mode="json"),
        cypher=list(cypher) if cypher else list(PATH_CYPHER),
        params={"tenant": tenant, "from": from_key, "to": to_key},
        evidence=list(key_list),
        ms=ms,
        empty=not key_list,
    )


def build_stats(counts: Mapping[str, Any]) -> schemas.Stats:
    """Map A's counts query into ``Stats``.

    ``proof`` and ``latencies`` stay empty until A13/A14/B11; they are never
    populated from fixture stats. The auxiliary ``Node`` label is excluded from
    node kind totals.
    """

    raw_nodes = counts.get("node_counts") or {}
    raw_edges = counts.get("edge_counts") or {}
    node_counts = {
        str(kind): int(count)
        for kind, count in raw_nodes.items()
        if kind not in AUXILIARY_NODE_LABELS
    }
    edge_counts = {str(rel_type): int(count) for rel_type, count in raw_edges.items()}
    last_ingest_at = counts.get("last_ingest_at")
    return schemas.Stats(
        node_counts=node_counts,
        edge_counts=edge_counts,
        last_ingest_at=last_ingest_at,
        proof=None,
        latencies=[],
    )
