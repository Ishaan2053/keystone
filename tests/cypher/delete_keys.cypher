// Deletes the recall_run test fixture by key.
MATCH (n:Node) WHERE n.key IN $keys DETACH DELETE n
