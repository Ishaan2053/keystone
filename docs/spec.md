# Keystone spec

The source of truth for what to build. `docs/tasks.md` says in what order. If this spec and the code disagree, raise it before changing either.

## 1. What the demo must show (3 minutes, in this order)

1. **Live:** an issue is opened in the sandbox repo (on camera, from a phone). Within seconds Keystone posts a triage comment on it, with no button pressed. The issue is a replay of a real FalkorDB issue.
2. The run appears in the UI. The trace shows each tool the agent chose from its MCP server, with its Cypher and duration.
3. The triage: labels, two reviewers with reasons, related past decisions and a risk flag, already applied to the sandbox issue.
4. Clicking any claim highlights its evidence path on the graph canvas.
5. A maintainer replies on GitHub with `/keystone reject <login> <reason>`. A Rule node is created. Re-running changes the result, and the trace shows the rule applied.
6. A question ("Why does GraphRAG-SDK do X?") gets an answer that cites decisions, PRs and docs.
7. **Proof:** on held-out history, the graph beats a CODEOWNERS baseline and a similar-issues (vector-only) baseline on reviewers, modules and labels, with real query latencies.
8. **Try it yourself (10 s):** the hosted MCP endpoint connects to any MCP client in one line.

Everything below exists to make those eight moments real. Sections 13 and 14 cover the Live action and Proof additions.

## 2. Tenants

One FalkorDB graph per org, named `tenant_<slug>`. Each tenant has a config file:

```yaml
# ingest/tenants/falkordb.yml
slug: falkordb
org: FalkorDB
repos:            # fill in during prep: repos with real PR and review activity
  - FalkorDB/FalkorDB
  - FalkorDB/GraphRAG-SDK
since_days: 365   # history window
docs_globs: ["README.md", "docs/**/*.md", "*.md"]
max_pr_files: 300 # a PR touching more files is a sweep (section 4)
```

Tenant 2 is optional (see tasks). Nothing in the code may assume a single tenant.

## 3. Graph model

Every node has two labels: its kind and `Node`. Every node has a unique `key`. Timestamps are integer epoch seconds.

| Label | Key format | Properties |
| --- | --- | --- |
| Person | `person:<login>` | login, name, url, pagerank, betweenness, community |
| Team | `team:<org>/<slug>`, `team:user/<login>` (a one-person CODEOWNERS owner) or `team:inferred/<n>` | name, inferred (bool) |
| Repo | `repo:<owner>/<name>` | name, url |
| Module | `module:<owner>/<name>/<path>` | path, repo |
| File | `file:<owner>/<name>/<path>` | path, language |
| Issue | `issue:<owner>/<name>#<n>` | number, title, body, state, created_at, closed_at, url, embedding, incoming (true for issues in `SANDBOX_REPO`) |
| PR | `pr:<owner>/<name>#<n>` | number, title, body, state ('open', 'merged', 'closed'), created_at, merged_at, url, comment_count, sweep (bool) |
| Comment | `comment:<github id>` | body, created_at, url |
| Label | `label:<owner>/<name>/<label>` | name, color |
| Doc | `doc:<owner>/<name>/<path>` | path, title, url |
| Chunk | `chunk:<doc key>#<i>` | text, embedding |
| Decision | `decision:<sha1 of summary + source key>` | summary, rationale, quote, url, embedding |
| AgentRun | `run:<uuid>` | at, issue_key, model, llm_calls, ms, summary, fallback (bool) |
| Action | `action:<uuid>` | kind ('label', 'assign', 'comment'), value, module (module key, for assign), status ('proposed', 'applied', 'dry_run', 'refused', 'rejected'), note |
| Feedback | `feedback:<uuid>` | verdict ('accept', 'reject'), reason, given_by, at |
| Rule | `rule:<uuid>` | kind ('avoid'), scope (module key or null), text, active, created_at |

