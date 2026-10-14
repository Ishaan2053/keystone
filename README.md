# Keystone

Keystone is a hackathon triage agent for public open-source GitHub data backed by FalkorDB. This working copy contains the B1 infrastructure shell (a minimal FastAPI process, `/health` process liveness, Docker Compose wiring for FalkorDB/API/Caddy), the implemented B2 fixture-mode API shell and B3 fixture web shell, draft B1–B3 contracts with fixtures. Operator instructions are below; see Working copy notes for the copied references and path details.

## Current status

Implemented locally (not yet accepted):

- `GET /health` returns `{"status":"ok"}` from the API process.
- Docker Compose defines `falkordb`, `api`, and `caddy` services.
- FalkorDB requires `FALKORDB_PASSWORD`, persists to a named volume mounted at `/data`, and enables AOF persistence.
- FalkorDB Redis port `6379` and Browser `3000` are localhost-only for development access.
- The API is not published directly; Caddy is the only public service and binds `80`/`443`.
- Caddy certificate and config state use named persistent volumes.
- Static Compose config checks pass with disposable values, and the B1 health/infra tests pass locally.
- B2 fixture API shell implemented locally: `api/config.py`, `api/fixture_store.py`, `api/routes/*`, and a `create_app()` factory in `api/main.py`. Fixture routes serve curated data from `api/fixtures/`; CORS allows only explicitly configured origins; `/openapi.json` lists all 13 paths including the reserved webhook/MCP placeholders.
- Full backend suite: `.venv/bin/python -m pytest -q` → `95 passed, 1 warning` (one dependency deprecation warning, intentionally unsuppressed; see Local API development).
- B3 fixture web shell implemented locally: the `/t/{tenant}` workspace (for example `/t/falkordb`) renders the triage panel, tool trace, dry-run apply preview, and the B5 canvas placeholder, with dark and light themes and responsive desktop/tablet/phone layouts. Controller verification: `npm run typecheck` exit 0, `npm run test:empty` passed, Playwright suite `16 passed`, `npm run build` exit 0; screenshots in `.execution/screenshots/` are local visual evidence, not acceptance proof. Web review completed; residual coverage items are now covered by the checked-in `test:empty` runner and phone legend reachability test (see `.execution/web-report.md` and `.execution/web-rereview1.md`).
- Draft B1–B3 contracts (`api/schemas.py`, `api/fixtures/`, `web/lib/types.ts`) exist and validate locally; they are unapproved drafts awaiting A's integration approval (see Fixture-only mode).

Not yet measured or implemented:

- Graph counts and product data are not loaded yet.
- Live integrations (GitHub, FalkorDB, LLM), polling/replay, Proof metrics, benchmark, live webhook action, MCP HTTP, and the B5 graph canvas are not implemented; the fixture web shell listed above is implemented locally, but live integration-driven UI flows are not.
- The B3 fixture web shell is implemented locally and reviewed. The checked-in `npm run test:empty` runner covers empty fixture arrays, and the Playwright suite includes phone canvas legend reachability; local passes are not remote acceptance.
- Public VM deployment, valid TLS, remote `/health`, and live FalkorDB acceptance remain pending until VM/DNS/access and private credentials are supplied.
- The Docker daemon is currently unavailable on this machine, so live Compose, auth, and persistence checks have not run here.

## Working copy notes

- The root path of this copy ends in a trailing space (`…/code `). Commands below assume you have already changed into that root directory and use relative paths.
- This root is not a git repository, and no commits, branches, or other git operations are performed, by explicit instruction.
- `design/` (`README.md`, `tokens.json`, `graph-canvas.md`) and `docs/` (`spec.md`, `tasks.md`, `docs/superpowers/plans/`) are copies of the handoff references, kept locally for convenience; the originals remain the source of truth.
- `.execution/` holds worker reports, review packages, scratch scripts, screenshots, and a `compat-venv`; these are local progress records with evidence, not proof of remote acceptance. `.execution/` and `web/*.tsbuildinfo` are excluded from future git and Docker context via `.gitignore`/`.dockerignore`.

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

Secrets such as GitHub, webhook, Gemini, Groq, and graph passwords are blank in `.env.example` by design.

## Local API development

