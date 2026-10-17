// Every index the product uses (spec section 3). Run by ingest/load.py before loading;
// re-creating an existing index fails with "already indexed", which the loader ignores.

// `key` on Node, for lookups where the kind is unknown, and on every kind.
CREATE INDEX FOR (n:Node) ON (n.key);
CREATE INDEX FOR (n:Person) ON (n.key);
CREATE INDEX FOR (n:Team) ON (n.key);
CREATE INDEX FOR (n:Repo) ON (n.key);
CREATE INDEX FOR (n:Module) ON (n.key);
CREATE INDEX FOR (n:File) ON (n.key);
CREATE INDEX FOR (n:Issue) ON (n.key);
CREATE INDEX FOR (n:PR) ON (n.key);
CREATE INDEX FOR (n:Comment) ON (n.key);
CREATE INDEX FOR (n:Label) ON (n.key);
CREATE INDEX FOR (n:Doc) ON (n.key);
CREATE INDEX FOR (n:Chunk) ON (n.key);
CREATE INDEX FOR (n:Decision) ON (n.key);
CREATE INDEX FOR (n:AgentRun) ON (n.key);
CREATE INDEX FOR (n:Action) ON (n.key);
CREATE INDEX FOR (n:Feedback) ON (n.key);
CREATE INDEX FOR (n:Rule) ON (n.key);

CREATE INDEX FOR (p:Person) ON (p.login);
CREATE INDEX FOR (pr:PR) ON (pr.merged_at);
CREATE INDEX FOR (i:Issue) ON (i.number);

// Embeddings are 384-d (all-MiniLM-L6-v2), stored with vecf32().
CREATE VECTOR INDEX FOR (i:Issue) ON (i.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'};
CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'};
CREATE VECTOR INDEX FOR (d:Decision) ON (d.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'};

CALL db.idx.fulltext.createNodeIndex('Issue', 'title', 'body');