| Relationship | From → To | Notes |
| --- | --- | --- |
| MEMBER_OF | Person → Team | |
| OWNS | Team → Module | from CODEOWNERS; inferred teams own nothing |
| IN | File → Module, Module → Repo | |
| AUTHORED | Person → PR, Person → Issue | |
| REVIEWED | Person → PR | one edge per (person, PR); `state` = their latest review: 'APPROVED', 'CHANGES_REQUESTED', 'COMMENTED' |
| REVIEW_REQUESTED | PR → Person | open PRs only; this is "load" |
| TOUCHES | PR → File | none for sweeps (section 4) |
| FIXES | PR → Issue | parsed from PR body: fixes / closes / resolves #n |
| REPLAY_OF | Issue → Issue | incoming sandbox issue → the upstream issue it replays (from the "Replayed from" link) |
| HAS_LABEL | Issue → Label, PR → Label | |
| ON | Comment → Issue, Comment → PR, Feedback → Action | |
| WROTE | Person → Comment | |
| DESCRIBES | Doc → Module | when the doc's directory matches a module path |
| HAS_CHUNK | Doc → Chunk | |
| MADE_IN | Decision → PR, Decision → Issue, Decision → Doc | |
| ABOUT | Decision → Module | |
| REVIEWED_FOR | Person → Person | derived: reviewer → author, `n` = count |
| HANDLED | AgentRun → Issue | |
| USED_EVIDENCE | AgentRun → any Node | every key the run cited |
| PROPOSED | AgentRun → Action | |
| TARGETS | Action → Person, Action → Label | |
| DERIVED_FROM | Rule → Feedback | |
| APPLIES_TO | Rule → Person | |

**Module rule:** a file's module is its first path segment. If that segment is `src`, `lib`, `packages` or `pkg`, use the first two segments. Root-level files belong to module `<repo>/(root)`.

**Embeddings** are stored with `SET n.embedding = vecf32($vec)`. A plain list is not picked up by the vector index.

**Indexes** (`cypher/indexes.cypher`). A `key` index on every kind, so `(m:Module {key: $k})` uses an index, plus one on `Node` for lookups where the kind is unknown:

```cypher
CREATE INDEX FOR (n:Node) ON (n.key)
// ...and the same for each label in the table above: Person, Team, Repo, Module, File, Issue, PR,
// Comment, Label, Doc, Chunk, Decision, AgentRun, Action, Feedback, Rule
CREATE INDEX FOR (p:Person) ON (p.login)
CREATE INDEX FOR (pr:PR) ON (pr.merged_at)
CREATE VECTOR INDEX FOR (i:Issue) ON (i.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'}
CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'}
CREATE VECTOR INDEX FOR (d:Decision) ON (d.embedding) OPTIONS {dimension: 384, similarityFunction: 'cosine'}
CALL db.idx.fulltext.createNodeIndex('Issue', 'title', 'body')
```

## 4. Ingest

**Fetch** (`ingest/fetch.py`): GitHub REST with `GITHUB_TOKEN`. Write every response to `data/raw/<tenant>/` and skip any request already cached. Per repo:

- PRs (all states, created within `since_days`), and for each PR: files, reviews, and requested reviewers (open PRs only, from the list payload)
- Issues (all states, updated within `since_days`; drop items that are PRs), and issue and PR comments
- Labels
- `CODEOWNERS` from `.github/`, the root or `docs/` (may not exist)
- Markdown docs matching `docs_globs`, via the git trees API

Fetch nothing about users beyond the login, name and profile URL already embedded in these responses. Never read commit emails. Note that GitHub returns at most 3,000 files per PR; the sweep rule below covers those PRs.

**Load** (`ingest/load.py`): MERGE by `key`, batched with `UNWIND $rows`. `Person.name` is the login: these responses carry no display name, and nothing fetches profiles. `PR.comment_count` counts the PR's issue comments plus review comments. Bots (GitHub user type `Bot`: dependabot, Copilot, coderabbitai and others) are not people: no Person, AUTHORED, REVIEWED, REVIEW_REQUESTED or WROTE for them, and their comments are neither loaded nor counted, though their PRs load. About half of all reviews come from review bots, which would otherwise top every ranking. `REVIEWED` skips an author's reviews of their own PR (their replies in review threads), and its `state` is the person's latest APPROVED, CHANGES_REQUESTED or COMMENTED review. Then:

