// The $k Issues nearest to the 384-d vector $q. On FalkorDB v4.20.4, `score` is a cosine distance
// (0 = same direction, 1 = unrelated, 2 = opposite), so nearest first is ORDER BY score ASC.
CALL db.idx.vector.queryNodes('Issue', 'embedding', $k, vecf32($q)) YIELD node AS i, score
RETURN i.key AS issue, i.title AS title, score
ORDER BY score ASC
