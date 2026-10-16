// The newest timestamped AgentRun's key, for ingest/bench.py to time recall_run on a real run. A run
// with no `at` is skipped. No row when the tenant has no timestamped runs yet.
MATCH (run:AgentRun)
WHERE run.at IS NOT NULL
RETURN run.key AS run
ORDER BY run.at DESC, run.key DESC
LIMIT 1
