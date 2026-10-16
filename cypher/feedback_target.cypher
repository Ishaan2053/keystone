// The Action that feedback is about, read before record_feedback writes: no row when $run is not an
// AgentRun; `action` is null when $run did not PROPOSE $action. `rejected` lists the reject Feedback
// already on the Action, oldest first, each with the Rule it created and that Rule's scope, so a
// repeated reject with the same scope reuses them.
MATCH (run:AgentRun {key: $run})
OPTIONAL MATCH (run)-[:PROPOSED]->(a:Action {key: $action})
OPTIONAL MATCH (a)-[:TARGETS]->(p:Person)
OPTIONAL MATCH (a)<-[:ON]-(f:Feedback {verdict: 'reject'})
OPTIONAL MATCH (r:Rule)-[:DERIVED_FROM]->(f)
WITH run, a, p, f, r ORDER BY f.at, f.key
RETURN run.key AS run, a.key AS action, a.kind AS kind, a.module AS module, p.key AS person, p.login AS login,
       collect(CASE WHEN f IS NULL THEN NULL ELSE {feedback: f.key, rule: r.key, scope: r.scope} END) AS rejected