1. Parse `FIXES` from PR bodies.
2. Mark sweeps. A PR is a sweep (`sweep: true`) when it touches more than `max_pr_files` files, or when its head is a branch of the same repo that 3 or more PRs in the window use as their base (release merges such as `staging` into `main`). A sweep keeps every edge except `TOUCHES`. Dependency bumps, branch merges and release merges would otherwise make their author an expert on every module. In the FalkorDB tenant this marks 33 PRs in FalkorDB (88% of its file rows), 1 in GraphRAG-SDK and 57 in falkordb-browser.
3. Parse CODEOWNERS into Team-OWNS-Module. A rule owns a module only when its pattern is a directory that is the module's path or contains it (`/src/` owns every `src/*` module); file and wildcard patterns own nothing. Individual owners (`@user`) become a one-person Team named after them. Org teams (`@org/team`) get no `MEMBER_OF`, since membership isn't public.
4. Derive `REVIEWED_FOR` (`cypher/derive_reviewed_for.cypher`).
5. Store algorithm scores on Person: `algo.pageRank('Person', 'REVIEWED_FOR')` into `pagerank`; `algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['REVIEWED_FOR'], samplingSeed: 42})` into `betweenness`; `algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['REVIEWED_FOR']})` into `community`.
6. Create inferred Teams from `community` (communities of 2 or more people), with `inferred: true`, whether or not the tenant has Teams from CODEOWNERS. A CODEOWNERS file may cover only CI paths, as FalkorDB's does (`/build/`, `/.github/`).
7. Embed Issue text (title + first 1,000 characters of body) and Doc chunks (about 800 characters, split on headings). Decisions are embedded by the decisions job, which runs after load.
8. Print node and edge counts per label and type, and the number of sweeps. The README quotes them.

**Decisions** (`ingest/decisions.py`, run once, cached): pick the 150 merged PRs with the most comments, plus every Doc whose path contains `adr`, `rfc`, `design` or `decision`. For each, one LLM call returns:

```json
{"decisions": [{"summary": "one sentence", "rationale": "one sentence", "quote": "exact text from the source", "about_paths": ["src/serializers"]}]}
```

Keep a decision only if `quote` appears verbatim in the source text. This is the guard against invented decisions. Map `about_paths` to Module keys; drop paths that don't match a module. Write each Decision with `MADE_IN`, `ABOUT` and its embedding (`vecf32`).

## 5. Tool contract

Tools live in `api/tools/`, are registered on the MCP server (`api/mcp_server.py`, `fastmcp`), and each runs one or two named queries from `cypher/`. Every tool returns:

```python
class ToolResult(BaseModel):
    tool: str
    data: Any                 # the answer
    cypher: list[str]         # query text actually run
    params: dict              # parameters, embeddings replaced by "<384-d vector>"
    evidence: list[str]       # node keys that support `data`
    ms: int
    empty: bool               # true when the query found nothing; the agent must say so
```

| Tool | Input | Output `data` | Query file |
| --- | --- | --- | --- |
| `affected_modules` | tenant, title, body, exclude_issues, before? | top 3 modules with evidence count, similar past issues, and labels used on them | `affected_modules.cypher` |
| `rank_reviewers` | tenant, module_key, k=5, before?, exclude_logins | people with expertise, load, score and any rules applied | `rank_reviewers.cypher` |
| `bus_factor` | tenant, module_key, since? | total PRs, top author and share, active author count | `bus_factor.cypher` |
| `find_decisions` | tenant, query | up to 5 decisions and 5 doc chunks with source URLs | `find_decisions.cypher`, `find_chunks.cypher` |
| `explain_path` | tenant, from_key, to_key | the shortest path as keys and relationship types | `explain_path.cypher` |
| `recall_run` | tenant, run_key | the run, its evidence, actions and feedback | `recall_run.cypher` |
| `apply_triage` (action) | tenant, run_key, dry_run=true | per action: applied, dry_run or refused | none (GitHub API), then `record_actions.cypher` |

**Inputs to query parameters.** `module_key` → `$module`, `exclude_logins` → `$exclude`, `run_key` → `$run`, `from_key` / `to_key` → `$from` / `$to`, `k` → `$k`, `exclude_issues` → `$exclude_issues` (the issue itself and its `REPLAY_OF` target, so a replay never finds its own original), `before` → `$before`. Title and body, or the `query`, are embedded locally into `$q`. The tool computes `$recent` = `before` minus 180 days.

