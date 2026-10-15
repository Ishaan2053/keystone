// The $limit people with the highest pagerank, with betweenness; printed by ingest/load.py.
MATCH (p:Person) WHERE p.pagerank IS NOT NULL
RETURN p.login AS login, p.pagerank AS pagerank, p.betweenness AS betweenness
ORDER BY pagerank DESC, login
LIMIT $limit
