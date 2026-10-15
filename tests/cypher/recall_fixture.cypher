// Test fixture for recall_run: an AgentRun with cited evidence, two proposed Actions, a reject
// Feedback on the assign and the (inactive) Rule it created. tests/test_tools.py deletes it after.
MATCH (i:Issue {key: $issue}), (p:Person {key: $person}), (m:Module {key: $module})
CREATE (run:AgentRun:Node {key: $run, at: $at, issue_key: $issue, model: 'fixture', llm_calls: 0, ms: 5,
                           summary: 'recall_run test fixture', fallback: true, triage: $triage})
CREATE (run)-[:HANDLED]->(i), (run)-[:USED_EVIDENCE]->(i), (run)-[:USED_EVIDENCE]->(p), (run)-[:USED_EVIDENCE]->(m)
CREATE (run)-[:PROPOSED]->(a1:Action:Node {key: $assign, kind: 'assign', value: p.login, module: m.key, status: 'rejected'})-[:TARGETS]->(p)
CREATE (run)-[:PROPOSED]->(:Action:Node {key: $label, kind: 'label', value: 'bug', status: 'proposed'})
CREATE (f:Feedback:Node {key: $feedback, verdict: 'reject', reason: 'fixture', given_by: 'fixture', at: $at})-[:ON]->(a1)
CREATE (r:Rule:Node {key: $rule, kind: 'avoid', scope: m.key, text: 'fixture', active: false, created_at: $at})-[:DERIVED_FROM]->(f)
CREATE (r)-[:APPLIES_TO]->(p)