**Defaults.** `before` = now; `since` = now minus 365 days; `exclude_logins` = `[the issue's author]`; `exclude_issues` = `[]`. Lists are never null (`IN null` drops every row). All times are epoch seconds. The backtest passes `before` = the held-out issue's `created_at` to every tool, so no tool can see the future.

**Every other query lives in `cypher/` too**, by these names: `algo_scores.cypher`, `inferred_teams.cypher`, `record_run.cypher`, `record_actions.cypher`, `record_feedback.cypher`, `subgraph.cypher`, `stats.cypher`, `backtest_sample.cypher`.

Tool descriptions on the MCP server matter: they're what the LLM chooses from. Write each as one sentence that says when to use it.

## 6. Cypher starting points

Untested. Run each in FalkorDB Browser on Thursday and fix it there first.

```cypher
// affected_modules.cypher
CALL db.idx.vector.queryNodes('Issue', 'embedding', 20, vecf32($q)) YIELD node AS past, score
WITH past, score
WHERE NOT past.key IN $exclude_issues AND NOT coalesce(past.incoming, false) AND past.created_at < $before
MATCH (past)<-[:FIXES]-(pr:PR)-[:TOUCHES]->(:File)-[:IN]->(m:Module)
WHERE pr.merged_at < $before
OPTIONAL MATCH (past)-[:HAS_LABEL]->(l:Label)
RETURN m.key AS module, m.path AS path, count(DISTINCT pr) AS evidence,
       collect(DISTINCT past.key)[..3] AS similar, collect(DISTINCT l.name)[..5] AS labels
ORDER BY evidence DESC LIMIT 3
```

```cypher
// rank_reviewers.cypher: expertise weighted to recent work, divided by open review load; 'avoid' rules zero the score
MATCH (m:Module {key: $module})<-[:IN]-(:File)<-[:TOUCHES]-(pr:PR)
WHERE pr.merged_at IS NOT NULL AND pr.merged_at < $before
MATCH (p:Person)-[:AUTHORED|REVIEWED]->(pr)
WHERE NOT p.login IN $exclude
WITH DISTINCT p, pr
WITH p, sum(CASE WHEN pr.merged_at > $recent THEN 3 ELSE 1 END) AS expertise, collect(pr.key)[..5] AS via
OPTIONAL MATCH (p)<-[:REVIEW_REQUESTED]-(open:PR {state: 'open'})
WITH p, expertise, via, count(open) AS load
OPTIONAL MATCH (r:Rule {active: true})-[:APPLIES_TO]->(p)
WHERE r.scope IS NULL OR r.scope = $module
WITH p, expertise, via, load, collect(r.key) AS rules
RETURN p.key AS person, p.login AS login, expertise, load, via, rules,
       CASE WHEN size(rules) > 0 THEN 0.0 ELSE toFloat(expertise) / (1 + load) END AS score
ORDER BY score DESC, expertise DESC LIMIT $k
```

```cypher
// bus_factor.cypher
MATCH (m:Module {key: $module})
OPTIONAL MATCH (m)<-[:IN]-(:File)<-[:TOUCHES]-(pr:PR)<-[:AUTHORED]-(p:Person)
WHERE pr.merged_at > $since
WITH m, p, count(DISTINCT pr) AS n ORDER BY n DESC
WITH m, collect(CASE WHEN p IS NULL THEN NULL ELSE {key: p.key, login: p.login, n: n} END) AS authors
WITH m, authors, reduce(t = 0, a IN authors | t + a.n) AS total
RETURN m.key AS module, total, authors[0] AS top, size(authors) AS active_authors,
       CASE WHEN total > 0 THEN toFloat(authors[0].n) / total ELSE NULL END AS top_share
// Always returns one row: set ToolResult.empty from total = 0, not from the row count.
```

```cypher
// find_decisions.cypher
CALL db.idx.vector.queryNodes('Decision', 'embedding', 5, vecf32($q)) YIELD node AS d, score
MATCH (d)-[:MADE_IN]->(src)
OPTIONAL MATCH (d)-[:ABOUT]->(m:Module)
OPTIONAL MATCH (m)<-[:OWNS]-(t:Team)
WITH d, score, collect(DISTINCT {key: src.key, url: src.url}) AS sources,
     collect(DISTINCT m.key) AS modules, collect(DISTINCT t.name) AS teams
RETURN d.key AS decision, d.summary AS summary, d.quote AS quote, sources, modules, teams, score
ORDER BY score ASC
// Check on Thursday whether `score` is a distance (lower = closer) or a similarity, and fix the ORDER BY direction.
```

