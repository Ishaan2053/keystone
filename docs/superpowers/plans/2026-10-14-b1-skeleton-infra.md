# B1 Skeleton and Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide the project skeleton and password-protected FalkorDB, with an API health endpoint served over public HTTPS on the VM.

**Architecture:** Docker Compose runs FalkorDB, a minimal FastAPI process and Caddy on a private service network. Only Caddy exposes public ports; FalkorDB persists to `/data`, with local-only development access to its database and Browser. B2 extends the same API app, rather than creating a second shell.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, pytest/httpx, Docker Compose, FalkorDB, Caddy.

**Spec:** `CLAUDE.md`; `docs/tasks.md` B1; `docs/spec.md` sections 2, 9, 12–13; `docs/superpowers/plans/2026-10-14-b1-b3-contract-proposal.md` (pending approval).

## Global Constraints

- “Python 3.11+”; “Docker Compose on one VM (FalkorDB + API + Caddy); `web/` on Vercel”.
- “FALKORDB_PASSWORD (required)”; “DRY_RUN=true”; “LLM_CACHE=on”; “TENANTS=falkordb”.
- “Public data, no personal details.” Never store emails or commit secrets.
- “One FalkorDB graph per org, named `tenant_<slug>`.”
- No commits, staging, branch creation or repository initialization now. Plan files only before kickoff; execute only with explicit approval after Oct 15, 12:01 AM IST.
- Do not alter shared contracts or A-owned files; empty skeleton directories are allowed, not placeholder A implementations.
- Approve proposed environment additions/dependencies before execution. Do not tick the task on the owner's behalf.

## Review Focus

- Missing/blank graph password: Compose refuses to start rather than running unauthenticated.
- Wrong database credentials: the graph rejects the query; the health endpoint must not imply readiness.
- Container recreation: persisted graph data survives without exposing `/data` publicly.
- Public network access: database, Browser and API ports are not publicly bound.
- Missing DNS/blocked ACME ports: deployment remains unverified, with an actionable prerequisite rather than a false HTTPS claim.

---

## Preconditions and file map

Use existing handoff contents unchanged. Owner B supplies VM access, `API_DOMAIN` DNS pointing at it, Docker/Compose, ports 80/443 available, and a password entered privately in `.env`. No secrets in screenshots or verification logs.

Create root `.gitignore`, `.env.example`, `README.md`, `requirements.txt`, `Dockerfile.api`, `docker-compose.yml`, `Caddyfile`; `api/__init__.py`, `api/main.py`; `tests/test_health.py`, `tests/test_infra.py`; root directories `ingest/`, `web/`, `cypher/`, `data/` if absent. Use `.gitkeep` only where an otherwise-empty skeleton needs preservation; no `.gitkeep` in ignored data. T0 creates shared schema/types/fixtures separately.

Target B1 at roughly 75–90 minutes excluding external DNS/ACME wait. If implementation grows beyond two hours, stop and propose reducing scope; do not consume Saturday Buffer.

### Task 1: Minimal API process and safe skeleton

**Files:** create `api/__init__.py`, `api/main.py`, `tests/test_health.py`, `requirements.txt`, `.gitignore`, `.env.example`, `README.md`; create the missing skeleton directories only.

**Interfaces:** produces `api.main.app: FastAPI`, `health() -> dict[str,str]`, `GET /health → {'status':'ok'}`. B2 replaces app construction with `create_app()` while preserving this import and endpoint. `.env.example` lists every variable in `CLAUDE.md` plus approved `API_DOMAIN`, `CORS_ORIGINS`, `API_MODE`.

- [ ] **Step 1: Write the failing health test.**

