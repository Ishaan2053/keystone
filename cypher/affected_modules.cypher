// Modules an issue likely touches: the 50 past Issues nearest to $q (score is a cosine distance,
// lower = closer), over-fetched because most have no merged fixing PR or are after $before, then the
// modules that merged PRs fixing them touched, ranked by PR count.
// $exclude_issues and $before keep a replay or a backtest from finding its own answer.
// Test modules (tests, test, e2e) are skipped: nearly every fix touches tests, so they would top
// most rankings and send rank_reviewers to test owners rather than to the code's.
CALL db.idx.vector.queryNodes('Issue', 'embedding', 50, vecf32($q)) YIELD node AS past, score
WITH past, score
WHERE NOT past.key IN $exclude_issues AND NOT coalesce(past.incoming, false) AND past.created_at < $before
MATCH (past)<-[:FIXES]-(pr:PR)-[:TOUCHES]->(:File)-[:IN]->(m:Module)
WHERE pr.merged_at < $before AND NOT m.path IN ['tests', 'test', 'e2e']
OPTIONAL MATCH (past)-[:HAS_LABEL]->(l:Label)
RETURN m.key AS module, m.path AS path, count(DISTINCT pr) AS evidence,
       collect(DISTINCT pr.key)[..5] AS prs, collect(DISTINCT past.key)[..3] AS similar,
       collect(DISTINCT CASE WHEN l IS NULL THEN NULL ELSE {key: l.key, name: l.name} END)[..5] AS labels
ORDER BY evidence DESC, module LIMIT 3
