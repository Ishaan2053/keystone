// Node counts per label (the kind; `Node` itself is left out) and edge counts per relationship type.
// One row each: kind ('node' or 'edge'), name, n. Used by ingest/load.py and GET /tenants/{t}/stats.
MATCH (n)
UNWIND labels(n) AS label
WITH label WHERE label <> 'Node'
RETURN 'node' AS kind, label AS name, count(*) AS n
UNION ALL
MATCH ()-[r]->()
RETURN 'edge' AS kind, type(r) AS name, count(r) AS n
