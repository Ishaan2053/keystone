# Operations

How to run, load, deploy and connect Keystone. The [README](../README.md) has the overview and the quick start.

- [Environment](#environment)
- [Local API development](#local-api-development)
- [Load the graph](#load-the-graph)
- [Local frontend development](#local-frontend-development)
- [Compose usage](#compose-usage)
- [Deploy to a VM](#deploy-to-a-vm)
- [Live webhook flow and commands](#live-webhook-flow-and-commands)
- [Hosted MCP over HTTPS](#hosted-mcp-over-https)
- [Data and safety rules](#data-and-safety-rules)
- [Tests on Windows](#tests-on-windows)

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
- `FALKORDB_HOST=localhost` (for host-run ingest jobs and `uvicorn`; Compose sets `FALKORDB_HOST: falkordb` for the `api` container itself)
- `FALKORDB_PORT=6379`
- `CORS_ORIGINS=http://localhost:3001`
- `API_MODE=fixtures`
- `NEXT_PUBLIC_DATA_SOURCE=fixtures`
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`

Secrets such as GitHub, webhook, Gemini, Groq, and graph passwords are blank in `.env.example` by design.

### Optional second tenant (off by default)

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

The Pallets tenant has not been ingested yet, so no counts or timings are recorded for it.

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

## Load the graph

The live API (`API_MODE=live`) and the agent read the graph `tenant_falkordb`; until it is loaded, the live graph routes answer a sanitized `503`. From a clean clone, load it with these commands, in order. The API image ships only `ingest/replay.py` and the tenant configs, not these jobs, so run them from the host venv above (`pip install -r requirements.txt`). Each job reads the repo `.env` (the shell wins); use `FALKORDB_HOST=localhost` (the `.env.example` default), because Compose publishes FalkorDB's `6379` on `127.0.0.1` only.

```bash
docker compose up -d falkordb                  # FalkorDB on 127.0.0.1:6379; needs FALKORDB_PASSWORD in .env
python -m ingest.fetch --tenant falkordb       # GitHub -> data/raw/falkordb/*.json (cached); needs GITHUB_TOKEN (read-only)
python -m ingest.load --tenant falkordb        # data/raw -> graph tenant_falkordb; a load never drops the graph
python -m ingest.decisions --tenant falkordb   # LLM job; needs GEMINI_API_KEY (GROQ_API_KEY is the fallback), or responses already cached in data/llm_cache/
python -m ingest.proof --tenant falkordb       # graph vs baselines -> data/proof/falkordb.json
python -m ingest.bench --tenant falkordb       # query latency -> data/bench/falkordb.json
```

`ingest.decisions` also takes `--prs N` (default 150) and `--no-docs` for a smaller trial run; `ingest.fetch --refresh` recomputes the history cutoff and refetches. Every LLM response is cached on disk, so a re-run costs only the calls not made yet. `/stats` reads `data/proof/` and `data/bench/` as written by the last two jobs.

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

The backend selects its own implementation independently with `API_MODE=fixtures|live`. In live mode, the API serves `GET /tenants`, and `GET /tenants/{tenant}/stats`, `/subgraph?keys=[...]` and `/path?from=&to=` from that tenant's graph (`tenant_<slug>`) through the named queries in `cypher/` and the `explain_path` tool; unknown tenants are `404` and a failed graph read is a sanitized `503`. Live triage, runs, run recall and feedback come from the agent and memory (`api/agent.py`, `api/memory.py`); live ask calls `agent.ask`. Live apply runs on `memory.GraphActionStore`, which the default `api/main.py` app passes in live mode. The API reads the repo `.env` on startup (the shell wins), so `API_MODE=live uvicorn api.main:app` can reach a local FalkorDB. The browser calls the API directly in `api` mode, so add the web origin to `CORS_ORIGINS`.

In `api` mode the workspace shows real node/edge counts and an issue-key form. Use the `?issue=` anchor with an encoded stable Issue key, for example `/t/falkordb?issue=issue%3Aowner%2Frepo%231`. Keys containing `/`, `#`, spaces or commas survive URL transport. A runs list polls the tenant's recent runs every 5 s, and selecting one loads its triage, trace and graph; until a run is selected the card and trace show "No triage run selected", and fixture triage is never substituted.

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

## Deploy to a VM

The demo runs this way on one GCP VM (FalkorDB, API and Caddy in Compose), with `web/` on Vercel. To deploy your own:

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

## Live webhook flow and commands

The live path is `POST /webhooks/github` → one background worker → the persisted
upsert → triage → apply pipeline, plus a collaborator command lane. It is off unless the
deployment is configured for it.

### GitHub webhook setup

Point the sandbox repository's webhook at `POST https://<API_DOMAIN>/webhooks/github` and
subscribe to the `issues` and `issue_comment` events. Set Content type to `application/json`
(`application/x-www-form-urlencoded` also works). Only `issues` (`opened`, `edited`) and
`issue_comment` (`created`) are acted on; other events and actions are acknowledged and
ignored. The receiver verifies `X-Hub-Signature-256`, an HMAC-SHA256 of the **raw** body keyed
by `GITHUB_WEBHOOK_SECRET`, in constant time before it reads anything else; a bad or missing
signature is `401`. `X-GitHub-Delivery` is the dedupe key: a repeat delivery of an accepted
job is acknowledged (`202`) without a second triage.

Required configuration/secrets (blank in `.env.example` by design):

- `GITHUB_WEBHOOK_SECRET` — the webhook HMAC secret; without it admission fails closed (`503`).
- `KEYSTONE_BOT_LOGIN` — the bot's own login; its events and comments are dropped so the bot never loops, except an `issues.opened` the bot created through `ingest.replay`, identified by its replay marker (a first line ``Replayed from `https://github.com/<owner>/<name>/issues/<n>` ``), which is triaged.
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

## Hosted MCP over HTTPS

The six read-only graph tools are also served over MCP at `POST https://<API_DOMAIN>/mcp`; the demo deployment serves them at `https://35-244-53-175.sslip.io/mcp`. Caddy terminates HTTPS on the VM.

Cursor supports remote Streamable HTTP MCP servers directly. Add this to `.cursor/mcp.json` (or the client's equivalent) — no stdio bridge package, no dependency install and no auth token:

```json
{"mcpServers":{"keystone":{"url":"https://35-244-53-175.sslip.io/mcp"}}}
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
- if the optional second tenant is enabled, both CSV variables become `falkordb,pallets` (`PUBLIC_MCP_TENANTS` must stay a subset of `TENANTS`).

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

### Testing the endpoint

`pytest -q tests/test_mcp_http.py` exercises admission, tenant gating, the rolling quota, batching and the mounted lifecycle with stub tools and no network. The opt-in remote smoke test runs against a deployed endpoint:

```bash
KEYSTONE_MCP_TEST_URL="https://35-244-53-175.sslip.io/mcp" pytest -q tests/test_mcp_http.py -k remote_rank_reviewers_acceptance
```

The test is skipped unless `KEYSTONE_MCP_TEST_URL` is set; it lists the tools (expecting exactly the six above) and validates a `rank_reviewers` result for tenant `falkordb` on `module:FalkorDB/FalkorDB/src/execution_plan` through the SDK's `ToolResult`, requiring non-empty Cypher, the module key in `evidence` and at least one real reviewer. It makes no graph writes, ingest or LLM calls. It passed against the demo deployment on 2026-10-18.

## Data and safety rules

- Keystone uses public GitHub data only.
- Never store emails or private personal details.
- GitHub write actions are sandbox-only and must target `SANDBOX_REPO`. In live mode the public apply route is dry-run only and refuses `dry_run=false` with HTTP 403 before any service execution; in fixture mode it returns the fixed refused fixture and never writes.
- Text written to the sandbox (replayed issues, triage comments) has its @-mentions, `owner/repo#n` references and GitHub issue URLs rewritten into code spans, so upstream maintainers are never pinged or backlinked.
- `GITHUB_TOKEN` is read-only; `GITHUB_WRITE_TOKEN` is the only token that writes, scoped to the sandbox repo.
- `DRY_RUN` is parsed but not read by any path. The public `/apply` route is always dry-run (live writes are refused with `403`); only the internal webhook flow applies live (`dry_run=false`), so no environment flag can make the public route write.
- The infrastructure performs no GitHub actions; the only live GitHub writes happen on the verified webhook path.

## Tests on Windows

Two socket-patching tests (`tests/test_api_shell.py::test_fixture_live_apply_is_refused` and `tests/test_webhooks.py::test_request_path_does_no_embedding_or_network_work`) fail on Windows because asyncio's self-pipe falls back to a loopback `socketpair()` connect there; they pass on Linux and in CI. The `tests/test_infra.py` Compose tests need a Docker Compose that supports `docker compose config --format`.

