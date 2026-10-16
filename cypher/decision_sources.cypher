// Decisions job inputs (spec section 4): the $limit merged PRs with the most comments, each with its
// title, body, the module paths it touches (prompt hints) and its human comments in time order.
MATCH (p:PR {state: 'merged'})
WITH p ORDER BY p.comment_count DESC, p.key LIMIT $limit
OPTIONAL MATCH (p)-[:TOUCHES]->(:File)-[:IN]->(m:Module)
WITH p, collect(DISTINCT m.path) AS modules
OPTIONAL MATCH (author:Person)-[:WROTE]->(c:Comment)-[:ON]->(p)
WITH p, modules, c, author ORDER BY c.created_at, c.key
RETURN p.key AS key, p.url AS url, p.title AS title, p.body AS body, p.comment_count AS comment_count, modules,
       collect(CASE WHEN c IS NULL THEN NULL ELSE {login: author.login, body: c.body} END) AS comments
ORDER BY comment_count DESC, key
