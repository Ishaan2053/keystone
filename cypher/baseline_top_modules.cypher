// Proof, CODEOWNERS baseline, modules (spec section 14): the 3 non-test modules of $repo changed by
// the most PRs merged before $before. `newest` is the latest such PR, for the leak check.
MATCH (r:Repo {key: $repo})<-[:IN]-(m:Module)<-[:IN]-(:File)<-[:TOUCHES]-(pr:PR)
WHERE pr.merged_at < $before AND NOT m.path IN ['tests', 'test', 'e2e']
WITH m, pr ORDER BY pr.merged_at DESC
WITH m, count(DISTINCT pr) AS changes, collect(DISTINCT pr.key)[..1] AS newest
RETURN m.key AS module, changes, newest
ORDER BY changes DESC, module LIMIT 3
