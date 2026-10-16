// The issue an agent run triages, by key: its text, its author, and the upstream Issue it replays
// (REPLAY_OF) with that issue's author. The agent excludes both issues from affected_modules and both
// authors from rank_reviewers, so a replay never finds its own original (spec section 5).
// An unknown $issue returns no row.
MATCH (i:Issue {key: $issue})
OPTIONAL MATCH (i)<-[:AUTHORED]-(a:Person)
OPTIONAL MATCH (i)-[:REPLAY_OF]->(o:Issue)
OPTIONAL MATCH (o)<-[:AUTHORED]-(oa:Person)
RETURN i.key AS issue, i.title AS title, coalesce(i.body, '') AS body, i.number AS number,
       coalesce(i.incoming, false) AS incoming, i.created_at AS created_at, a.login AS author,
       o.key AS replay_of, oa.login AS replay_author
LIMIT 1
