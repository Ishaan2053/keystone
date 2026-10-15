// GET /subgraph (spec section 9): the nodes in $keys plus their 1-hop neighbours, seeds first,
// capped at 150 nodes, and every edge between two returned nodes. Always exactly one row: `nodes`
// and `edges`, both [] when no key in $keys exists. Each node is {key, kind, label, properties};
// label is a display name (title, summary, name, path, @login or owner/name, else the key).
// properties are [name, value] pairs, which the caller turns into a dict for GraphNode, without
// `embedding` (a 384-d vector), `body` (long text) or `triage` (an AgentRun's whole TriageResult).
// Only stable keys are returned, never internal ids (Rule 4).
MATCH (s:Node) WHERE s.key IN $keys
OPTIONAL MATCH (s)--(nb:Node)
WITH collect(DISTINCT s) AS seeds, collect(DISTINCT nb) AS nbs
WITH seeds + [x IN nbs WHERE NOT x IN seeds] AS all
WITH all[..150] AS ns
UNWIND CASE WHEN size(ns) = 0 THEN [null] ELSE ns END AS a
OPTIONAL MATCH (a)-[r]->(b:Node) WHERE b IN ns
WITH ns, collect(DISTINCT CASE WHEN r IS NULL THEN NULL ELSE {source: a.key, target: b.key, type: type(r)} END) AS edges
RETURN [n IN ns | {key: n.key, kind: [l IN labels(n) WHERE l <> 'Node'][0],
        label: coalesce(CASE WHEN n:Person THEN '@' + n.login WHEN n:Repo THEN substring(n.key, 5)
                             WHEN n:Module OR n:File OR n:Doc THEN n.path END,
                        n.title, n.summary, n.name, n.verdict, n.key),
        properties: [k IN keys(n) WHERE NOT k IN ['embedding', 'body', 'triage'] | [k, n[k]]]}] AS nodes, edges