```cypher
// find_chunks.cypher
CALL db.idx.vector.queryNodes('Chunk', 'embedding', 5, vecf32($q)) YIELD node AS c, score
MATCH (d:Doc)-[:HAS_CHUNK]->(c)
RETURN c.key AS chunk, c.text AS text, d.key AS doc, d.url AS url, score
ORDER BY score ASC
```

```cypher
// explain_path.cypher: at most 4 hops, over evidence relationships only, so a path never routes
// through agent memory (USED_EVIDENCE), Labels, or the File-Module-Repo containment chain.
// FalkorDB may accept shortestPath only in WITH/RETURN; this is that form. If it is rejected, use algo.SPpaths.
MATCH (a:Node {key: $from}), (b:Node {key: $to})
WITH shortestPath((a)-[:AUTHORED|REVIEWED|TOUCHES|FIXES|OWNS|MEMBER_OF|MADE_IN|ABOUT|DESCRIBES*..4]-(b)) AS p
RETURN [n IN nodes(p) | n.key] AS keys, [r IN relationships(p) | type(r)] AS rels
```

```cypher
// recall_run.cypher
MATCH (run:AgentRun {key: $run})
OPTIONAL MATCH (run)-[:USED_EVIDENCE]->(e)
WITH run, collect(DISTINCT e.key) AS evidence
OPTIONAL MATCH (run)-[:PROPOSED]->(a:Action)
OPTIONAL MATCH (a)<-[:ON]-(f:Feedback)
WITH run, evidence, collect(DISTINCT CASE WHEN a IS NULL THEN NULL ELSE
       {action: a.key, kind: a.kind, value: a.value, status: a.status, verdict: f.verdict, reason: f.reason} END) AS actions
RETURN run.key AS run, run.at AS at, run.issue_key AS issue, run.summary AS summary, run.fallback AS fallback, evidence, actions
```

```cypher
// derive_reviewed_for.cypher
MATCH (r:Person)-[:REVIEWED]->(pr:PR)<-[:AUTHORED]-(a:Person) WHERE r <> a
WITH r, a, count(DISTINCT pr) AS n
MERGE (r)-[x:REVIEWED_FOR]->(a) SET x.n = n
```

```cypher
// record_actions.cypher: after apply_triage, one row per action result
UNWIND $results AS res
MATCH (a:Action {key: res.key})
SET a.status = res.status, a.note = res.note
```

## 7. Agent loop (`api/agent.py`)

The agent is an MCP client of Keystone's own MCP server, through `fastmcp`'s in-memory client in the API process. Claude Desktop or Cursor can connect to the same server over stdio.

1. **Input:** a tenant, plus an issue number (looked up in the graph) or a raw title and body.
2. **Plan (LLM call 1):** send the issue and the tool list from `list_tools()`. The model returns one or more tool calls. Execute them all.
3. **Follow-up (LLM calls 2 to 3, optional):** send the results; the model may call more tools. Stop after 3 tool rounds.
4. **Compose (final LLM call):** the model returns a `TriageResult` as JSON. Every claim carries `citations`: a list of evidence keys.
5. **Validate in code:** drop any citation key not present in some `ToolResult.evidence`. Drop any claim left with no citations. If a tool returned `empty: true`, the result must include that absence as a claim (for example, "no merged PR touched this module in 12 months").
6. **Record:** in one transaction, create the AgentRun, `HANDLED`, `USED_EVIDENCE` to every cited key, and `PROPOSED` Actions with `status: 'proposed'` (assign actions carry their `module`).
7. **Fallback:** if the LLM fails or rate-limits, run a fixed pipeline (affected_modules, then rank_reviewers and bus_factor on the top module, then find_decisions) and fill the TriageResult from a template. Set `fallback: true` on the run. The demo must work with no LLM at all.

