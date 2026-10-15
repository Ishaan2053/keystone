// Run by ingest/load.py after the writes: a Chunk that is no longer produced (its doc got shorter or was removed)
// goes, so vector search never returns stale text. $keys: every Chunk key in this load.
MATCH (c:Chunk)
WHERE NOT c.key IN $keys
DETACH DELETE c
