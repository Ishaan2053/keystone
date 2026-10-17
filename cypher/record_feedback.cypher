// Feedback ON an Action the run PROPOSED, in one write (spec section 8). Every call writes a new
// Feedback; a reject sets the Action to 'rejected'. With a $rule key (a reject on an assign) it MERGEs
// the one 'avoid' Rule for the Action's Person and $scope (a module key, or null for global), keyed by
// record_feedback, (re)activates it, and links it DERIVED_FROM this Feedback, so a repeated reject
// reuses the Rule and concurrent rejects converge on it. rank_reviewers.cypher reads the Rule.
// No row when $run did not PROPOSE $action.
MATCH (:AgentRun {key: $run})-[:PROPOSED]->(a:Action {key: $action})
CREATE (f:Feedback:Node {key: $feedback, verdict: $verdict, reason: $reason, given_by: $given_by, at: $at})-[:ON]->(a)
SET a.status = CASE WHEN $verdict = 'reject' THEN 'rejected' ELSE a.status END
WITH a, f
OPTIONAL MATCH (a)-[:TARGETS]->(p:Person)
WHERE $rule IS NOT NULL
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
  MERGE (r:Rule:Node {key: $rule})
    ON CREATE SET r.kind = 'avoid', r.scope = $scope, r.text = $text, r.created_at = $at
  SET r.active = true
  MERGE (r)-[:APPLIES_TO]->(p)
  CREATE (r)-[:DERIVED_FROM]->(f))
WITH a, f
OPTIONAL MATCH (r:Rule)-[:DERIVED_FROM]->(f)
RETURN f.key AS feedback_key, a.key AS action_key, f.verdict AS verdict, r.key AS rule_key