```python
class Claim(BaseModel):
    text: str
    citations: list[str]          # evidence keys

class TriageResult(BaseModel):
    run_key: str
    labels: list[Claim]           # text = label name
    reviewers: list[Claim]        # text = login; reason in a second Claim if needed
    risks: list[Claim]
    decisions: list[Claim]
    comment_markdown: str         # the comment that apply_triage posts; numbered citations as links
    trace: list[ToolResult]
```

The system prompt states: use only tool results; cite evidence keys; prefer saying "no evidence" over guessing; pick labels only from labels that exist in the repo.

**`apply_triage` against the sandbox.** It acts only on incoming Issues (`incoming: true`, repo = `SANDBOX_REPO`), using their own `number`; any other issue means status `refused`. Labels are created in the sandbox if missing. An assign is applied only if the login is a collaborator on `SANDBOX_REPO`; otherwise it is recorded as `dry_run` with a note, and the suggestion still appears in the comment. In any posted comment, write logins in code spans without `@` (`` `octocat` ``) so real maintainers are never pinged. The UI may show `@login`; GitHub comments may not.

## 8. Memory loop

- **Episodic:** each AgentRun, with its evidence and actions. `recall_run` replays it.
- **Semantic:** Decisions, Docs and Chunks.
- **Procedural:** Rules. They apply inside `rank_reviewers` itself, in Cypher, so a rule changes results with no extra LLM call.

Feedback flow: `POST /tenants/{t}/runs/{run}/feedback` with `{action_key, verdict, reason, scope: 'module' | 'global'}`. A `reject` on an assign action creates the Feedback (`ON` the Action), sets the Action to `rejected`, and creates a Rule with `kind: 'avoid'`, `APPLIES_TO` that person, `DERIVED_FROM` the Feedback, and `scope` set to the Action's `module` (for 'module') or null (for 'global'). No LLM is needed. A rule can be switched off (`active: false`) from the UI.

## 9. API (`api/main.py`)

| Method and path | Does |
| --- | --- |
| `POST /tenants/{t}/triage` | body `{issue_number}` or `{title, body}` → TriageResult |
| `POST /tenants/{t}/runs/{run}/apply` | body `{dry_run}` → apply_triage result |
| `POST /tenants/{t}/runs/{run}/feedback` | as in section 8 |
| `GET /tenants/{t}/runs/{run}` | recall_run |
| `POST /tenants/{t}/ask` | body `{question}` → cited answer from find_decisions plus one compose call |
| `GET /tenants/{t}/path?from=&to=` | explain_path, for drawing an evidence path on the canvas |
| `GET /tenants/{t}/subgraph?keys=...` | nodes and edges among those keys plus their 1-hop neighbours, capped at 150 nodes |
| `GET /tenants/{t}/runs?limit=20` | recent runs, newest first, for the live runs list (the UI polls it every 5 s) |
| `GET /tenants/{t}/stats` | node and edge counts, last ingest time, proof results, latency table |
| `GET /tenants` | configured tenants |
| `POST /webhooks/github` | GitHub webhook receiver (section 13) |
| `/mcp` | hosted read-only MCP endpoint over HTTP (section 13) |

CORS allows the Vercel domain and localhost only.

## 10. Backtest

Superseded by section 14 (Proof), which keeps this backtest as its reviewer metric and adds baselines, modules, labels and latency.

## 11. UI (`web/`)

One route, `/t/[tenant]`, built as `design/README.md` describes: triage card on the left, graph canvas in the centre, trace on the right. Plus:

- A header with the tenant switcher and the proof headline (`metric` style): the graph's top-3 reviewer hit rate. Clicking it opens the Proof panel: a grouped bar chart of graph vs both baselines on the three metrics (graph = `chart-1`, CODEOWNERS = `chart-2`, similar-issues = `chart-3`), with `n` and the latency table under it.
- A live runs list (above the issue input): runs from webhooks and from the UI, newest first, each with its issue, time and status. Selecting one loads it into the three panes.
- A Dry run switch beside **Apply to sandbox** at the bottom of the triage card, on by default. When on, the button reads **Apply to sandbox (dry run)**.
- An "Ask" box at the bottom of the triage card for cited Q&A.
- Canvas behaviour exactly as in `design/graph-canvas.md`.
- Map `design/tokens.json` to CSS variables in `web/app/globals.css`. Dark is the default theme.

