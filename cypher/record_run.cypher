// One AgentRun in one query, so in one transaction (spec section 7, step 6): the run with its
// TriageResult as JSON for recall_run, HANDLED to its Issue (none for a raw title and body),
// USED_EVIDENCE to every key it cited, and its PROPOSED Actions with status 'proposed', each
// TARGETS its Person or Label when that node exists. Assign actions carry their module key.
// $actions rows: {key, kind, value, module, target}. Returns the number of Actions created.
CREATE (run:AgentRun:Node {key: $run, at: $at, issue_key: $issue_key, model: $model, llm_calls: $llm_calls,
                           ms: $ms, summary: $summary, fallback: $fallback, triage: $triage})
WITH run
OPTIONAL MATCH (i:Issue {key: $issue_key})
FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | CREATE (run)-[:HANDLED]->(i))
WITH run
OPTIONAL MATCH (e:Node) WHERE e.key IN $evidence
WITH run, collect(DISTINCT e) AS cited
FOREACH (e IN cited | CREATE (run)-[:USED_EVIDENCE]->(e))
WITH run
UNWIND $actions AS a
OPTIONAL MATCH (t:Node {key: a.target})
CREATE (run)-[:PROPOSED]->(act:Action:Node {key: a.key, kind: a.kind, value: a.value, module: a.module,
                                            status: 'proposed'})
FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | CREATE (act)-[:TARGETS]->(t))
RETURN count(act) AS actions
