// The shortest path (at most 4 hops, either direction) from $from to $to over evidence
// relationships only, so a path never routes through agent memory (USED_EVIDENCE) or Labels.
// IN is included so expertise reaches a module (person -REVIEWED-> pr -TOUCHES-> file -IN-> module);
// two modules of one repo then connect through the Repo. FalkorDB v4.20.4 rejects an undirected
// shortestPath(), so this uses algo.SPpaths; with no weight property every hop costs 1. No path, no row.
MATCH (a:Node {key: $from}), (b:Node {key: $to})
CALL algo.SPpaths({sourceNode: a, targetNode: b,
                   relTypes: ['AUTHORED', 'REVIEWED', 'TOUCHES', 'FIXES', 'IN', 'OWNS', 'MEMBER_OF', 'MADE_IN', 'ABOUT', 'DESCRIBES'],
                   relDirection: 'both', maxLen: 4, pathCount: 1}) YIELD path
RETURN [n IN nodes(path) | n.key] AS keys, [r IN relationships(path) | type(r)] AS rels
