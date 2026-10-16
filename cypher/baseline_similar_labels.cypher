// Proof, similar-issues (vector only) baseline, labels (spec section 14): the labels of the 3
// nearest earlier labelled Issues, most used first, ties to the nearest.
CALL db.idx.vector.queryNodes('Issue', 'embedding', 100, vecf32($q)) YIELD node AS past, score
WITH past, score
WHERE NOT past.key IN $exclude_issues AND NOT coalesce(past.incoming, false) AND past.created_at < $before
  AND (past)-[:HAS_LABEL]->(:Label)
WITH past, score ORDER BY score ASC, past.key LIMIT 3
MATCH (past)-[:HAS_LABEL]->(l:Label)
WITH l, count(DISTINCT past) AS uses, min(score) AS nearest, collect(DISTINCT past.key) AS similar
RETURN l.name AS label, l.key AS key, uses, similar
ORDER BY uses DESC, nearest ASC, label LIMIT 2
