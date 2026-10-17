# Keystone

Keystone is a hackathon triage agent for public open-source GitHub data backed by FalkorDB. The graph model, the read API and web shell, the decisions job, the verified live webhook/command path (B7/B8), and the hosted read-only MCP endpoint at `POST /mcp` (B10) are implemented on this branch, as are the A10 agent (`api/agent.py`), A11 memory (`api/memory.py`) and A12 ask, with their adapters wired into the default `api/main.py` app; the live UI/Proof panel (B11), the full VM deployment and the remote hosted-MCP acceptance are still pending.

## Current status

Implemented and reviewed on this branch:

- **B1 infrastructure.** `GET /health` returns `{"status":"ok"}`; Docker Compose defines `falkordb`, `api` and `caddy`; FalkorDB requires `FALKORDB_PASSWORD`, persists to a named volume mounted at `/data` and `/var/lib/falkordb/data` with AOF, and its Redis `6379`/Browser `3000` ports are localhost-only; the API is not published directly — Caddy is the only public service and binds `80`/`443`.
- **B2 fixture API shell.** `api/config.py`, `api/fixture_store.py`, `api/routes/*` and the `create_app()` factory in `api/main.py`; fixture routes serve curated `api/fixtures/`; CORS normalizes trailing slashes and allows only explicitly configured origins; `/openapi.json` lists every REST path.
- **B3/B4/B5 web shell.** Next.js App Router workspace at `/t/[tenant]`, three-pane responsive layout, dark/light theme, triage card, trace panel with copyable Cypher, citation chips and the graph canvas; one `NEXT_PUBLIC_DATA_SOURCE=fixtures|api` switch with live `/tenants`, `/stats` and `/subgraph` reads, a real issue-key anchor (`?issue=`) and an explicit "no triage run selected" state that never substitutes fixture triage.
- **A7/A8/A9 decisions and graph.** The minimal LLM layer and Decisions job (verbatim-quote check, `MADE_IN`/`ABOUT`, embeddings) and the README graph model and query inventory.
- **B6 replay and apply.** `ingest/replay.py` and `apply_triage` with sandbox-only writes; the public `/apply` route is dry-run only and refuses `dry_run=false` with `403`.
- **B7 webhooks.** A verified `POST /webhooks/github` receiver (raw-body HMAC, delivery dedupe, sandbox/bot guards) feeding one serialized worker, the persisted incoming-issue upsert with `REPLAY_OF` provenance (currently always absent: `allowed_repos` is empty pending an agreed repo-inventory source, so no `REPLAY_OF` edge can be attached), and the composed upsert → triage → apply pipeline.
- **B8 commands.** The collaborator lane (`/keystone reject`, `/keystone why`) with safe replies, graph-backed `why` evidence, and a reject that records a module-scoped Rule and re-triages. No automatic remove-assignee.
- **A10/A11 adapters wired (#68).** In live mode the default `api.main:app` passes `agent.triage_issue` as `agent_triage`, `memory.record_feedback` as `record_feedback` and `memory.GraphActionStore()` as `apply_store`, so live `/apply` and `POST /webhooks/github` get an apply service and webhook worker instead of a `503`. Fixture mode never imports them. No live webhook triage or command has run end-to-end yet (see the deployment item below).
- **B10 hosted MCP.** `api/mcp_http.py` mounts the shared six read-only graph tools at `POST /mcp` over stateless Streamable HTTP with JSON responses: no auth (the data is public), public tenants only (`PUBLIC_MCP_TENANTS`), and one rolling 30-tool-call-attempts-per-IP-per-minute budget. `apply_triage` and arbitrary query text are refused over HTTP before any tool runs. See [Hosted MCP over HTTPS](#hosted-mcp-over-https-b10).

Pending:

- **B11 live UI and Proof panel** (runs polling and Proof bars/latency) and **Proof metrics** are not implemented.
- **Remote hosted-MCP acceptance is pending.** No deployed API domain exists in this environment, so the Cursor client check and the opt-in `KEYSTONE_MCP_TEST_URL` smoke test have not run; the deterministic `tests/test_mcp_http.py` suite is not remote acceptance. Record dated remote evidence before calling B10 accepted.
- **Full VM deployment and live acceptance are pending.** No live load, live GitHub write or live LLM call has been run for this work; the webhook flow is verified by unit/integration tests only, and FalkorDB-backed tests skip when the graph is unreachable.
- A Windows-only note from the B4-B6 review still applies: six tests fail for platform reasons (five in `tests/test_infra.py`, one socket-patching test in `tests/test_api_shell.py`).

## Graph model

Each org is one FalkorDB graph, `tenant_<slug>`. The demo tenant is `tenant_falkordb`, built from FalkorDB/FalkorDB, FalkorDB/GraphRAG-SDK and FalkorDB/falkordb-browser. Every node has two labels, its kind and `Node`, plus a stable `key` such as `person:octocat` or `pr:FalkorDB/FalkorDB#123`. Evidence, citations and the UI use keys, never FalkorDB internal ids. Full property lists are in [docs/spec.md section 3](docs/spec.md#3-graph-model).

| Node | Key | What it is |
| --- | --- | --- |
| Person | `person:<login>` | a human contributor: login and profile URL only, never an email |
| Team | `team:<org>/<slug>`, `team:user/<login>`, `team:inferred/<n>` | a CODEOWNERS owner, or a community found in the review graph |
| Repo | `repo:<owner>/<name>` | a repository |
| Module | `module:<owner>/<name>/<path>` | a file's first path segment, or its first two under `src`, `lib`, `packages` or `pkg`; root files go to `(root)` |
| File | `file:<owner>/<name>/<path>` | a file some PR touched |
| Issue | `issue:<owner>/<name>#<n>` | an issue, with a 384-d embedding of its title and body |
| PR | `pr:<owner>/<name>#<n>` | a pull request; `sweep` marks the ones that are not evidence of expertise |
| Comment | `comment:<github id>` | a human comment on an issue or PR |
| Label | `label:<owner>/<name>/<label>` | a repo label |
| Doc, Chunk | `doc:<owner>/<name>/<path>`, `chunk:<doc key>#<i>` | a markdown doc and its embedded sections |
| Decision | `decision:<sha1>` | a decision the LLM found in a PR thread or design doc, kept only if its quote is verbatim in the source |
| AgentRun, Action, Feedback, Rule | `run:`, `action:`, `feedback:`, `rule:` + uuid | agent memory: a triage run, what it proposed, what a human said, and the rule that feedback created |

| Relationship | From → To | Meaning |
| --- | --- | --- |
| AUTHORED | Person → PR, Issue | wrote it |
| REVIEWED | Person → PR | reviewed someone else's PR; `state` is their latest review |
| REVIEW_REQUESTED | PR → Person | an open review request: current review load |
| TOUCHES | PR → File | changed the file (none for sweeps) |
| IN | File → Module, Module → Repo | where code lives |
| FIXES | PR → Issue | "fixes / closes / resolves #n" in the PR body |
| REPLAY_OF | Issue → Issue | a sandbox issue → the upstream issue it replays |
| HAS_LABEL | Issue, PR → Label | labelled |
| WROTE, ON | Person → Comment → Issue, PR | who said what, where |
| OWNS | Team → Module | from CODEOWNERS directory rules |
| MEMBER_OF | Person → Team | inferred teams, and one-person CODEOWNERS teams; org team membership isn't public |
| REVIEWED_FOR | Person → Person | derived: reviewer → author, `n` = PRs reviewed |
| DESCRIBES, HAS_CHUNK | Doc → Module, Doc → Chunk | a doc about a module, split for vector search |
| MADE_IN, ABOUT | Decision → PR, Issue, Doc; Decision → Module | where a decision was made and what code it governs |
| HANDLED, USED_EVIDENCE, PROPOSED | AgentRun → Issue, any Node, Action | agent memory: the issue, every key cited, the actions proposed |
| ON, DERIVED_FROM, APPLIES_TO, TARGETS | Feedback → Action, Rule → Feedback, Rule → Person, Action → Person or Label | agent memory: feedback and the rules it creates |

Rules that shape the data:

- **Bots are not people.** Bot accounts (dependabot, Copilot, coderabbitai and others) get no Person, AUTHORED, REVIEWED, REVIEW_REQUESTED or WROTE, and their comments aren't loaded. About half of all reviews come from review bots, which would otherwise top every ranking.
- **Sweeps carry no expertise.** A PR that touches more than 300 files, or whose head is a same-repo branch that is the base of 3 or more PRs (a release merge such as `staging` into `main`), keeps every edge except TOUCHES.
- **Graph algorithms run in FalkorDB.** `algo.pageRank`, `algo.betweenness` and `algo.labelPropagation` run over REVIEWED_FOR and are stored on Person. Communities of 2 or more people become `team:inferred/<n>` Teams, because FalkorDB's own CODEOWNERS covers only `/build/` and `/.github/`.
- **Embeddings are local.** `all-MiniLM-L6-v2` (384-d) vectors on Issue, Chunk and Decision, stored with `vecf32()` behind cosine vector indexes.

### Counts

From a fresh `python -m ingest.load --tenant falkordb` on 2026-10-16, pasted as printed, trimmed to the per-repo summary and the counts. A load never drops the graph, which also holds agent memory. The 210 Decisions and their MADE_IN and ABOUT edges were written earlier by `python -m ingest.decisions`. No triage has run against this graph yet, so it has no agent memory nodes.

```text
FalkorDB/FalkorDB: 1006 PRs, 33 sweeps (33 by size, 0 by branch)
  skipped 5997 bot comments
  skipped 2295 bot reviews
  skipped 287 bot-authored PRs (no AUTHORED)
  skipped 77 comments on PRs or issues outside the window
  CODEOWNERS /build/ @FalkorDB/falkordb-core -> build
  CODEOWNERS /.github/ @FalkorDB/falkordb-core -> .github
  CODEOWNERS /CODEOWNERS @FalkorDB/falkordb-core -> nothing
FalkorDB/GraphRAG-SDK: 164 PRs, 1 sweeps (1 by size, 0 by branch)
  skipped 1379 bot comments
  skipped 534 bot reviews
  skipped 53 bot-authored PRs (no AUTHORED)
  skipped 5 comments on PRs or issues outside the window
FalkorDB/falkordb-browser: 781 PRs, 57 sweeps (0 by size, 57 by branch)
  skipped 6275 bot comments
  skipped 1789 bot reviews
  skipped 368 bot-authored PRs (no AUTHORED)
  skipped 2 comments on PRs or issues outside the window
edges dropped (endpoint not in the data): FIXES 18
nodes:
  Chunk                  500
  Comment               5688
  Decision               210
  Doc                     18
  File                  3348
  Issue                 2010
  Label                  175
  Module                  71
  PR                    1951
  Person                 294
  Repo                     3
  Team                     4
edges:
  ABOUT                  207
  AUTHORED              3252
  DESCRIBES               18
  FIXES                  866
  HAS_CHUNK              500
  HAS_LABEL             3781
  IN                    3419
  MADE_IN                210
  MEMBER_OF               38
  ON                    5688
  OWNS                     2
  REVIEWED              1222
  REVIEWED_FOR            85
  REVIEW_REQUESTED        71
  TOUCHES              13519
  WROTE                 5688
sweeps: 91
loaded tenant_falkordb in 130.2s
```

## Cypher

Every query the product runs is a file in [`cypher/`](cypher/), loaded by name and always run with parameters. Each line says what the query does and why it needs a graph.

Triage tools (`api/tools/`, served to the agent over MCP):

- `affected_modules.cypher`: finds the past issues nearest a new one by vector search, then walks Issue ← FIXES ← PR → TOUCHES → File → IN → Module. That three-hop join from text similarity to code is something no single table holds.
- `rank_reviewers.cypher`: in one query, scores the people who AUTHORED or REVIEWED PRs touching a module, divides by their open REVIEW_REQUESTED load, and zeroes anyone an active avoid Rule from agent memory APPLIES_TO. All three facts sit next to the same Person node.
- `bus_factor.cypher`: Module ← IN ← File ← TOUCHES ← PR ← AUTHORED ← Person, grouped by author. Ownership concentration is a count of paths.
- `find_decisions.cypher`: vector search over Decisions, then MADE_IN to the PR, issue or doc, ABOUT to the module, and OWNS back to its Team. It returns why a decision was made, where, and who owns the code.
- `find_chunks.cypher`: vector search over doc Chunks, joined over HAS_CHUNK to their Doc. The `find_decisions` tool runs it alongside the decisions.
- `explain_path.cypher`: `algo.SPpaths` shortest path, up to 4 hops, between two keys over evidence edges only. The proof behind a claim is a path in the graph.
- `recall_run.cypher`: agent memory. One traversal from an AgentRun to every key it USED_EVIDENCE, the Actions it PROPOSED, the Feedback ON them, and the Rules DERIVED_FROM that feedback.
- `similar_issues.cypher`: the nearest Issues by vector index alone, with no traversal. The loader uses it as a sanity check, and it is the starting point for Proof's vector-only baseline, which the graph has to beat.

API reads (`/tenants/{tenant}/...`):

- `subgraph.cypher`: seed nodes, their one-hop neighbours and every edge between them. This is what the canvas draws.
- `stats.cypher`: node counts per label and edge counts per type, for `/stats` and the load summary above.

Agent (`api/agent.py`, `api/memory.py`):

- `issue_context.cypher`: the issue under triage with its author, and over REPLAY_OF the upstream issue it replays with that issue's author, so a replay never finds its own original or suggests its own author.
- `issue_by_number.cypher`: the issues with a number, sandbox issues first. A tenant spans several repos, so a bare number can be ambiguous.
- `record_run.cypher`: one query, so one transaction: the AgentRun, HANDLED to its issue, USED_EVIDENCE to every key it cited, and the Actions it PROPOSED, each TARGETS its Person or Label. Episodic memory is a subgraph wired into the same nodes the evidence came from.
- `run_context.cypher`: what `apply_triage` needs for a run: the issue it HANDLED with its own number and sandbox flag, and the Actions it PROPOSED.
- `list_runs.cypher`: the newest AgentRuns, for the live runs list.
- `feedback_target.cypher`: from an AgentRun to the Action it PROPOSED and the Person that Action TARGETS, so feedback is checked against the run that made the suggestion.
- `record_feedback.cypher`: writes Feedback ON the Action and, for a rejected reviewer, MERGEs the one avoid Rule for that Person and scope, which APPLIES_TO the Person and is DERIVED_FROM each Feedback, in one write. A repeated reject reactivates the same Rule rather than adding another. The `rank_reviewers` query reads that Rule on the next triage, so the feedback changes the ranking inside the graph query, with no LLM call.

Load (`ingest/load.py`), in the order it runs:

- `indexes.cypher`: a `key` index on every label, vector indexes on Issue, Chunk and Decision, and a full-text index on Issue. They make every MERGE by key and every lookup an index seek, not a scan.
- `load_reset.cypher`: deletes REVIEW_REQUESTED before rewriting it. Load is current state, so an edge must go when a review request is withdrawn.
- `load_cleanup.cypher`: removes TOUCHES from PRs marked as sweeps, so a release merge never makes its author an expert on every module.
- `drop_stale_chunks.cypher`: deletes Chunks the current docs no longer produce, so vector search never returns text that is gone.
- `derive_reviewed_for.cypher`: collapses Person → REVIEWED → PR ← AUTHORED ← Person into one weighted Person → Person edge. This is the social graph the algorithms run on.
- `algo_scores.cypher`: FalkorDB's `pageRank`, `betweenness` and `labelPropagation` over REVIEWED_FOR, stored on Person.
- `inferred_teams.cypher`: turns label-propagation communities into Team nodes with MEMBER_OF edges. Keys are numbered by community size, so the same data gives the same keys and a reload reuses the teams rather than recreating them, which lets agent-memory edges into a team survive. A reload that changes the communities can renumber them.
- `set_embeddings.cypher`: writes vectors with `vecf32()` so the vector indexes pick them up.
- `top_people.cypher`: the highest-pagerank people with their betweenness, printed after the algorithms run.
- `inferred_team_members.cypher`: each inferred team with its members, printed after the teams are rebuilt.

Decisions job (`ingest/decisions.py`):

- `decision_sources.cypher`: the most-discussed merged PRs, each with the modules it touches (TOUCHES → IN) and its human comments (WROTE → ON) in order, as LLM input.
- `decision_docs.cypher`: ADR, RFC and design docs with their Chunks in order and the Module each DESCRIBES.
- `module_keys.cypher`: checks the LLM's module paths against real Module nodes, so a decision is never attached to code that doesn't exist.
- `write_decisions.cypher`: MERGEs each Decision with MADE_IN to its source, ABOUT to its modules, and its embedding.

Proof and benchmarking:

- `backtest_sample.cypher`: samples closed, non-incoming Issues evenly across history after a warmup, with merged, non-sweep fixes reviewed by someone other than the issue author. FIXES → TOUCHES → IN, REVIEWED and HAS_LABEL supply the non-test modules, reviewers and labels used as truth.
- `baseline_codeowners_reviewers.cypher`: follows Module ← OWNS ← Team ← MEMBER_OF ← Person for up to three owners, falling back to the repo's most frequent reviewers of PRs merged before the cutoff. Ownership and review history meet at Person nodes, with excluded logins left out.
- `baseline_similar_labels.cypher`: takes the three nearest earlier labelled Issues by vector search, then follows HAS_LABEL to rank two labels by usage, breaking ties by nearest issue. The edges turn text similarity into label predictions.
- `baseline_similar_modules.cypher`: takes the nearest earlier Issue with a fix merged before the cutoff, then follows FIXES → TOUCHES → IN to rank up to three non-test modules by files touched. The paths connect a similar report to the code its fixes changed.
- `baseline_similar_reviewers.cypher`: takes the ten nearest earlier Issues with fixes merged before the cutoff, then follows FIXES and AUTHORED or REVIEWED to rank three people by PR count, leaving out excluded logins. It returns the supporting issue and PR keys alongside each person.
- `baseline_top_labels.cypher`: counts HAS_LABEL edges from the repo's earlier, non-incoming Issues to return its two most-used labels. The newest supporting issue key lets Proof check the historical cutoff.
- `baseline_top_modules.cypher`: walks Repo ← IN ← Module ← IN ← File ← TOUCHES ← PR to rank three non-test modules by PRs merged before the cutoff. The newest supporting PR key lets Proof check that the counts use only past changes.
- `proof_evidence_times.cypher`: resolves returned evidence keys to Node timestamps and reports any dated at or after the cutoff, using merged_at before created_at. The shared Node label lets one leak check cover evidence of different types; undated nodes pass.
- `latest_run.cypher`: selects the newest timestamped AgentRun key for the recall benchmark, returning no row when none exists. It supplies a real graph-memory starting node for timing `recall_run`.

Actions and webhooks:

- `record_actions.cypher`: records each `apply_triage` result on the Action node a run PROPOSED, so feedback and recall see what actually happened. Only the live apply path calls it, through the `GraphActionStore` that `api/main.py` passes in live mode.
- `upsert_incoming_issue.cypher`: MERGEs one incoming sandbox Issue from a validated webhook job, its human author (AUTHORED) and an optional `REPLAY_OF` edge to an already-loaded upstream issue. The replay target must already exist in the tenant graph, so the query can attach provenance without ever fabricating an upstream Issue.
- `webhook_latest_run.cypher`: the latest HANDLED AgentRun for one exact incoming issue, with its evidence, proposed Actions and their Feedback/Rules, in the same row shape as `recall_run`. Feedback, actions and evidence sit behind one traversal, so a collaborator reject resolves the right action on the right run.
- `webhook_node_links.cypher`: resolves only the given keys' stored URLs in the current tenant. A key with no stored URL returns no row, so a `why` reply links real stored sources and never synthesizes one from the key or from comment text.
- `webhook_person_key.cypher`: resolves a GitHub login to the graph's canonical `person:<login>` key, matching case-insensitively because logins are case-insensitive while the key preserves the stored casing. An unknown login returns no row, so a `why` reply never invents a Person key.

## Live webhook flow, commands and deployment

The live path (B7/B8) is `POST /webhooks/github` → one background worker → the persisted
upsert → triage → apply pipeline, plus a collaborator command lane. It is off unless the
deployment is configured for it.

### GitHub webhook setup

Point the sandbox repository's webhook at `POST https://<API_DOMAIN>/webhooks/github` and
subscribe to the `issues` and `issue_comment` events. Only `issues` (`opened`, `edited`) and
`issue_comment` (`created`) are acted on; other events and actions are acknowledged and
ignored. The receiver verifies `X-Hub-Signature-256`, an HMAC-SHA256 of the **raw** body keyed
by `GITHUB_WEBHOOK_SECRET`, in constant time before it reads anything else; a bad or missing
signature is `401`. `X-GitHub-Delivery` is the dedupe key: a repeat delivery of an accepted
job is acknowledged (`202`) without a second triage.

Required configuration/secrets (blank in `.env.example` by design):

- `GITHUB_WEBHOOK_SECRET` — the webhook HMAC secret; without it admission fails closed (`503`).
- `KEYSTONE_BOT_LOGIN` — the bot's own login; its events and comments are dropped so the bot never loops.
- `SANDBOX_REPO` — strict `owner/name`; a payload whose `repository.full_name` is not this repo is ignored.
- `GITHUB_WRITE_TOKEN` — the only writing token; the collaborator check and all sandbox writes use it.
- `PUBLIC_BASE_URL` — the canonical public origin used to build the UI run link posted in comments.
- `API_MODE=live`, exactly one entry in `TENANTS`, and a non-blank `PUBLIC_BASE_URL` are also required before the worker is built.

A `/keystone` command is accepted only from a **collaborator on the sandbox repo**, checked
against the comment sender (never `author_association`) with the sandbox-repo collaborator
permission of `GITHUB_WRITE_TOKEN`. A non-collaborator or bot command gets no reply and runs no
graph query.

### Deployment constraints

- **One API process, one enabled tenant.** Admission requires `API_MODE=live` and exactly one configured tenant; the tenant is fixed at construction and never read from request input.
- **One serialized worker.** Jobs run one at a time on a single dedicated thread to respect the LLM budget. The queue holds at most 100 pending jobs; a full queue answers `503` and the delivery can be retried.
- **60-second cooldown** on ordinary `issues` triage: the same issue is not re-triaged inside the window. A collaborator command still re-triages.
- **In-memory state.** Pending jobs, the per-issue guard, the cooldown and the delivery/comment dedupe sets live in this process only. A restart can lose pending work, and there is no cross-process exactly-once; GitHub redelivery is the recovery path for a delivery that was not completed.

### Command grammar

A strict leading line, from a sandbox collaborator only:

```text
/keystone reject <login> <reason>
/keystone why <login>
```

- `reject` records a module-scoped reject for that person through the feedback path, confirms the persisted Rule, then re-triages. The reason is the entire remaining text, so a multiline reason is preserved.
- `why` answers with the stored evidence path from the incoming issue to that person, each key and relationship in order with its stored source link, plus the UI run link.
- Anything else — an ordinary comment, an incidental or quoted mention, a reject with no reason — is ignored. **No automatic remove-assignee is performed.**

### Public dry-run policy

The public `POST /apply` route is deliberately dry-run only: it refuses `dry_run=false` with
`403` before any service execution. Only the internal webhook flow calls
`apply_triage(..., dry_run=False)`, so live sandbox writes happen solely on the verified
webhook path, under the sandbox and collaborator rules.

## Hosted MCP over HTTPS (B10)

The six read-only graph tools are also served over MCP at `POST https://YOUR_API_DOMAIN/mcp`. `YOUR_API_DOMAIN` is a placeholder: replace it with the deployed API domain before publishing. No live domain is published here, and Caddy terminates HTTPS on the VM.

Cursor supports remote Streamable HTTP MCP servers directly. Add this to `.cursor/mcp.json` (or the client's equivalent) — no stdio bridge package, no dependency install and no auth token:

```json
{"mcpServers":{"keystone":{"url":"https://YOUR_API_DOMAIN/mcp"}}}
```

The server exposes exactly these six read-only tools:

- `affected_modules`
- `rank_reviewers`
- `bus_factor`
- `find_decisions`
- `explain_path`
- `recall_run`

`apply_triage` is never exposed over HTTP. It is not in the list above, and the hosted transport refuses it, a generic query tool and any unknown tool name before a tool or the graph runs, so there is no query-text input and no graph write over HTTP; Cypher stays in `cypher/` and is run only by the named tools.

Only tenants listed in `PUBLIC_MCP_TENANTS` are reachable; a call naming any other tenant is refused. A blank value serves every tenant in `TENANTS`; an explicit value narrows it and must be a subset of `TENANTS`. The hosted transport is live-mode only (`API_MODE=live`); in fixture mode there is no transport and `/mcp` answers `503`.

Example live configuration:

- `API_MODE=live`
- `TENANTS=falkordb`
- `PUBLIC_MCP_TENANTS=falkordb`
- no published API port: the API's `8000` and FalkorDB's `6379`/`3000` stay private; only `80`/`443` are public (apart from separately restricted administration).
- if the optional B9 second tenant is enabled, both CSV variables become `falkordb,pallets` (`PUBLIC_MCP_TENANTS` must stay a subset of `TENANTS`). No ingest is part of B10.

### Ingress and the trusted client IP

The approved ingress is directly internet-facing Caddy, with no preceding proxy or CDN. The API DNS record points at the VM; if Cloudflare is used it must be DNS-only (grey cloud), and Caddy manages HTTPS. Caddy overwrites `X-Keystone-Client-IP` on every upstream request with the real socket peer (`{remote_host}`), and the API has no published port, so a caller cannot forge its own quota key: the API trusts that one header and nothing else (`Forwarded`, `X-Forwarded-For`, `X-Real-IP` and MCP session IDs never select a bucket). For a local or trusted-ingress check the fronting proxy supplies the header; the test suite sets `X-Keystone-Client-IP` directly. REST and stdio policies are unchanged: the public REST `/apply` route is still dry-run only, and the stdio server still exposes all seven tools including `apply_triage`.

### Rolling quota

- One rolling 60-second budget of 30 tool-call attempts per public client IP, shared across reconnects, all six tools and every public tenant.
- A call is a `tools/call` attempt — including notifications, unknown tools, private tenants and argument errors — not an HTTP request. `initialize`, `tools/list` and other exchanges consume nothing.
- When the budget is exhausted the server returns HTTP `429` with a `Retry-After` header in seconds and a JSON-RPC error `-32000` (`Tool call rate limit exceeded`).
- JSON arrays are rejected as unsupported batching (`400`, `-32600`): nothing executes and nothing is charged.
- Every hosted MCP POST has a **1 MiB (1,048,576-byte) total body limit** (`413` on overflow) and a **10-second total upload deadline** (`408` on timeout), even with exhausted quota or absent/inaccurate `Content-Length`. Overflow stops body consumption; incomplete/disconnected uploads never execute. The deadline covers upload only, not tool runtime.
- Limits of the design: clients behind one shared NAT IP share one budget; a process restart clears the in-memory budget; the budget is per API process, and the deployment runs one API process.

### Example prompts

After connecting, try these; they use only returned evidence:

1. “Use rank_reviewers for tenant falkordb, module module:FalkorDB/FalkorDB/src/execution_plan, k 3. Explain each suggestion using only returned evidence keys.”
2. “For tenant falkordb, use affected_modules on ‘Crash with OPTIONAL MATCH’, then bus_factor on the top module. Cite the returned evidence; say if none is found.”
3. “For tenant falkordb, use find_decisions to explain query timeout configuration. Cite its source URLs and evidence keys; do not invent a decision if the result is empty.”

### Status and acceptance

Local deterministic evidence: `pytest -q tests/test_mcp_http.py` exercises admission, tenant gating, the rolling quota, batching and the mounted lifecycle with stub tools and no network. **This is not remote acceptance.**

Remote acceptance — a real Cursor client plus the opt-in smoke test — is **pending**: no deployed API domain exists in this environment, so it has not been run. When a VM, DNS and the loaded graph are available, run the smoke test from outside the VM:

```bash
KEYSTONE_MCP_TEST_URL="https://YOUR_API_DOMAIN/mcp" pytest -q tests/test_mcp_http.py -k remote_rank_reviewers_acceptance
```

The test is skipped unless `KEYSTONE_MCP_TEST_URL` is set; it lists the tools (expecting exactly the six above) and validates a `rank_reviewers` result for tenant `falkordb` on `module:FalkorDB/FalkorDB/src/execution_plan` through the SDK's `ToolResult`, requiring non-empty Cypher, the module key in `evidence` and at least one real reviewer. It makes no graph writes, ingest or LLM calls.

Also connect Cursor with the published snippet, run prompt 1, and record the dated URL, client, output, the six tools, a real reviewer and its evidence. Direct HTTP `apply_triage` must fail before any action-store access. On a dedicated acceptance IP, use cheap empty `recall_run` calls to reach attempt 31 and retry with forged `Forwarded`/custom-IP headers: Caddy must overwrite them and the same quota must reject, with no extra execution; a second real IP must have its own allowance. Do not spoof headers against a direct API port and call that a Caddy test. If reviewer evidence is empty, record acceptance **pending/failed**, not PASS, and do not load data automatically.

## Environment

Copy `.env.example` to `.env` only on the machine where the stack runs and fill private values there. Do not commit `.env`.

Required for Compose:

- `FALKORDB_PASSWORD` — required; no default is provided.
- `API_DOMAIN` — DNS name Caddy should serve.

Defaults preserved in `.env.example`:

- `DRY_RUN=true`
- `LLM_CACHE=on`
- `TENANTS=falkordb`
- `PUBLIC_MCP_TENANTS=` (blank: every tenant in `TENANTS` is served over hosted MCP)
- `FALKORDB_HOST=falkordb`
- `FALKORDB_PORT=6379`
- `CORS_ORIGINS=http://localhost:3001`
- `API_MODE=fixtures`
- `NEXT_PUBLIC_DATA_SOURCE=fixtures`
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`

Secrets such as GitHub, webhook, Gemini, Groq, and graph passwords are blank in `.env.example` by design.

### Optional second tenant (B9, off by default)

The second tenant, `pallets` (graph `tenant_pallets`, repo `pallets/flask`), is present but
off by default: `.env.example` keeps `TENANTS=falkordb` and the two-tenant setting is
commented. To enable it, ingest its public data and then widen the tenant lists:

```bash
python -m ingest.fetch --tenant pallets   # read-only GitHub credentials required; caches under data/raw/pallets
python -m ingest.load --tenant pallets    # loads tenant_pallets; a load never drops the graph (agent memory survives)
```

Then set `TENANTS=falkordb,pallets` (and, to also serve both over hosted MCP,
`PUBLIC_MCP_TENANTS=falkordb,pallets`; it must stay a subset of `TENANTS`), `API_MODE=live`,
and `NEXT_PUBLIC_DATA_SOURCE=api` for the two-tenant UI. Keep the single-tenant fixture
defaults unchanged in the template.

Real Pallets ingestion and two-tenant acceptance have **not** been run in this environment, so
no Pallets counts, timings or "different graph" screenshots are recorded here; those remain
pending until the commands above run with credentials and the loaded graph.

## Local API development

Requires Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
uvicorn api.main:app --port 8000
curl --fail --show-error http://127.0.0.1:8000/health
```

`/health` is process liveness only. It intentionally does not connect to FalkorDB, read credentials, call GitHub, or imply product readiness.

## Local frontend development

From the root, install web dependencies and start the dev server:

```bash
cd web
npm ci
npm run dev
```

The dev server listens on http://localhost:3001, matching the default `CORS_ORIGINS` origin.

### Fixture vs live data

The web app has one switch, `NEXT_PUBLIC_DATA_SOURCE`:

- `fixtures` (default) reads curated JSON from `api/fixtures/` on the server; the UI is labelled "Fixture data" and triage/apply results are explicitly synthetic.
- `api` reads the live graph API over HTTP and is labelled "Live data". Set `NEXT_PUBLIC_API_BASE_URL` to the API origin (a location, not a mode flag). Any other `NEXT_PUBLIC_DATA_SOURCE` value fails the render.

The backend selects its own implementation independently with `API_MODE=fixtures|live`. In live mode, the API serves `GET /tenants`, and `GET /tenants/{tenant}/stats`, `/subgraph?keys=[...]` and `/path?from=&to=` from that tenant's graph (`tenant_<slug>`) through the named queries in `cypher/` and the `explain_path` tool; unknown tenants are `404` and a failed graph read is a sanitized `503`. Live triage, runs, run recall and feedback come from the agent and memory (A10/A11); live ask calls `agent.ask` (A12). Live apply runs on A11's `GraphActionStore`, which the default `api/main.py` app passes in live mode. The API reads the repo `.env` on startup (the shell wins), so `API_MODE=live uvicorn api.main:app` can reach a local FalkorDB. The browser calls the API directly in `api` mode, so add the web origin to `CORS_ORIGINS`.

In `api` mode the workspace shows real node/edge counts and an issue-key form. Use the `?issue=` anchor with an encoded stable Issue key, for example `/t/falkordb?issue=issue%3Aowner%2Frepo%231`. Keys containing `/`, `#`, spaces or commas survive URL transport. Showing live runs in the workspace is pending B11, so the card and trace show "No triage run selected"; fixture triage is never substituted.

Copy frontend variables into `web/.env.local` (Next.js reads environment files from the `web/` directory, not the repository root).

Verification commands (run from `web/`):

```bash
npm run typecheck
npm run test:empty
npm run test:e2e      # fixture mode
npm run test:e2e:api  # API mode against a deterministic local fake API
npm run build
```

## Compose usage

Create a private `.env` first. Then run:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Authenticated graph check without printing the password:

```bash
docker compose exec -T falkordb sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli GRAPH.QUERY tenant_falkordb "RETURN 1"'
```

Expected result is a returned value of `1`. To inspect auth failure behavior, use disposable values only:

```bash
docker compose exec -T falkordb env -u REDISCLI_AUTH redis-cli GRAPH.QUERY tenant_falkordb "RETURN 1"
docker compose exec -T -e REDISCLI_AUTH=wrong-disposable-password falkordb redis-cli GRAPH.QUERY tenant_falkordb "RETURN 1"
```

The first unauthenticated query should report `NOAUTH`; the second should report `WRONGPASS` or an invalid-password equivalent.

Persistence check, using an isolated disposable graph name:

```bash
docker compose exec -T falkordb sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli GRAPH.QUERY b1_persistence_check "CREATE (:Marker {id: 1}) RETURN 1"'
docker compose restart falkordb
docker compose exec -T falkordb sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli GRAPH.QUERY b1_persistence_check "MATCH (m:Marker {id: 1}) RETURN count(m)"'
docker compose exec -T falkordb sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli GRAPH.DELETE b1_persistence_check'
```

Do not run `docker compose down -v` against the working project unless intentionally deleting local graph data.

## VM/TLS acceptance handoff

Remote B1 acceptance is pending, not done: when VM access, DNS, and private credentials are available, run the checks below and record dated factual results before marking B1 accepted:

1. Verify DNS points at the VM:

   ```bash
   dig +short "$API_DOMAIN"
   ```

2. Verify the VM firewall allows public `80`/`443` and does not expose `6379`, `3000`, or `8000`.
3. Transfer source without `.env` or `data/`, create `.env` privately on the VM, then run (copy only `data/proof/` and `data/bench/` from `python -m ingest.proof` / `ingest.bench`: the API's `/stats` reads them through read-only mounts in `docker-compose.yml`):

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

4. From outside the VM, verify HTTPS without `-k`:

   ```bash
   curl --fail --show-error --include "https://$API_DOMAIN/health"
   ```

5. On the VM, verify FalkorDB with the authenticated `GRAPH.QUERY ... "RETURN 1"` command above.

## Data and safety rules

- Keystone uses public GitHub data only.
- Never store emails or private personal details.
- GitHub write actions are sandbox-only and must target `SANDBOX_REPO`. In live mode the public apply route is dry-run only and refuses `dry_run=false` with HTTP 403 before any service execution; in fixture mode it returns the fixed refused fixture and never writes.
- Text written to the sandbox (replayed issues, triage comments) has its @-mentions, `owner/repo#n` references and GitHub issue URLs rewritten into code spans, so upstream maintainers are never pinged or backlinked.
- `GITHUB_TOKEN` is read-only; `GITHUB_WRITE_TOKEN` is the only token that writes, scoped to the sandbox repo.
- `DRY_RUN` is parsed but not read by any path. The public `/apply` route is always dry-run (live writes are refused with `403`); only the internal webhook flow applies live (`dry_run=false`), so no environment flag can make the public route write.
- B1 infrastructure performs no GitHub actions; the only live GitHub writes happen on the verified webhook path (B7/B8).

## AI use

Keystone is built with AI assistance, directed and reviewed by the developers. B1-B8 were built with OpenCode assistance; the B4-B6 review fixes were made with Claude Code. B10, the hosted read-only MCP endpoint, was also built with OpenCode assistance. Keep this disclosure accurate as the build continues.
