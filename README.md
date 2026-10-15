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
- Full test suites pass locally: backend pytest suite, Playwright browser tests (fixture and API modes), empty fixture render harness, and Next.js production build.

Upcoming tasks:

- Graph counts, live integrations, Proof metrics, and full VM deployment.

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

The backend selects its own implementation independently with `API_MODE=fixtures|live`. In live mode, the API serves `GET /tenants`, `GET /tenants/{tenant}/stats` and `GET /tenants/{tenant}/subgraph?keys=[...]`; unknown tenants are `404` and a failed graph read is a sanitized `503`. The browser calls the API directly in `api` mode, so add the web origin to `CORS_ORIGINS`.

In `api` mode the workspace shows real node/edge counts and an issue-key form. Use the `?issue=` anchor with an encoded stable Issue key, for example `/t/falkordb?issue=issue%3Aowner%2Frepo%231`. Keys containing `/`, `#`, spaces or commas survive URL transport. Until a real triage run exists the card and trace show "No triage run selected"; fixture triage is never substituted.

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
- GitHub write actions are sandbox-only and must target `SANDBOX_REPO`; the public apply route is dry-run only and refuses `dry_run=false` with HTTP 403 before any service execution.
- `GITHUB_TOKEN` is read-only; `GITHUB_WRITE_TOKEN` is the only token that writes, scoped to the sandbox repo.
- `DRY_RUN=true` is the deployment default and does not override a trusted caller's explicit `dry_run` later.
- B1 does not perform live GitHub actions.

## AI use

The project handoff identifies Claude Code as part of the build process. This B1 infrastructure shell was implemented with OpenCode assistance as a subagent handoff. Keep this disclosure accurate as the build continues.
