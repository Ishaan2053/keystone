# B9 Second Tenant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optionally show a separate Pallets/Flask dataset without mixing tenant graphs, runs, evidence, or client state.
**Architecture:** Add one tenant YAML and reuse A's fetch/load scripts and existing API routes. Give the existing workspace a tenant key; verify dispatch, a pending-response switch, and real run isolation in disposable graphs. Real ingestion is manual acceptance, not new test infrastructure.
**Tech Stack:** Existing Python 3.11+, PyYAML, pytest, FastAPI TestClient, FalkorDB, Next.js/React, TypeScript, Playwright; no dependencies added.
**Spec:** `docs/spec.md` §§2–5, 8–9, 11; `docs/tasks.md` B9; `CLAUDE.md`; `design/README.md`.

## Global Constraints
- B9 is optional, about one hour, and the first cut if the schedule slips. Full-year network ingestion is not guaranteed to fit; defer instead of borrowing B11/B12 time.
- One graph per org: `tenant_falkordb` and `tenant_pallets`; memory and evidence remain graph-local. Stable keys may legitimately repeat across graphs.
- Approved values: `slug: pallets`, `org: pallets`, `repos: [pallets/flask]`, `since_days: 365`, `max_pr_files: 300`, `docs_globs: ["README.md", "docs/**/*.md", "*.md"]`.
- Leave existing `ingest/tenants/falkordb.yml`, its three repositories, raw data, and real graph untouched; no resets, refreshes, replay, triage, feedback, or GitHub writes in real-data acceptance.
- No new product feature, dependency, shared schema, fixture dataset, ingestion format, or query infrastructure. Preserve sandbox-only writes, dry-run defaults, stable keys, and public-data privacy rules.
- Confirmed B10 decision: both configured public tenants are exposed read-only; `apply_triage` is never exposed over HTTP. B10 implementation remains outside this plan.

## Review Focus
1. Wrong tenant dispatch or allowlist mismatch: verify exact route arguments and matching environment/settings (Task 1).
2. Switching with a selected run/evidence: no old graph, claims, trace, or apply binding survives (Task 2).
3. Pending old response after switching: destination state stays unchanged (Task 2).
4. Identical run keys in separate graphs: recall/evidence stays local; foreign-only run returns 404 (Task 3).
5. Sparse Markdown, bare tenant route, or unavailable data: show honest empty/error state, never another tenant's fixtures (Task 2 and acceptance).

## Existing interfaces, boundaries, and conflicts
- `ingest.fetch.main(argv: list[str] | None = None) -> None`: `--tenant pallets`; cache `data/raw/pallets/_cache/`, repo directory `pallets__flask`, cutoff persisted in `manifest.json`. No `--refresh`.
- `ingest.load.main(argv: list[str] | None = None) -> None`: raw path uses CLI tenant, graph uses YAML `slug`; pin both to `pallets`.
- `api.db.graph(tenant: str)` selects `tenant_<tenant>`; `memory.list_runs(tenant: str, limit: int) -> list[RunSummary]`; `api.tools.recall_run(tenant: str, run_key: str) -> ToolResult` takes `run:<uuid>`, while HTTP recall takes bare UUID.
- Routes use `Settings.tenants`; tools separately read process `TENANTS`. Configure both consistently; do not bypass either check.
- `loadWorkspaceData(tenant, issueKey, runKey?)` returns existing `WorkspaceData`; `Header` already pushes `/t/<slug>` without query parameters. `Workspace(data: WorkspaceData)` currently initializes local state from props.
- **Bare-route limitation:** `/t/pallets` intentionally has an empty canvas plus local stats; select an issue to demonstrate a different graph. Do not add a graph overview to satisfy ambiguous “switch shows graph” wording.
- Flask docs may be `.rst`; Markdown results may be sparse/empty. Keep spec globs; do not add RST support or a decisions job.
- Current live Dry run control is disabled/on and public live apply is refused, stricter than the spec's switch wording. Preserve safety; flag this conflict rather than changing policy.
- Current stats omit last ingest/proof/latency values; README's “A10/A11 absent” status is stale relative to code. Neither discrepancy is repaired here; no fabricated Pallets metrics.
- A owns ingestion code, tools, memory, agent routes, and product Cypher. Only the explicitly approved new YAML crosses that directory boundary. Stop and send A the exact failing existing signature above, command/test, and sanitized error; do not repair A code implicitly.

## Files
- Create `ingest/tenants/pallets.yml` and `tests/test_second_tenant.py` (config + small disposable run-isolation test).
- Modify `tests/test_graph_routes.py` (reuse `FakeGraphReads`/`live_client`); no new API implementation.
- Modify `web/app/t/[tenant]/page.tsx`, `web/tests/fake-api.ts`, `web/tests/api-data.spec.ts` (tenant remount and targeted existing-suite tests).
- Modify `.env.example` and `README.md` only for optional activation/manual commands; keep safe single-tenant/fixture defaults.
- No new acceptance runner, snapshot helpers, test Cypher, polling UI, shared contracts, or A-owned product edits.

