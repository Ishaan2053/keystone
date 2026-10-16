// The stored URLs of exactly the evidence-path keys $keys in the current tenant, for a why reply.
// Only a node's own stored `url` is returned; a key with no stored URL yields no row, so the reply
// never synthesizes a source URL from untrusted text or from the key itself.
UNWIND $keys AS k
MATCH (n:Node {key: k})
WHERE n.url IS NOT NULL AND n.url <> ''
RETURN n.key AS key, n.url AS url
