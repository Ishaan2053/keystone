// Decisions job output (spec section 4). $rows: [{key, source, summary, rationale, quote, url, vec, modules}].
// vecf32() is required for the Decision vector index; `modules` are Module keys that already exist.
UNWIND $rows AS r
MATCH (src:Node {key: r.source})
MERGE (d:Decision:Node {key: r.key})
SET d.summary = r.summary, d.rationale = r.rationale, d.quote = r.quote, d.url = r.url,
    d.embedding = vecf32(r.vec)
MERGE (d)-[:MADE_IN]->(src)
WITH d, r
UNWIND r.modules AS mk
MATCH (m:Module {key: mk})
MERGE (d)-[:ABOUT]->(m)