Requires Python 3.11 or newer. For a reproducible clean setup (a shared `.venv` already exists in this copy, but these commands rebuild it from scratch):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` pins FastAPI, httpx, pydantic, pytest, and uvicorn. Then run the tests and the local server:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn api.main:app --port 8000
curl --fail --show-error http://127.0.0.1:8000/health
```

Known warning: with the pinned dependencies (FastAPI 0.119.0 → Starlette 0.48.0 on AnyIO 4.15.1), the suite prints one unsuppressed deprecation warning (`anyio.abc.BlockingPortal` via Starlette's TestClient). It does not fail tests and is intentionally left visible; no dependency change has been applied (an isolated FastAPI 0.143.0 probe removes it but introduces a new TestClient warning and a major Starlette version change — see `.execution/dependency-report.md`).

`/health` is process liveness only. It intentionally does not connect to FalkorDB, read credentials, call GitHub, or imply product readiness.

## Local frontend development

From the root, install web dependencies and start the dev server:

```bash
cd web
npm ci
npm run dev
```

The dev server listens on http://localhost:3001, matching the default `CORS_ORIGINS` origin. Verification commands, run from `web/`:

```bash
npm run typecheck
npm run test:empty
npm run test:e2e
npm run build
```

`npm run test:empty` compiles and runs the checked-in empty-render harness (`tests/render-empty.tsx`) against the real `TracePanel`, `TriageCard`, and `CanvasPlaceholder` components. `npm run test:e2e` runs Playwright against the server on port 3001; install the Playwright browser binaries once if not already present. The fixture route is `/t/falkordb` (unknown tenants return 404). Controller verification on 2026-10-14: `npm run typecheck` exit 0; `npm run test:empty` passed; `npm run test:e2e` → 16 browser checks passed, including phone canvas containment and phone canvas legend reachability at rest and after citation selection; and `npm run build` exit 0. Screenshots: `.execution/screenshots/desktop-dark.png`, `.execution/screenshots/desktop-light.png`, `.execution/screenshots/phone.png` — local evidence only. Empty fixture arrays are covered by `npm run test:empty` (client render characterization only, not a backend-empty round trip); no VM or remote acceptance has run.

## Fixture-only mode

- `API_MODE=fixtures` is the only mode in this shell. The B2 routes serve curated fixtures from `api/fixtures/` with no GitHub, FalkorDB, LLM, or MCP calls and no writes; live apply is refused even when `dry_run=false`. Unknown tenants and runs return 404, configured tenants without fixture data return 503, request validation failures (including unexpected query parameters on tenant GET routes) return 422, and fixture data uses the fixed run UUID `00000000-0000-4000-8000-000000000001`.
- Webhook (`POST /webhooks/github`) and MCP (`GET/POST/DELETE /mcp`) are reserved placeholders that must return `501` until B7/B10. They are documented reservations, not functional integrations.
- The contracts in `api/schemas.py`, `api/fixtures/`, and `web/lib/types.ts` are local drafts awaiting A's integration approval before real graph integration; shapes may still change, and fixture statistics are illustrative, not observed.

## Compose usage

The Docker daemon is currently unavailable on this machine, so the live checks below are pending here. Create a private `.env` first. Then run:

Password handling note: Compose writes a Redis config file inside the named FalkorDB volume before the database starts, then starts FalkorDB with `REDIS_ARGS=/data/redis.conf`. This avoids placing the graph password in a split-prone `REDIS_ARGS` string and preserves ordinary special characters such as spaces, quotes, dollar signs, semicolons, and backslashes. Do not print `/data/redis.conf`; it contains the graph password.


```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Authenticated graph check without printing the password:

```bash
docker compose exec -T falkordb sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli GRAPH.QUERY tenant_falkordb "RETURN 1"'
```

Expected result is a returned value of `1`. To inspect auth failure behavior, use disposable values only and do not print a secret-rich config:

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

Remote B1 acceptance is pending, not done: the Docker daemon is unavailable locally, and no VM access, DNS, or private credentials have been supplied. When VM access, DNS, and private credentials are available, run the checks below and record dated factual results before marking B1 accepted:

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
- GitHub write actions are sandbox-only and must target `SANDBOX_REPO`.
- B1 does not perform live GitHub actions.

## AI use

The project handoff identifies Claude Code as part of the build process. This B1 infrastructure shell was implemented with OpenCode assistance as a subagent handoff. Keep this disclosure accurate as the build continues.
