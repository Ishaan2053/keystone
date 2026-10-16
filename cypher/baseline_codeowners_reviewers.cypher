// Proof, CODEOWNERS baseline, reviewers (spec section 14): up to 3 members of the Teams that OWN
// $module (the module the fix touched most). When no owning Team has known members, the 3 people
// who reviewed the most PRs in the repo ($prs is its PR key prefix) merged before $before.
// `source` says which was used; `via` lists each person's PRs newest first, for the leak check.
OPTIONAL MATCH (:Module {key: $module})<-[:OWNS]-(:Team)<-[:MEMBER_OF]-(o:Person)
WHERE NOT o.login IN $exclude
WITH DISTINCT o ORDER BY o.login
WITH collect(o.login)[..3] AS owners
OPTIONAL MATCH (p:Person)-[:REVIEWED]->(pr:PR)
WHERE size(owners) = 0 AND pr.key STARTS WITH $prs AND pr.merged_at < $before AND NOT p.login IN $exclude
WITH owners, p, pr ORDER BY pr.merged_at DESC
WITH owners, p, count(DISTINCT pr) AS n, collect(DISTINCT pr.key)[..3] AS via
ORDER BY n DESC, p.login
WITH owners, collect(CASE WHEN p IS NULL THEN NULL ELSE {login: p.login, key: p.key, n: n, via: via} END)[..3] AS top
RETURN CASE WHEN size(owners) > 0 THEN 'owners' ELSE 'repo_top_reviewers' END AS source,
       CASE WHEN size(owners) > 0 THEN [l IN owners | {login: l, key: 'person:' + l, n: null, via: []}] ELSE top END AS reviewers
