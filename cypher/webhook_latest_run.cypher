// The latest AgentRun that HANDLED the exact incoming issue $issue, by `at` then run key: its
// summary fields, the stored TriageResult (JSON), every key it cited, and its proposed Actions,
// each with the Feedback on it and the Rule each Feedback created (if any). Same row shape as
// recall_run.cypher, so both reuse one RecallRunData mapping. An issue with no run returns no row.
// A run with no `at` (which would sort first DESC) or no stored TriageResult is skipped, so it never
// hides an older complete run (like latest_run.cypher).
MATCH (run:AgentRun)-[:HANDLED]->(i:Issue {key: $issue})
WHERE run.at IS NOT NULL AND run.triage IS NOT NULL
WITH run ORDER BY run.at DESC, run.key DESC LIMIT 1
OPTIONAL MATCH (run)-[:USED_EVIDENCE]->(e)
WITH run, collect(DISTINCT e.key) AS evidence
OPTIONAL MATCH (run)-[:PROPOSED]->(a:Action)
OPTIONAL MATCH (a)<-[:ON]-(f:Feedback)
OPTIONAL MATCH (rule:Rule)-[:DERIVED_FROM]->(f)
WITH run, evidence, a, f, rule ORDER BY f.at, f.key
WITH run, evidence, a, collect(CASE WHEN f IS NULL THEN NULL ELSE
       {feedback_key: f.key, action_key: a.key, verdict: f.verdict, rule_key: rule.key} END) AS feedback
ORDER BY a.key
WITH run, evidence, collect(CASE WHEN a IS NULL THEN NULL ELSE
       {action_key: a.key, kind: a.kind, value: a.value, module: a.module, status: a.status, note: a.note,
        feedback: feedback} END) AS actions
RETURN run.key AS run_key, run.at AS at, run.issue_key AS issue_key, run.summary AS summary,
       run.fallback AS fallback, run.model AS model, run.llm_calls AS llm_calls, run.ms AS ms,
       run.triage AS triage, evidence, actions
