// The Action that feedback is about, read before record_feedback writes: no row when $run is not an
// AgentRun; `action` is null when $run did not PROPOSE $action. `rejected` is the earliest reject
// Feedback already on the Action and the Rule it created, so a repeated reject reuses them.
MATCH (run:AgentRun {key: $run})
OPTIONAL MATCH (run)-[:PROPOSED]->(a:Action {key: $action})
OPTIONAL MATCH (a)-[:TARGETS]->(p:Person)
OPTIONAL MATCH (a)<-[:ON]-(f:Feedback {verdict: 'reject'})
OPTIONAL MATCH (r:Rule)-[:DERIVED_FROM]->(f)
WITH run, a, p, f, r ORDER BY f.at, f.key
RETURN run.key AS run, a.key AS action, a.kind AS kind, a.module AS module, p.key AS person, p.login AS login,
       head(collect(CASE WHEN f IS NULL THEN NULL ELSE {feedback: f.key, rule: r.key} END)) AS rejected
