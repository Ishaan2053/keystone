// Run by ingest/load.py after every load, so a re-load never leaves edges the new data rules out:
// a PR that became a sweep (e.g. max_pr_files lowered) loses its TOUCHES, and a PR no longer
// open loses its REVIEW_REQUESTED (that edge is current review load).
MATCH (:PR {sweep: true})-[t:TOUCHES]->() DELETE t;
MATCH (pr:PR)-[r:REVIEW_REQUESTED]->() WHERE pr.state <> 'open' DELETE r;
