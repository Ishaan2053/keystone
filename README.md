# Keystone

Keystone is a hackathon triage agent for public open-source GitHub data backed by FalkorDB. This commit contains the B1 infrastructure shell (a minimal FastAPI process, `/health` process liveness, Docker Compose wiring for FalkorDB/API/Caddy). Subsequent tasks implement the API shell (B2) and web UI shell (B3).

## Current status

Implemented locally in B1, B2 & B3:

- `GET /health` returns `{"status":"ok"}` from the API process.
- Docker Compose defines `falkordb`, `api`, and `caddy` services.
- FalkorDB requires `FALKORDB_PASSWORD`, persists to a named volume mounted at `/data` and `/var/lib/falkordb/data`, and enables AOF persistence.
- FalkorDB Redis port `6379` and Browser `3000` are localhost-only for development access.
- The API is not published directly; Caddy is the only public service and binds `80`/`443`.
- Caddy certificate and config state use named persistent volumes.
- B2 fixture API shell implemented: `api/config.py`, `api/fixture_store.py`, `api/routes/*`, and `create_app()` factory in `api/main.py`.
- Fixture routes serve curated data from `api/fixtures/`; CORS normalizes trailing slashes and allows only explicitly configured origins; `/openapi.json` lists all 13 paths including reserved webhook/MCP placeholders.
- B3 fixture web shell implemented in `web/`: Next.js App Router workspace at `/t/[tenant]`, three-pane responsive layout, dark/light theme, triage card, trace panel with copyable Cypher, citation chips, and canvas placeholder.
- B4 real graph view implemented in `web/`: one `NEXT_PUBLIC_DATA_SOURCE=fixtures|api` switch, live `/tenants`, `/stats` and `/subgraph` reads with no-store requests, a real issue-key anchor (`?issue=`), visible live counts, and an explicit "no triage run selected" state that never substitutes fixture triage.
- Verified for the B4-B6 review fixes: the backend pytest suite passes locally, including live `/stats`, `/subgraph` and `/path` tests against an ingested `tenant_falkordb` (skipped when FalkorDB is unreachable). On Windows six tests fail for platform reasons (five in `tests/test_infra.py`, one socket-patching test in `tests/test_api_shell.py`). The web checks under "Verification commands" were not re-run for this note.

Upcoming tasks:

- Graph counts, live integrations, Proof metrics, and full VM deployment.

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
| MEMBER_OF | Person → Team | inferred teams only; org team membership isn't public |
| REVIEWED_FOR | Person → Person | derived: reviewer → author, `n` = PRs reviewed |
| DESCRIBES, HAS_CHUNK | Doc → Module, Doc → Chunk | a doc about a module, split for vector search |
| MADE_IN, ABOUT | Decision → PR, Issue, Doc; Decision → Module | where a decision was made and what code it governs |
| HANDLED, USED_EVIDENCE, PROPOSED | AgentRun → Issue, any Node, Action | agent memory: the issue, every key cited, the actions proposed |
| ON, DERIVED_FROM, APPLIES_TO, TARGETS | Feedback → Action, Rule → Feedback, Rule → Person, Action → Person or Label | agent memory: feedback and the rules it creates |

Rules that shape the data:

- **Bots are not people.** Bot accounts (dependabot, Copilot, coderabbitai and others) get no Person, AUTHORED, REVIEWED, REVIEW_REQUESTED or WROTE, and their comments aren't loaded. About half of all reviews come from review bots, which would otherwise top every ranking.
- **Sweeps carry no expertise.** A PR that touches more than 300 files, or whose head branch is the base of 3 or more PRs (a release merge such as `staging` into `main`), keeps every edge except TOUCHES.
- **Graph algorithms run in FalkorDB.** `algo.pageRank`, `algo.betweenness` and `algo.labelPropagation` run over REVIEWED_FOR and are stored on Person. Communities of 2 or more people become `team:inferred/<n>` Teams, because FalkorDB's own CODEOWNERS covers only `/build/` and `/.github/`.
- **Embeddings are local.** `all-MiniLM-L6-v2` (384-d) vectors on Issue, Chunk and Decision, stored with `vecf32()` behind cosine vector indexes.

### Counts

From a fresh `python -m ingest.load --tenant falkordb` on 2026-10-16, pasted as printed. A load never drops the graph, which also holds agent memory. The 210 Decisions and their MADE_IN and ABOUT edges were written earlier by `python -m ingest.decisions`. No triage has run against this graph yet, so it has no agent memory nodes.

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
- `DRY_RUN` is parsed but not used yet; it is reserved for the webhook flow (B7).
- B1 does not perform live GitHub actions.

## AI use

Keystone is built with AI assistance, directed and reviewed by the developers. B1-B6 were built with OpenCode assistance; the B4-B6 review fixes were made with Claude Code. Keep this disclosure accurate as the build continues.
