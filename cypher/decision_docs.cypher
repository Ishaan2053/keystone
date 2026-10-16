// Decisions job inputs (spec section 4): every Doc whose path contains one of $words (lower case),
// with its Chunks in order and the module it describes, if any.
MATCH (d:Doc)-[:HAS_CHUNK]->(c:Chunk)
WHERE any(w IN $words WHERE toLower(d.path) CONTAINS w)
WITH d, c ORDER BY toInteger(last(split(c.key, '#')))
WITH d, collect(c.text) AS chunks
OPTIONAL MATCH (d)-[:DESCRIBES]->(m:Module)
RETURN d.key AS key, d.url AS url, d.path AS path, d.title AS title, collect(m.path) AS modules, chunks
ORDER BY key
