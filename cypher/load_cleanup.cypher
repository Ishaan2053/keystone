// Run by ingest/load.py after the writes: a PR that became a sweep (e.g. max_pr_files lowered) loses its TOUCHES.
// Re-loading the same cached snapshot is idempotent; after `ingest.fetch --refresh`, delete the tenant graph and
// load again (other loader edges are only added, never removed).
MATCH (:PR {sweep: true})-[t:TOUCHES]->() DELETE t;
