// Task 2: the latest eligible AgentRun for the exact incoming issue $issue: one that carries
// webhook-origin lineage (notify_root), ordered by the recorded notify_at then run key. Same row
// shape as recall_run.cypher, plus the issue title and the issue's actual REPLAY_OF key.
MATCH (run:AgentRun)-[:HANDLED]->(i:Issue {key: $issue})
WHERE i.incoming = true AND run.notify_root IS NOT NULL
  AND run.notify_at IS NOT NULL AND run.at IS NOT NULL AND run.triage IS NOT NULL
OPTIONAL MATCH (i)-[:REPLAY_OF]->(up:Issue)
WITH run, i, up ORDER BY run.notify_at DESC, run.key DESC LIMIT 1
OPTIONAL MATCH (run)-[:USED_EVIDENCE]->(e)
WITH run, i, up, collect(DISTINCT e.key) AS evidence
OPTIONAL MATCH (run)-[:PROPOSED]->(a:Action)
OPTIONAL MATCH (a)<-[:ON]-(f:Feedback)
OPTIONAL MATCH (rule:Rule)-[:DERIVED_FROM]->(f)
WITH run, i, up, evidence, a, f, rule ORDER BY f.at, f.key
WITH run, i, up, evidence, a, collect(CASE WHEN f IS NULL THEN NULL ELSE
       {feedback_key: f.key, action_key: a.key, verdict: f.verdict, rule_key: rule.key} END) AS feedback
ORDER BY a.key
WITH run, i, up, evidence, collect(CASE WHEN a IS NULL THEN NULL ELSE
       {action_key: a.key, kind: a.kind, value: a.value, module: a.module, status: a.status, note: a.note,
        feedback: feedback} END) AS actions
RETURN run.key AS run_key, run.at AS at, run.issue_key AS issue_key, run.summary AS summary,
       run.fallback AS fallback, run.model AS model, run.llm_calls AS llm_calls, run.ms AS ms,
       run.triage AS triage, evidence, actions, i.title AS title, up.key AS replay_of
