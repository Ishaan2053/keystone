// Task 2: a proven eligible parent for a UI feedback re-triage. $run must HANDLE the exact
// incoming issue $issue, carry webhook-origin lineage (notify_root), and have PROPOSED the Action
// that the exact persisted UI Feedback $feedback (given_by='ui') is ON. Returns the parent's issue
// title and replay key, or no row when any check fails. The feedback reference is not authority.
MATCH (parent:AgentRun {key: $run})-[:HANDLED]->(i:Issue {key: $issue})
WHERE i.incoming = true AND parent.notify_root IS NOT NULL
MATCH (parent)-[:PROPOSED]->(:Action)<-[:ON]-(f:Feedback {key: $feedback})
WHERE f.given_by = 'ui'
OPTIONAL MATCH (i)-[:REPLAY_OF]->(up:Issue)
RETURN i.title AS title, up.key AS replay_of
