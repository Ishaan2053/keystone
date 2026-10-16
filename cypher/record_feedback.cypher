// Feedback ON an Action the run PROPOSED, in one write (spec section 8). A reject sets the Action to
// 'rejected'; with a $rule key (a reject on an assign) it also creates an active 'avoid' Rule that
// APPLIES_TO the Action's Person and is DERIVED_FROM the Feedback, scoped to $scope (a module key, or
// null for global). rank_reviewers.cypher reads the Rule. No row when $run did not PROPOSE $action.
MATCH (:AgentRun {key: $run})-[:PROPOSED]->(a:Action {key: $action})
CREATE (f:Feedback:Node {key: $feedback, verdict: $verdict, reason: $reason, given_by: $given_by, at: $at})-[:ON]->(a)
SET a.status = CASE WHEN $verdict = 'reject' THEN 'rejected' ELSE a.status END
WITH a, f
OPTIONAL MATCH (a)-[:TARGETS]->(p:Person)
WHERE $rule IS NOT NULL
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
  CREATE (r:Rule:Node {key: $rule, kind: 'avoid', scope: $scope, text: $text, active: true, created_at: $at})
  CREATE (r)-[:APPLIES_TO]->(p)
  CREATE (r)-[:DERIVED_FROM]->(f))
WITH a, f
OPTIONAL MATCH (r:Rule)-[:DERIVED_FROM]->(f)
RETURN f.key AS feedback_key, a.key AS action_key, f.verdict AS verdict, r.key AS rule_key