### Task 1: Config and existing dispatch regression (10 minutes)
**Interfaces:** Consume YAML through existing fetch/load CLIs and `live_client(reader, tenants=(...))` in `tests/test_graph_routes.py`; produce only approved config and regression coverage.
- [ ] **1. Add `test_pallets_config` in `tests/test_second_tenant.py`.** Parse both YAML files; assert exact mapping and disjoint case-normalized repository sets:
  ```python
  assert cfg == dict(slug="pallets", org="pallets", repos=["pallets/flask"], since_days=365,
                     docs_globs=["README.md", "docs/**/*.md", "*.md"], max_pr_files=300)
  assert not ({r.lower() for r in cfg["repos"]} & {r.lower() for r in primary["repos"]})
  ```
- [ ] **2. Run `pytest -q tests/test_second_tenant.py -k config`.** Expected red: missing `pallets.yml`.
- [ ] **3. Add `pallets.yml` with precisely the asserted mapping.** Add an optional `TENANTS=falkordb,pallets` comment in `.env.example`, retaining `TENANTS=falkordb` default. Do not modify source tenant config.
- [ ] **4. Add `test_second_tenant_dispatch` to `tests/test_graph_routes.py`.** Reuse the existing fake reader/live client, set process `TENANTS=falkordb,pallets`, then assert:
  ```python
  assert {t["slug"] for t in client.get("/tenants").json()["tenants"]} == {"falkordb", "pallets"}
  assert client.get("/tenants/pallets/stats").status_code == 200
  assert reader.calls[-1] == ("stats", "pallets")
  assert client.get("/tenants/unknown/stats").status_code == 404
  ```
  Also request subgraph/path for each slug and assert exact tenant arguments using existing reader call tuples. These checks may already pass; do not manufacture failures or edit working API code.
- [ ] **5. Run `pytest -q tests/test_second_tenant.py -k config` and `pytest -q tests/test_graph_routes.py`.** Expected pass; implementation-time scoped commit: `B9: configure Pallets tenant`.

### Task 2: Tenant remount and targeted browser regression (20 minutes)
**Interfaces:** Keep `Workspace(data: WorkspaceData)` and existing Header navigation; use existing fake API/Playwright config and AbortSignal guards. Produce only tenant-keyed React identity.
- [ ] **1. Extend `web/tests/fake-api.ts` minimally.** Preserve Acme payloads; add Pallets tenant with distinct stats and one Flask issue. Reuse the existing 1,200 ms slow-subgraph response; no new fake run builder/server framework.
- [ ] **2. Add one `tenant switch clears selected run and pending graph` test to `web/tests/api-data.spec.ts`.** Open Acme's existing `KNOWN_RUN`, select an evidence citation, start loading `issue:Acme/slow`, then switch through the existing Tenant select to Pallets. Check after the slow response has completed:
  ```typescript
  await expect(page).toHaveURL(/\/t\/pallets$/);
  await expect(page.getByLabel('Issue key')).toHaveValue('');
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
  await expect(page.locator('[data-action-status]')).toHaveCount(0);
  await expect(page.locator('.canvas-counts')).toContainText('Issue: 3');
  ```
  Set Pallets fake Issue count to `3`; assert no Acme claim/trace/evidence remains. Load the Flask issue and verify its anchor, then switch back without preserving query parameters. Assert disabled/on Dry run before switching; do not attempt unsupported dry-run-off interaction.
- [ ] **3. Add `destination outage never reuses source data`.** Use the existing fake API outage switch after source page loads, navigate to Pallets, then assert `expect(page.locator('[data-live-error]')).toContainText('Live data unavailable')` and no old run/graph/fixture success. Existing unknown-tenant test remains the 404 check.
- [ ] **4. Run `npm --prefix web run test:e2e:api -- api-data.spec.ts`.** Expected state-reset regression fails if React preserves the workspace; other existing protections may already pass.
- [ ] **5. Read the installed Next.js routing guide required by `web/AGENTS.md`, then change the existing page render to `<Workspace key={data.tenant} {...data} />`.** Reuse unmount abort/cancel cleanup; no global store, new component, or loader signature.
- [ ] **6. Run `npm --prefix web run typecheck` and `npm --prefix web run test:e2e:api`.** Expected pass including existing apply/run-link tests; implementation-time scoped commit: `B9: isolate workspace on tenant navigation`.

