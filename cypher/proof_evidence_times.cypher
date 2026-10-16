// Leak check for the Proof: the evidence keys a method returned that are dated at or after $before
// (a PR by merged_at, anything else by created_at). Undated nodes (Person, Module, Label) pass.
// proof.py asserts this returns no rows for every Issue and method.
UNWIND $keys AS k
MATCH (n:Node {key: k})
WITH n, coalesce(n.merged_at, n.created_at) AS at
WHERE at >= $before
RETURN n.key AS key, at
ORDER BY at DESC
