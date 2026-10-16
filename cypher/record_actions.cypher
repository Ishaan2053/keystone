// After apply_triage: one row per action result, {key, status, note}, written onto the Action that
// AgentRun $run PROPOSED (spec section 6). Returns how many were updated; the store raises when that
// is fewer than the results, so an action key from another run is never recorded.
UNWIND $results AS res
MATCH (:AgentRun {key: $run})-[:PROPOSED]->(a:Action {key: res.key})
SET a.status = res.status, a.note = res.note
RETURN count(a) AS updated
