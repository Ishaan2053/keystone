// Load step 6 (spec §4): rebuild inferred Teams from Person.community (communities of 2 or more people).
// Runs whether or not CODEOWNERS teams exist; those (inferred: false) are left alone.
// Keys are team:inferred/<n>, n = 1, 2, ... by size (largest first), ties by smallest login, so they are
// stable across reloads and never carry the internal communityId. Inferred teams own nothing.
// Teams are reused, not dropped, so edges into them (agent memory) survive: only MEMBER_OF is rebuilt, and
// teams left with no members are deleted. Last, Person.community becomes the inferred team key (or null).
MATCH (:Person)-[m:MEMBER_OF]->(:Team {inferred: true}) DELETE m;

MATCH (p:Person) WHERE p.community IS NOT NULL
WITH p.community AS c, collect(p) AS members, min(p.login) AS first
WHERE size(members) >= 2
WITH members, first ORDER BY size(members) DESC, first
WITH collect(members) AS groups
UNWIND range(0, size(groups) - 1) AS i
WITH i + 1 AS n, groups[i] AS members
MERGE (t:Team:Node {key: 'team:inferred/' + toString(n)})
SET t.name = 'Inferred team ' + toString(n), t.inferred = true
WITH t, members
UNWIND members AS p
MERGE (p)-[:MEMBER_OF]->(t);

MATCH (t:Team {inferred: true}) WHERE NOT ()-[:MEMBER_OF]->(t) DETACH DELETE t;

MATCH (p:Person)
OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team {inferred: true})
SET p.community = t.key;
