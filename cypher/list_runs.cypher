// The newest $limit AgentRuns, newest first, for GET /tenants/{t}/runs (the UI polls it).
// Sorts on the node before projecting, like latest_run.cypher.
MATCH (run:AgentRun)
WHERE run.at IS NOT NULL
WITH run ORDER BY run.at DESC, run.key DESC LIMIT $limit
RETURN run.key AS run_key, run.at AS at, run.issue_key AS issue_key, run.summary AS summary,
       run.fallback AS fallback, run.model AS model, run.llm_calls AS llm_calls, run.ms AS ms
