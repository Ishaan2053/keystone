// The 5 Decisions nearest to $q (score is a cosine distance, lower = closer), with the PRs, Issues
// or Docs they were made in, the modules they are about, and the teams that own those modules.
CALL db.idx.vector.queryNodes('Decision', 'embedding', 5, vecf32($q)) YIELD node AS d, score
MATCH (d)-[:MADE_IN]->(src)
OPTIONAL MATCH (d)-[:ABOUT]->(m:Module)
OPTIONAL MATCH (m)<-[:OWNS]-(t:Team)
WITH d, score, collect(DISTINCT {key: src.key, url: src.url}) AS sources,
     collect(DISTINCT m.key) AS modules, collect(DISTINCT t.name) AS teams
RETURN d.key AS decision, d.summary AS summary, d.rationale AS rationale, d.quote AS quote,
       sources, modules, teams, score
ORDER BY score ASC
