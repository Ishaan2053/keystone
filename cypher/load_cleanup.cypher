// Run by ingest/load.py after the writes: a PR that became a sweep (e.g. max_pr_files lowered) loses its TOUCHES.
// Other loader edges are only added, never removed. After `ingest.fetch --refresh`, re-run load (then A3 and A4);
// never drop the tenant graph, it holds agent memory.
MATCH (:PR {sweep: true})-[t:TOUCHES]->() DELETE t;
