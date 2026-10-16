// Proof, similar-issues (vector only) baseline, reviewers (spec section 14): the authors and
// reviewers of the merged fixing PRs of the 10 nearest earlier Issues that have one, by PR count.
// The same 100-neighbour over-fetch as affected_modules, filtered the same way.
CALL db.idx.vector.queryNodes('Issue', 'embedding', 100, vecf32($q)) YIELD node AS past, score
WITH past, score
WHERE NOT past.key IN $exclude_issues AND NOT coalesce(past.incoming, false) AND past.created_at < $before
MATCH (past)<-[:FIXES]-(pr:PR)
WHERE pr.merged_at < $before
WITH past, score, collect(pr) AS prs ORDER BY score ASC, past.key LIMIT 10
UNWIND prs AS pr
MATCH (p:Person)-[:AUTHORED|REVIEWED]->(pr)
WHERE NOT p.login IN $exclude
WITH p, pr, past ORDER BY pr.merged_at DESC
WITH p, count(DISTINCT pr) AS n, collect(DISTINCT pr.key)[..5] AS via, collect(DISTINCT past.key)[..5] AS similar
RETURN p.login AS login, p.key AS person, n, via, similar
ORDER BY n DESC, login LIMIT 3
