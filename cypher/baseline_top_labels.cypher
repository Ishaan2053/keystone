// Proof, CODEOWNERS baseline, labels (spec section 14): the 2 labels most used on the repo's Issues
// ($issues is its Issue key prefix) created before $before. `newest` is the latest such Issue.
MATCH (i:Issue)-[:HAS_LABEL]->(l:Label)
WHERE i.key STARTS WITH $issues AND i.created_at < $before AND NOT coalesce(i.incoming, false)
WITH l, i ORDER BY i.created_at DESC
WITH l, count(DISTINCT i) AS uses, collect(DISTINCT i.key)[..1] AS newest
RETURN l.name AS label, l.key AS key, uses, newest
ORDER BY uses DESC, label LIMIT 2