## 12. Out of scope

Multi-agent handoffs and concurrent batch triage, the "what if X leaves" simulator, GitHub Discussions, user auth (the UI is public and read-only except dry runs and feedback), a second LLM provider beyond the fallback, editing rules beyond on/off, and any GitHub write outside `SANDBOX_REPO`.

## 13. Live action (owner: B)

**Webhook** (`api/webhooks.py`, `POST /webhooks/github`). The sandbox repo sends `issues` and `issue_comment` events.

1. Verify `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with `GITHUB_WEBHOOK_SECRET`, constant-time compare). Bad signature → 401.
2. Ignore any event whose `repository.full_name` is not `SANDBOX_REPO`, or whose `sender.login` is Keystone's own bot account (no loops).
3. Deduplicate on `X-GitHub-Delivery`; also skip an issue triaged in the last 60 s.
4. Reply 202 at once and process in a single background worker queue, one job at a time, to respect the LLM budget.

**On `issues.opened` (and `edited`):**

- Upsert the incoming Issue (`incoming: true`, embedding, `AUTHORED` from the sender).
- If the body has a "Replayed from <url>" line, MERGE `REPLAY_OF` to that upstream Issue.
- Run the agent.
- Call `apply_triage` with `dry_run = false`. This is the one place it runs live by default; the sandbox and collaborator rules in section 7 still apply.
- The comment ends with a link to the run in the UI.

**On `issue_comment.created`, from a sandbox collaborator only:**

- `/keystone reject <login> <reason>` → the same flow as the feedback route (section 8), scope `module`, then a reply confirming the Rule and a re-triage.
- `/keystone why <login>` → `explain_path` from the issue to that person, replied as a short chain of keys with links, plus the UI link.
- Anything else is ignored.

**Replay** (`ingest/replay.py`): copies an upstream issue's title and body into a new sandbox issue, adding a first line "Replayed from <upstream url>". The webhook does the rest.

**Hosted MCP** (`api/mcp_http.py`: the server from `api/mcp_server.py`, served over HTTP at `/mcp` behind Caddy):

- Exposes the six read-only graph tools; `apply_triage` is never exposed over HTTP.
- Public tenants only, no auth (the data is public), with a per-IP limit of 30 calls a minute.
- The README gives a copy-paste config for MCP clients that support remote servers, plus three example prompts.

**New env:** `GITHUB_WEBHOOK_SECRET`, `KEYSTONE_BOT_LOGIN`, `PUBLIC_BASE_URL`.

## 14. Proof (owner: A; `ingest/proof.py`, `ingest/bench.py`)

**Sample:** up to 50 closed issues with a FIXES PR that is merged, is not a sweep, and has at least one reviewer who is not its author. Every method gets `before` = the issue's `created_at` (no peeking).

| Metric | Truth | Graph (Keystone) | Baseline 1 | Baseline 2: similar-issues (vector only) |
| --- | --- | --- | --- | --- |
| Reviewer hit@3 | actual reviewers of the fixing PR | `rank_reviewers` on the top module from `affected_modules` | members of the Teams that OWN the module the fix touched most; when no Team with known members owns it, the repo's 3 most frequent reviewers before the date (each per-issue row records which was used) | reviewers and authors of the fixing PRs of the 10 nearest earlier issues, by count |
| Module hit@1 and @3 | modules the fixing PR touched | `affected_modules` | the 3 most-changed modules before the date | modules of the single nearest earlier issue's fix |
| Label precision@2 | the issue's labels | labels from `affected_modules`, weighted by evidence | the 2 most common labels before the date | labels of the 3 nearest earlier issues |

The similar-issues baseline is what a vector-only RAG agent would do with the same embeddings. It answers the judges' real question: what does the graph add?

**Output:** `data/proof/<tenant>.json` holding `n`, every rate, and per-issue rows (so the README can quote one win and one loss). Report the numbers as they come out, even where the graph loses; say so in the README.

**Latency** (`ingest/bench.py`): each tool's queries run 50 times warm, reporting p50 and p95 in ms, alongside node and edge counts. Output `data/bench/<tenant>.json`. Both files are exposed via `/stats` and tabled in the README.
