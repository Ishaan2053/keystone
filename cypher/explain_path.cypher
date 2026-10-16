// The shortest path (at most $max_len hops, either direction) from $from to $to over evidence
// relationships only, so a path never routes through agent memory (USED_EVIDENCE) or Labels.
// IN is included so expertise reaches a module (person -REVIEWED-> pr -TOUCHES-> file -IN-> module);
// two modules of one repo then connect through the Repo. FalkorDB v4.20.4 rejects an undirected
// shortestPath(), so this uses algo.SPpaths; with no weight property every hop costs 1. No path, no row.
// SPpaths searches the whole $max_len neighbourhood even when a shorter path exists (about 1 s at 4
// hops from a busy reviewer), so api/tools/explain_path.py calls this with $max_len 2, then 3, then 4.
MATCH (a:Node {key: $from}), (b:Node {key: $to})
CALL algo.SPpaths({sourceNode: a, targetNode: b,
                   relTypes: ['AUTHORED', 'REVIEWED', 'TOUCHES', 'FIXES', 'IN', 'OWNS', 'MEMBER_OF', 'MADE_IN', 'ABOUT', 'DESCRIBES'],
                   relDirection: 'both', maxLen: $max_len, pathCount: 1}) YIELD path
RETURN [n IN nodes(path) | n.key] AS keys, [r IN relationships(path) | type(r)] AS rels
