// B7 (spec section 13): upsert one incoming sandbox Issue from a validated webhook job.
// $key/$number/$title/$body/$state/$url/$created_at/$incoming and the 384-d $vec.
// $author_login/$author_key/$author_url are null for a bot author (no Person/AUTHORED,
// consistent with the loader). $replay_key is a canonical upstream issue key that must
// already be loaded in this tenant, or null: only the REPLAY_OF edge is written, never a
// fabricated upstream Issue. An edit preserves the original created_at, reconciles
// AUTHORED and drops any obsolete REPLAY_OF before attaching the validated target.
MERGE (i:Issue:Node {key: $key})
ON CREATE SET i.created_at = $created_at
SET i.number = $number, i.title = $title, i.body = $body, i.state = $state,
    i.url = $url, i.incoming = $incoming, i.embedding = vecf32($vec)
WITH i
OPTIONAL MATCH (i)<-[old:AUTHORED]-(:Person)
DELETE old
WITH DISTINCT i
FOREACH (_ IN CASE WHEN $author_login IS NULL THEN [] ELSE [1] END |
  MERGE (p:Person:Node {key: $author_key})
  SET p.login = $author_login, p.name = $author_login, p.url = $author_url
  MERGE (p)-[:AUTHORED]->(i)
)
WITH DISTINCT i
OPTIONAL MATCH (i)-[stale:REPLAY_OF]->(:Issue)
DELETE stale
WITH DISTINCT i
OPTIONAL MATCH (up:Issue {key: $replay_key})
FOREACH (_ IN CASE WHEN up IS NULL THEN [] ELSE [1] END |
  MERGE (i)-[:REPLAY_OF]->(up)
)
RETURN i.key AS key, up IS NOT NULL AS replay_linked
