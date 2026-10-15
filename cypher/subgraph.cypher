// GET /subgraph (spec section 9): the nodes in $keys plus their 1-hop neighbours, seeds first,
// capped at 150 nodes, and every edge between two returned nodes. One row: `nodes` and `edges`.
// Each node is {key, kind, properties}; properties are [name, value] pairs without `embedding`
// (a 384-d vector) or `body` (long text). Only stable keys are returned, never internal ids (Rule 4).
MATCH (s:Node) WHERE s.key IN $keys
OPTIONAL MATCH (s)--(nb:Node)
WITH collect(DISTINCT s) AS seeds, collect(DISTINCT nb) AS nbs
WITH seeds + [x IN nbs WHERE NOT x IN seeds] AS all
WITH all[..150] AS ns
UNWIND ns AS a
OPTIONAL MATCH (a)-[r]->(b:Node) WHERE b IN ns
WITH ns, collect(DISTINCT CASE WHEN r IS NULL THEN NULL ELSE {source: a.key, target: b.key, type: type(r)} END) AS edges
RETURN [n IN ns | {key: n.key, kind: [l IN labels(n) WHERE l <> 'Node'][0],
        properties: [k IN keys(n) WHERE NOT k IN ['embedding', 'body'] | [k, n[k]]]}] AS nodes, edges
