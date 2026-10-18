// Task 2: record webhook-origin lineage on an existing AgentRun. $run must already HANDLE the
// exact incoming sandbox issue $issue. A parentless issue event ($parent null) roots the lineage
// at its own key; every other write inherits the exact proven parent's root and requires that
// parent to HANDLE the same issue and already carry lineage. The write happens once: a later write
// never changes the recorded event, parent or time, so a conflicting reparent returns the stored
// (different) parent for the store to reject. MATCH-only + SET: no node is ever fabricated.
MATCH (run:AgentRun {key: $run})-[:HANDLED]->(i:Issue)
WHERE i.incoming = true AND i.key = $issue
OPTIONAL MATCH (parent:AgentRun {key: $parent})-[:HANDLED]->(i)
WHERE $parent IS NOT NULL
WITH run, i, parent
WHERE run.notify_root IS NOT NULL
   OR $parent IS NULL
   OR (parent IS NOT NULL AND parent.notify_root IS NOT NULL)
FOREACH (_ IN CASE WHEN run.notify_root IS NULL THEN [1] ELSE [] END |
  SET run.notify_root = CASE WHEN $parent IS NULL THEN run.key ELSE parent.notify_root END,
      run.notify_parent = $parent,
      run.notify_event = $event,
      run.notify_cause = $cause,
      run.notify_at = $now
)
RETURN run.notify_root AS root, run.notify_parent AS parent, run.notify_event AS event,
       run.notify_cause AS cause, run.notify_at AS at
