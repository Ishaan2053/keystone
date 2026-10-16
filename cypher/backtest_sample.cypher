// Proof sample (spec section 14): up to $n closed Issues with a qualifying FIXES PR: merged, not a
// sweep, reviewed by someone other than the Issue's author, and touching a non-test module.
// Only Issues created $warmup seconds or more after the first merged PR, so every method has history.
// The pool is ordered by created_at and $n are picked evenly across it, so no one burst dominates.
// Truth per Issue, from its qualifying fix PRs only: their reviewers other than the Issue's author,
// the non-test modules they touched (most files first) and the Issue's labels. Test modules (tests,
// test, e2e) count in no metric. `exclude` is [author] (or []): the logins every method leaves out.
MATCH (first:PR) WHERE first.merged_at IS NOT NULL
WITH min(first.merged_at) + $warmup AS start
MATCH (i:Issue)<-[:FIXES]-(pr:PR)-[:TOUCHES]->(:File)-[:IN]->(m:Module)
WHERE i.state = 'closed' AND NOT coalesce(i.incoming, false) AND i.created_at >= start
  AND pr.merged_at IS NOT NULL AND NOT coalesce(pr.sweep, false) AND NOT m.path IN ['tests', 'test', 'e2e']
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(i)
WITH i, pr, a
MATCH (r:Person)-[:REVIEWED]->(pr)
WHERE a IS NULL OR r.key <> a.key
WITH DISTINCT i ORDER BY i.created_at, i.key
WITH collect(i) AS pool
WITH pool, CASE WHEN size(pool) < $n THEN size(pool) ELSE $n END AS n
UNWIND range(0, n - 1) AS j
WITH pool[j * size(pool) / n] AS i
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(i)
WITH i, a
MATCH (i)<-[:FIXES]-(pr:PR)-[:TOUCHES]->(:File)-[:IN]->(m:Module)
WHERE pr.merged_at IS NOT NULL AND NOT coalesce(pr.sweep, false) AND NOT m.path IN ['tests', 'test', 'e2e']
MATCH (r:Person)-[:REVIEWED]->(pr)
WHERE a IS NULL OR r.key <> a.key
WITH i, a, collect(DISTINCT pr) AS prs, collect(DISTINCT r.login) AS reviewers
UNWIND prs AS pr
MATCH (pr)-[:TOUCHES]->(f:File)-[:IN]->(m:Module)
WHERE NOT m.path IN ['tests', 'test', 'e2e']
WITH i, a, prs, reviewers, m, count(DISTINCT f) AS files ORDER BY files DESC, m.key
WITH i, a, [p IN prs | p.key] AS fixes, reviewers, collect(m.key) AS modules
OPTIONAL MATCH (i)-[:HAS_LABEL]->(l:Label)
RETURN i.key AS issue, i.url AS url, i.title AS title, i.body AS body, i.created_at AS before,
       a.login AS author, CASE WHEN a IS NULL THEN [] ELSE [a.login] END AS exclude,
       fixes, reviewers, modules, collect(DISTINCT l.name) AS labels
ORDER BY before, issue
