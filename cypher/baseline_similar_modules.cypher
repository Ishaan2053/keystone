// Proof, similar-issues (vector only) baseline, modules (spec section 14): the non-test modules
// touched by the merged fix of the single nearest earlier Issue that has one, most files first.
CALL db.idx.vector.queryNodes('Issue', 'embedding', 100, vecf32($q)) YIELD node AS past, score
WITH past, score
WHERE NOT past.key IN $exclude_issues AND NOT coalesce(past.incoming, false) AND past.created_at < $before
MATCH (past)<-[:FIXES]-(pr:PR)-[:TOUCHES]->(:File)-[:IN]->(m:Module)
WHERE pr.merged_at < $before AND NOT m.path IN ['tests', 'test', 'e2e']
WITH past, score ORDER BY score ASC, past.key LIMIT 1
MATCH (past)<-[:FIXES]-(pr:PR)-[:TOUCHES]->(f:File)-[:IN]->(m:Module)
WHERE pr.merged_at < $before AND NOT m.path IN ['tests', 'test', 'e2e']
RETURN m.key AS module, count(DISTINCT f) AS files, past.key AS similar, collect(DISTINCT pr.key) AS prs
ORDER BY files DESC, module LIMIT 3
