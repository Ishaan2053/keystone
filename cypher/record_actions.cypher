// After apply_triage: one row per action result, {key, status, note}. B6 runs this (spec section 6).
UNWIND $results AS res
MATCH (a:Action {key: res.key})
SET a.status = res.status, a.note = res.note
