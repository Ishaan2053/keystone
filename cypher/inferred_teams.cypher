// Load step 6 (spec §4): rebuild inferred Teams from Person.community (communities of 2 or more people).
// Runs whether or not CODEOWNERS teams exist; those (inferred: false) are left alone.
// Keys are team:inferred/<n>, n = 1, 2, ... by size (largest first), ties by smallest login, so they are
// stable across reloads and never carry the internal communityId. Inferred teams own nothing.
MATCH (t:Team {inferred: true}) DETACH DELETE t;

MATCH (p:Person) WHERE p.community IS NOT NULL
WITH p.community AS c, p ORDER BY p.login
WITH c, collect(p) AS members
WHERE size(members) >= 2
WITH members ORDER BY size(members) DESC, members[0].login
WITH collect(members) AS groups
UNWIND range(0, size(groups) - 1) AS i
WITH i + 1 AS n, groups[i] AS members
MERGE (t:Team:Node {key: 'team:inferred/' + toString(n)})
SET t.name = 'Inferred team ' + toString(n), t.inferred = true
WITH t, members
UNWIND members AS p
MERGE (p)-[:MEMBER_OF]->(t);
