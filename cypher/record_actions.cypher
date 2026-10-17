// After apply_triage: one row per action result, {key, status, note}, written onto the Action that
// AgentRun $run PROPOSED (spec section 6). All or nothing: when any of the distinct $keys is not one
// of the run's Actions, nothing is written. Returns how many were updated, so the store can raise.
OPTIONAL MATCH (:AgentRun {key: $run})-[:PROPOSED]->(a:Action)
WHERE a.key IN $keys
WITH collect(a) AS acts
UNWIND (CASE WHEN size(acts) = size($keys) THEN $results ELSE [] END) AS res
UNWIND [x IN acts WHERE x.key = res.key] AS a
SET a.status = res.status, a.note = res.note
RETURN count(a) AS updated
