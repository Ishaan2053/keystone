// The canonical stored Person key for a GitHub login, for a why reply. GitHub logins are
// case-insensitive while a Person's key preserves the graph's stored casing, so the lookup
// matches case-insensitively before the exact-key explain_path query. Unknown login: no row.
MATCH (p:Person:Node)
WHERE p.login IS NOT NULL AND toLower(p.login) = toLower($login)
RETURN p.key AS key
LIMIT 1