```python
def test_health_is_process_liveness():
    from api.main import app
    from fastapi.testclient import TestClient

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Install only approved backend dependencies in a local virtual environment and run the test.** Run `python -m pytest tests/test_health.py -q`; expected failure importing `api.main`, not a dependency error.
- [ ] **Step 3: Implement `health()` and `app` in `api/main.py`.** No graph client, credentials, agent or network call in this endpoint. Add dependency pins to `requirements.txt`; it is the sole initial backend install source.
- [ ] **Step 4: Add the skeleton and config examples.** Ignore `.env`, `.env.*` except `.env.example`, `data/`, Python caches/venvs, `web/node_modules/`, `web/.next/`, browser test artifacts and local OS files; preserve source and lock files. Set blank secret values; nonsecret defaults follow the proposal. README includes local/Compose commands, health-is-liveness explanation, owner acceptance checks, AI-use disclosure naming Claude Code **and OpenCode assistance accurately**, and public GitHub-data/no-emails/sandbox-only statements. Mark graph counts, Proof, deployment and MCP documentation as not yet measured/implemented; do not invent numbers.
- [ ] **Step 5: Verify the deliverable.** Run `python -m pytest tests/test_health.py -q`; expected `1 passed`. Manually compare `.env.example` with every variable in `CLAUDE.md`, check no credential value appears, and ensure all seven root folders exist. No commit.

### Task 2: Compose stack with credential and persistence checks

**Files:** create `Dockerfile.api`, `docker-compose.yml`, `Caddyfile`, `tests/test_infra.py`; modify `README.md` with exact deploy/verification commands.

**Interfaces:** service names `falkordb`, `api`, `caddy`; API service listens on container port 8000; Caddy proxies `{$API_DOMAIN}` to `api:8000` without stripping route prefixes. FalkorDB port 6379 requires password; its named volume mounts at `/data`. Caddy persists `/data` and `/config` in separate named volumes. API connects later using `FALKORDB_HOST=falkordb`, port 6379.

- [ ] **Step 1: Write infrastructure contract tests in `tests/test_infra.py`.** Use Python standard library `subprocess`/`json` with `docker compose config --format json` (Docker is a prerequisite, no YAML test dependency). Test `test_missing_password_refuses_config` with `--env-file /dev/null` and sanitized subprocess environment: unset and empty `FALKORDB_PASSWORD` both produce nonzero exit and mention `FALKORDB_PASSWORD`. Test `test_service_bindings_and_volumes`: graph 6379/Browser 3000 bind `127.0.0.1`, API has no published port, only Caddy binds 80/443 publicly, FalkorDB mount target equals `/data`, Caddy mounts `/data` and `/config`, no service uses host networking. Set a disposable test password/hostname only in the subprocess environment; never log resolved config.
- [ ] **Step 2: Run the tests before adding Compose.** Run `python -m pytest tests/test_infra.py -q`; expected failure because Compose files are missing.
- [ ] **Step 3: Implement the container files.** Use supported pinned `falkordb/falkordb`, Python 3.11+ and Caddy image tags, selected/recorded at execution. Build API from the root, copy requirements and `api/`, launch `uvicorn api.main:app --host 0.0.0.0 --port 8000`. Require `${FALKORDB_PASSWORD:?FALKORDB_PASSWORD is required}`; explicitly set that variable inside the graph container for authenticated checks, and pass it through FalkorDB's image-supported Redis arguments without shell interpolation. Verify the chosen image actually supports the arguments before relying on them. Explicitly enable persistent storage appropriate to that image (AOF or verified snapshot configuration), not just a mount. Graph healthcheck authenticates without printing the secret; API startup waits for graph health. Do not copy `.env` into the image. Preserve Caddy routing for future `/webhooks/github` and `/mcp`.
- [ ] **Step 4: Run static and live local checks.** Run `python -m pytest tests/test_health.py tests/test_infra.py -q`; expected all pass. Run `docker compose up -d --build`; expected all three services healthy/running (Caddy HTTPS may await VM DNS). Run `docker compose exec -T falkordb sh -c 'REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli GRAPH.QUERY tenant_falkordb "RETURN 1"'`; expected result value `1`. Run the same query without auth and with a wrong disposable password; expect `NOAUTH`/`WRONGPASS` (inspect reply text, not only Redis CLI exit code).
- [ ] **Step 5: Prove persistence using an isolated disposable Compose project.** Authenticate, create one marker node in graph `b1_persistence_check`; wait for the configured persistence mechanism, recreate only the database container, then authenticate and query the same marker. Expected marker retained. Delete that test graph afterward. Never remove the application's volumes or use `down -v` against the working project. Capture only redacted output. No commit.

### Task 3: VM HTTPS acceptance

**Files:** modify `README.md` deployment prerequisites and verified command instructions; no new application implementation.

**Interfaces:** consumes Task 2 stack and B-provided domain/VM; produces public `https://<API_DOMAIN>/health`. It does not deploy the web app (B12).

- [ ] **Step 1: Check DNS and ports before deployment.** Run `dig +short "$API_DOMAIN"` on the operator machine and verify the expected VM IP; verify VM firewall permits inbound 80/443, not 6379/3000/8000. If not ready, stop the acceptance check and report the prerequisite; do not claim B1 done.
- [ ] **Step 2: Run the stack on the VM.** Transfer source without `.env`/data; create `.env` privately on the VM, then run `docker compose up -d --build` there. Inspect `docker compose ps` and redacted Caddy logs for valid certificate issuance.
- [ ] **Step 3: Run the actual B1 Done when checks.** From outside the VM run `curl --fail --show-error --include "https://$API_DOMAIN/health"` without `-k`; expected HTTP 200 and `{"status":"ok"}` with valid TLS. On the VM run Task 2's authenticated `GRAPH.QUERY ... "RETURN 1"`; expected `1`. Check externally that 6379/3000/8000 are unreachable. Show the real output to B, without secret-bearing Compose config or command arguments.
- [ ] **Step 4: Record verification instructions/results in README as factual, dated notes.** If VM access is unavailable, mark remote acceptance pending and hand over the exact checks instead of checking B1. Leave `docs/tasks.md` untouched; no commit.

## Handoff

B2 consumes `api.main.app`, dependency/install source and Compose API service. B supplies actual deployment values privately. Approval of this plan is not permission to run VM operations immediately; execution and remote access must be explicitly authorized. No commits in any task.
