// The Action that feedback is about, read before record_feedback writes: no row when $run is not an
// AgentRun; `action` is null when $run did not PROPOSE $action, and `person` is null when the Action
// TARGETS no Person.
MATCH (run:AgentRun {key: $run})
OPTIONAL MATCH (run)-[:PROPOSED]->(a:Action {key: $action})
OPTIONAL MATCH (a)-[:TARGETS]->(p:Person)
RETURN run.key AS run, a.key AS action, a.kind AS kind, a.module AS module, p.key AS person, p.login AS login