### Task 3: Minimal real run isolation regression (10 minutes)
**Files:** Extend only `tests/test_second_tenant.py`. **Interfaces:** Reuse `tests/cypher/recall_fixture.cypher`, `api.schemas.TriageResult`, `memory.list_runs`, `recall_run`, and live TestClient; no A11 feedback/ranking re-test.
- [ ] **1. Add `test_runs_stay_in_their_graph` using two newly generated `b9test_<uuid>_a/b` slugs.** Set matching `TENANTS` and live settings. Following existing graph-test conventions, seed minimal Issue/Person/Module nodes with parameterized trivial writes; execute existing `recall_fixture.cypher` with distinct issue/evidence and valid stored triage JSON. Use the same valid run UUID in both graphs plus one A-only UUID. Assert:
  ```python
  assert recall_run(a, shared).data["run"]["issue_key"] == issue_a
  assert recall_run(b, shared).data["run"]["issue_key"] == issue_b
  assert issue_a not in recall_run(b, shared).evidence
  assert recall_run(b, only_a).empty is True
  assert {r.run_key for r in list_runs(b, 20)} == {shared}
  assert client.get(f"/tenants/{b}/runs/{only_a.removeprefix('run:')}").status_code == 404
  ```
  Before teardown, assert graph names are the newly owned `tenant_b9test_*` names; delete only those two in `finally`. Never reuse/delete `tenant_falkordb` or `tenant_pallets`. Skip this live test if DB unavailable, not offline config tests.
- [ ] **2. Run `pytest -q tests/test_second_tenant.py tests/test_agent_routes.py`.** Expected existing behavior passes; a real non-skipped isolation result is required for B9 acceptance. Failure inside memory/recall goes to A, not a B9 product fix.
- [ ] **3. Implementation-time scoped commit:** `B9: verify graph-local run recall`. No commits occur while drafting this plan.

## Manual acceptance and README handoff (remaining 20 minutes; stop if ingestion overruns)
- [ ] Confirm no concurrent source-tenant writer, configured read-only GitHub credentials, existing FalkorDB data, and no source edits via `git diff -- ingest/tenants/falkordb.yml`. Do not run a FalkorDB reload/reset.
- [ ] Run `python -m ingest.fetch --tenant pallets` twice. Second output must say `network requests: 0, cache hits: ...`; inspect Pallets manifest for exact tenant/repos/365-day window and the existing repo JSON files. Missing CODEOWNERS note or zero Markdown docs is acceptable; no ingestion expansion.
- [ ] Run `python -m ingest.load --tenant pallets`; require `loaded tenant_pallets`, positive Repo/PR/Issue counts, and existing embedding/vector sanity output. Do not run decisions/proof/bench just for B9.
- [ ] Set API/tools `TENANTS=falkordb,pallets`, `API_MODE=live`; web `NEXT_PUBLIC_DATA_SOURCE=api` and existing API base URL. Restart/rebuild as needed; keep fixture defaults unchanged in the template.
- [ ] Read-only HTTP checks: `curl -fsS "$API/tenants"`, then `curl -fsS "$API/tenants/pallets/stats"` and the FalkorDB equivalent. Select real issue keys from each raw dataset; use `curl -fsSG "$API/tenants/pallets/subgraph" --data-urlencode 'keys=["<actual Flask issue key>"]'`, then query that key under FalkorDB and require empty nodes/edges. Reverse the test with a real FalkorDB issue; cross-graph `/path?from=&to=` must be empty.
- [ ] Read `/tenants/{tenant}/runs` for both; any existing run must recall only in its owning graph. Empty real lists need Task 3's non-skipped isolation proof, not invented runs. No persistent graph writes to make this acceptance pass.
- [ ] In the UI load the real source issue, switch to Pallets (clean URL, empty canvas, local counts), then load the Flask key and show its different graph. Switch back; no Flask claims/evidence/run may persist. This is the honest B9 “different graph” demonstration.
- [ ] Add the optional activation commands and actual results to README; no fabricated metrics. Run `pytest -q`, `npm --prefix web test`, and Task 2 checks; distinguish baseline failures/skips. Owner alone ticks B9. If the hour expires, leave partial Pallets data unadvertised in runtime `TENANTS` and return to B11/B12 without deleting it.

## Self-review and handoff
Spec coverage: config, reused ingest, graph/run isolation, switcher, and non-overlapping repos are covered; B10/B11, A11 rules, and metrics remain outside B9. Steps use existing signatures and exact spec values; tests include runnable assertions rather than new infrastructure. Review-focus cases map to the three tasks/manual checks. No product choices change the spec; the bare-route and locked-dry-run discrepancies are explicit. Timebox is 40 minutes implementation/tests plus up to 20 minutes manual acceptance, with ingestion an honest cut gate. Planning only: no live commands, implementation, graph updates, or git commits executed. Parent reviews before execution.
