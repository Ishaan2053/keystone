// What apply_triage needs for one AgentRun (api.actions.ApplyContext): the Issue it handled with
// that issue's own number and `incoming` flag, the stored TriageResult (for its comment_markdown),
// and its proposed Actions with their current status and note. An unknown $run returns no row.
// recall_run.cypher returns the actions with their feedback but not the issue's number or flag.
MATCH (run:AgentRun {key: $run})
OPTIONAL MATCH (run)-[:HANDLED]->(i:Issue)
OPTIONAL MATCH (run)-[:PROPOSED]->(a:Action)
WITH run, i, a ORDER BY a.key
RETURN run.key AS run_key, i.key AS issue_key, i.number AS number, coalesce(i.incoming, false) AS incoming,
       run.triage AS triage,
       collect(CASE WHEN a IS NULL THEN NULL ELSE
               {action_key: a.key, kind: a.kind, value: a.value, module: a.module, status: a.status,
                note: a.note, feedback: []} END) AS actions
