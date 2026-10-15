// Store embeddings (spec §3): vecf32() is required, a plain list is not picked up by the vector index.
// $rows: [{key, vec}] with 384-d vectors from api/embeddings.py.
UNWIND $rows AS r
MATCH (n:Node {key: r.key})
SET n.embedding = vecf32(r.vec)
