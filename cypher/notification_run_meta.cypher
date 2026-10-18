// Task 2: the issue title and the issue's actual REPLAY_OF key for one issue run $run, plus the
// run's recorded lineage root (null until mark_run has run). The exact-issue check is skipped when
// $issue is null. A raw/adhoc run has no HANDLED edge and therefore no row.
MATCH (run:AgentRun {key: $run})-[:HANDLED]->(i:Issue)
WHERE i.incoming = true AND ($issue IS NULL OR i.key = $issue)
OPTIONAL MATCH (i)-[:REPLAY_OF]->(up:Issue)
RETURN i.title AS title, up.key AS replay_of, run.notify_root AS notify_root
