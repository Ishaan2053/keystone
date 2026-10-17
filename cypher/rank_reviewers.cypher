// Reviewers for $module: people who authored or reviewed merged PRs touching it before $before,
// weighted 3 for PRs merged after $recent (else 1), divided by 1 + their open review requests.
// An active 'avoid' Rule (global, or scoped to this module) zeroes the score (spec section 8).
// Load and Rules are as of $before: a PR open now and created before $before was also open then
// (REVIEW_REQUESTED isn't kept for closed PRs, so load is a lower bound), and only Rules made by then count
// (<=: a reject and the re-triage it triggers can fall in the same second).
// Returns the top $k, plus anyone a Rule zeroed below them, so the trace shows who was avoided and why.
MATCH (m:Module {key: $module})<-[:IN]-(:File)<-[:TOUCHES]-(pr:PR)
WHERE pr.merged_at IS NOT NULL AND pr.merged_at < $before
MATCH (p:Person)-[:AUTHORED|REVIEWED]->(pr)
WHERE NOT p.login IN $exclude
WITH DISTINCT p, pr
WITH p, sum(CASE WHEN pr.merged_at > $recent THEN 3 ELSE 1 END) AS expertise, collect(pr.key)[..5] AS via
OPTIONAL MATCH (p)<-[:REVIEW_REQUESTED]-(open:PR {state: 'open'})
WHERE open.created_at < $before
WITH p, expertise, via, count(open) AS load
OPTIONAL MATCH (r:Rule {active: true})-[:APPLIES_TO]->(p)
WHERE (r.scope IS NULL OR r.scope = $module) AND r.created_at <= $before
WITH p, expertise, via, load, collect(r.key) AS rules
WITH p, expertise, via, load, rules, CASE WHEN size(rules) > 0 THEN 0.0 ELSE toFloat(expertise) / (1 + load) END AS score
ORDER BY score DESC, expertise DESC, p.login
WITH collect({person: p.key, login: p.login, expertise: expertise, load: load, via: via, rules: rules, score: score}) AS ranked
UNWIND [i IN range(0, size(ranked) - 1) WHERE i < $k OR size(ranked[i].rules) > 0 | ranked[i]] AS r
RETURN r.person AS person, r.login AS login, r.expertise AS expertise, r.load AS load, r.via AS via, r.rules AS rules, r.score AS score
