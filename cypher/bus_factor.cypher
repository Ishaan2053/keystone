// Bus factor for $module: merged PRs touching it in ($since, $before), by author. Always returns
// one row when the module exists; the tool sets `empty` from total = 0, not from the row count.
MATCH (m:Module {key: $module})
OPTIONAL MATCH (m)<-[:IN]-(:File)<-[:TOUCHES]-(pr:PR)<-[:AUTHORED]-(p:Person)
WHERE pr.merged_at > $since AND pr.merged_at < $before
WITH m, p, count(DISTINCT pr) AS n ORDER BY n DESC, p.login
WITH m, collect(CASE WHEN p IS NULL THEN NULL ELSE {key: p.key, login: p.login, n: n} END) AS authors
WITH m, authors, reduce(t = 0, a IN authors | t + a.n) AS total
RETURN m.key AS module, total, authors[0] AS top, size(authors) AS active_authors,
       CASE WHEN total > 0 THEN toFloat(authors[0].n) / total ELSE NULL END AS top_share
