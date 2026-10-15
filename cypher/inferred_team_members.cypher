// Each inferred Team with its members, highest pagerank first; printed by ingest/load.py.
MATCH (p:Person)-[:MEMBER_OF]->(t:Team {inferred: true})
WITH t, p ORDER BY p.pagerank DESC, p.login
RETURN t.key AS team, t.name AS name, collect(p.login) AS members
ORDER BY size(members) DESC, team
