// The newest AgentRun's key, for ingest/bench.py to time recall_run on a real run. No row when the
// tenant has no runs yet.
MATCH (run:AgentRun)
RETURN run.key AS run
ORDER BY run.at DESC, run.key DESC
LIMIT 1
