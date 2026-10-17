# Keystone

Keystone is a hackathon triage agent for public open-source GitHub data backed by FalkorDB. The graph model, the read API and web shell, the decisions job, and the verified live webhook/command path (B7/B8) are implemented on this branch; the A10/A11 live adapters, the live UI/Proof panel (B11) and the full VM deployment are still pending.

## Current status

Implemented and reviewed on this branch:

- **B1 infrastructure.** `GET /health` returns `{"status":"ok"}`; Docker Compose defines `falkordb`, `api` and `caddy`; FalkorDB requires `FALKORDB_PASSWORD`, persists to a named volume mounted at `/data` and `/var/lib/falkordb/data` with AOF, and its Redis `6379`/Browser `3000` ports are localhost-only; the API is not published directly — Caddy is the only public service and binds `80`/`443`.
- **B2 fixture API shell.** `api/config.py`, `api/fixture_store.py`, `api/routes/*` and the `create_app()` factory in `api/main.py`; fixture routes serve curated `api/fixtures/`; CORS normalizes trailing slashes and allows only explicitly configured origins; `/openapi.json` lists every path including the reserved webhook/MCP placeholders.
- **B3/B4/B5 web shell.** Next.js App Router workspace at `/t/[tenant]`, three-pane responsive layout, dark/light theme, triage card, trace panel with copyable Cypher, citation chips and the graph canvas; one `NEXT_PUBLIC_DATA_SOURCE=fixtures|api` switch with live `/tenants`, `/stats` and `/subgraph` reads, a real issue-key anchor (`?issue=`) and an explicit "no triage run selected" state that never substitutes fixture triage.
- **A7/A8/A9 decisions and graph.** The minimal LLM layer and Decisions job (verbatim-quote check, `MADE_IN`/`ABOUT`, embeddings) and the README graph model and query inventory.
- **B6 replay and apply.** `ingest/replay.py` and `apply_triage` with sandbox-only writes; the public `/apply` route is dry-run only and refuses `dry_run=false` with `403`.
- **B7 webhooks.** A verified `POST /webhooks/github` receiver (raw-body HMAC, delivery dedupe, sandbox/bot guards) feeding one serialized worker, the persisted incoming-issue upsert with `REPLAY_OF` provenance (currently always absent: `allowed_repos` is empty pending an agreed repo-inventory source, so no `REPLAY_OF` edge can be attached), and the composed upsert → triage → apply pipeline.
- **B8 commands.** The collaborator lane (`/keystone reject`, `/keystone why`) with safe replies, graph-backed `why` evidence, and a reject that records a module-scoped Rule and re-triages. No automatic remove-assignee.

Pending:

- **A10 (`api/agent.py`) and A11 (`api/memory.py`) are absent.** The live triage, feedback and recall adapters are injected seams. Until they land, live triage/runs/feedback/ask answer `503` and the webhook worker is not built, so no live triage or command has run end-to-end.
- **B11 live UI and Proof panel** (runs polling and Proof bars/latency) and **Proof metrics** are not implemented.
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

- `record_actions.cypher`: records each `apply_triage` result on the Action node a run PROPOSED, so feedback and recall see what actually happened. Only the live apply path calls it, and that path waits on A10's run store, so nothing calls it in the current build.
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

## Environment

Copy `.env.example` to `.env` only on the machine where the stack runs and fill private values there. Do not commit `.env`.

Required for Compose:

- `FALKORDB_PASSWORD` — required; no default is provided.
- `API_DOMAIN` — DNS name Caddy should serve.

Defaults preserved in `.env.example`:

- `DRY_RUN=true`
- `LLM_CACHE=on`
- `TENANTS=falkordb`
- `FALKORDB_HOST=falkordb`
- `FALKORDB_PORT=6379`
- `CORS_ORIGINS=http://localhost:3001`
- `API_MODE=fixtures`
- `NEXT_PUBLIC_DATA_SOURCE=fixtures`
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`

Secrets such as GitHub, webhook, Gemini, Groq, and graph passwords are blank in `.env.example` by design.

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

The backend selects its own implementation independently with `API_MODE=fixtures|live`. In live mode, the API serves `GET /tenants`, and `GET /tenants/{tenant}/stats`, `/subgraph?keys=[...]` and `/path?from=&to=` from that tenant's graph (`tenant_<slug>`) through the named queries in `cypher/` and the `explain_path` tool; unknown tenants are `404` and a failed graph read is a sanitized `503`. Live triage, runs, feedback and ask are pending A10 and answer `503` in live mode, as does apply until A10 supplies its run store. The API reads the repo `.env` on startup (the shell wins), so `API_MODE=live uvicorn api.main:app` can reach a local FalkorDB. The browser calls the API directly in `api` mode, so add the web origin to `CORS_ORIGINS`.

In `api` mode the workspace shows real node/edge counts and an issue-key form. Use the `?issue=` anchor with an encoded stable Issue key, for example `/t/falkordb?issue=issue%3Aowner%2Frepo%231`. Keys containing `/`, `#`, spaces or commas survive URL transport. Live triage runs are pending A10, so the card and trace show "No triage run selected"; fixture triage is never substituted.

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
3. Transfer source without `.env` or `data/`, create `.env` privately on the VM, then run:

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

Keystone is built with AI assistance, directed and reviewed by the developers. B1-B8 were built with OpenCode assistance; the B4-B6 review fixes were made with Claude Code. Keep this disclosure accurate as the build continues.
