// Reviewers for $module: people who authored or reviewed merged PRs touching it before $before,
// weighted 3 for PRs merged after $recent (else 1), divided by 1 + their open review requests.
// An active 'avoid' Rule (global, or scoped to this module) zeroes the score (spec section 8).
MATCH (m:Module {key: $module})<-[:IN]-(:File)<-[:TOUCHES]-(pr:PR)
WHERE pr.merged_at IS NOT NULL AND pr.merged_at < $before
MATCH (p:Person)-[:AUTHORED|REVIEWED]->(pr)
WHERE NOT p.login IN $exclude
WITH DISTINCT p, pr
WITH p, sum(CASE WHEN pr.merged_at > $recent THEN 3 ELSE 1 END) AS expertise, collect(pr.key)[..5] AS via
OPTIONAL MATCH (p)<-[:REVIEW_REQUESTED]-(open:PR {state: 'open'})
WITH p, expertise, via, count(open) AS load
OPTIONAL MATCH (r:Rule {active: true})-[:APPLIES_TO]->(p)
WHERE r.scope IS NULL OR r.scope = $module
WITH p, expertise, via, load, collect(r.key) AS rules
RETURN p.key AS person, p.login AS login, expertise, load, via, rules,
       CASE WHEN size(rules) > 0 THEN 0.0 ELSE toFloat(expertise) / (1 + load) END AS score
ORDER BY score DESC, expertise DESC, login LIMIT $k
