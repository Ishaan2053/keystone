// The 5 doc Chunks nearest to $q (score is a cosine distance, lower = closer), with their Doc.
CALL db.idx.vector.queryNodes('Chunk', 'embedding', 5, vecf32($q)) YIELD node AS c, score
MATCH (d:Doc)-[:HAS_CHUNK]->(c)
RETURN c.key AS chunk, c.text AS text, d.key AS doc, d.title AS title, d.url AS url, score
ORDER BY score ASC
