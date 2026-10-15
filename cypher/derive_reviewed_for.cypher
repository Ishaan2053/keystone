// Load step 4 (spec §4): reviewer -> author, n = PRs reviewed. REVIEWED already excludes bots and self-reviews.
// Safe to re-run: MERGE plus SET n, and the loader never removes REVIEWED edges.
MATCH (r:Person)-[:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person) WHERE r <> a
WITH r, a, count(DISTINCT pr) AS n
MERGE (r)-[x:REVIEWED_FOR]->(a) SET x.n = n
