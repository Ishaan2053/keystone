// Issues with number $number, for POST /tenants/{t}/triage {issue_number}: a tenant has several
// repos, so a number can match more than one. Incoming (sandbox) issues come first.
MATCH (i:Issue {number: $number})
RETURN i.key AS issue, coalesce(i.incoming, false) AS incoming
ORDER BY incoming DESC, issue
