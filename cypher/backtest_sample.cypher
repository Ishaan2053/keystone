// Proof sample (spec section 14): up to $n closed Issues with a FIXES PR that is merged, is not a
// sweep, has a reviewer (REVIEWED never holds an author's own review) and touches a non-test module.
// Only Issues created $warmup seconds or more after the first merged PR, so every method has history.
// The pool is ordered by created_at and $n are picked evenly across it, so no one burst dominates.
// Truth per Issue, from its qualifying fix PRs: their reviewers, the non-test modules they touched
// (most files first) and the Issue's labels. Test modules (tests, test, e2e) count in no metric.
MATCH (first:PR) WHERE first.merged_at IS NOT NULL
WITH min(first.merged_at) + $warmup AS start
MATCH (i:Issue)<-[:FIXES]-(pr:PR)-[:TOUCHES]->(:File)-[:IN]->(m:Module)
WHERE i.state = 'closed' AND NOT coalesce(i.incoming, false) AND i.created_at >= start
  AND pr.merged_at IS NOT NULL AND NOT coalesce(pr.sweep, false) AND NOT m.path IN ['tests', 'test', 'e2e']
  AND (pr)<-[:REVIEWED]-(:Person)
WITH DISTINCT i ORDER BY i.created_at, i.key
WITH collect(i) AS pool
WITH pool, CASE WHEN size(pool) < $n THEN size(pool) ELSE $n END AS n
UNWIND range(0, n - 1) AS j
WITH pool[j * size(pool) / n] AS i
MATCH (i)<-[:FIXES]-(pr:PR)
WHERE pr.merged_at IS NOT NULL AND NOT coalesce(pr.sweep, false) AND (pr)<-[:REVIEWED]-(:Person)
OPTIONAL MATCH (author:Person)-[:AUTHORED]->(i)
WITH i, author.login AS author, collect(DISTINCT pr) AS prs
UNWIND prs AS pr
MATCH (pr)-[:TOUCHES]->(f:File)-[:IN]->(m:Module)
WHERE NOT m.path IN ['tests', 'test', 'e2e']
WITH i, author, prs, m, count(DISTINCT f) AS files ORDER BY files DESC, m.key
WITH i, author, prs, collect(m.key) AS modules
UNWIND prs AS pr
MATCH (r:Person)-[:REVIEWED]->(pr)
WITH i, author, modules, collect(DISTINCT pr.key) AS fixes, collect(DISTINCT r.login) AS reviewers
OPTIONAL MATCH (i)-[:HAS_LABEL]->(l:Label)
RETURN i.key AS issue, i.url AS url, i.title AS title, i.body AS body, i.created_at AS before, author,
       fixes, reviewers, modules, collect(DISTINCT l.name) AS labels
ORDER BY before, issue
