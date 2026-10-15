# B4 — Wire the real graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve real tenant-scoped graph data and show real issue neighbourhoods and counts in the deployed UI without breaking fixture mode.

**Architecture:** Keep the current FastAPI models and routes, adding an injectable graph-read adapter over A's query infrastructure. Select fixture or HTTP web data through one flag. Allow live graph exploration before real triage exists; never silently substitute synthetic claims when the API is selected.

**Tech Stack:** Python 3.11+, FastAPI, FalkorDB Python client through A's adapter; Next.js App Router, TypeScript, existing Playwright tests.

**Spec:** `docs/spec.md` §§2, 3, 5–6, 9, 11; `docs/tasks.md` B4; `2026-10-15-b4-b6-review.md` for proposed decisions and handoff gates.

## Global Constraints

- “One FalkorDB graph per org, named `tenant_<slug>`.”
- “Every other query lives in `cypher/` too”; no inline Cypher or Python graph traversal to calculate product paths.
- “Stable keys, not internal ids.”
- `/subgraph`: keys plus one-hop neighbours, capped at **150 nodes**; no dangling edges.
- `/path`: evidence-only shortest path, at most **4 hops**, wrapped as `ToolResult`.
- “CORS allows the Vercel domain and localhost only.”
- Preserve shared `api/schemas.py`, `web/lib/types.ts`, and fixtures; changes require both owners' agreement.
- Work only in B's lane. Missing A queries/adapters are handoff blockers, not permission to implement A5.

## Review Focus

1. Issue keys containing `/`, `#`, spaces or commas survive URL transport without changing identity.
2. Unknown tenants cannot select a graph or reach the database.
3. Empty or oversized neighbourhoods return valid capped graphs, never phantom data or dangling edges.
4. Live dependency failures show an error, never a successful fixture fallback.
5. API-selected production pages do not freeze counts at build time or require fixture files at runtime.

---

## File map and prerequisites

- Modify `api/config.py`, `api/main.py`, `api/routes/graph.py`: live settings, injectable reader lifecycle/dependency, graph route dispatch.
- Create `api/graph_reads.py`: B-owned response adapter; no connection/query implementation duplicated from A.
- Create `tests/test_graph_routes.py`: fake reader and tenant/input/error tests.
- Modify `web/lib/workspace-data.ts`: UI-local discriminated fixture/live data, including `issueKey: string | null`, optional live triage and `source`.
- Create `web/lib/api-client.ts`: browser-safe typed HTTP reads; `web/lib/data-source.ts`: server data selection.
- Modify `web/app/page.tsx`, `web/app/t/[tenant]/page.tsx`, `web/components/workspace.tsx`, `web/components/header.tsx`, `web/components/canvas-placeholder.tsx`, `web/components/trace-panel.tsx`, `web/components/triage-card.tsx`: data boundary, issue selection, truthful source labels and counts.
- Modify `.env.example`, `README.md`, `web/playwright.config.ts`; create `web/tests/api-data.spec.ts`.
- A must supply named graph queries and graph/tool execution. Request signatures in Task 1; do not create A-owned query/tool files in B4.

### Task 1: Live graph route adapter (~45 min)

**Interfaces:** B's `GraphReads` protocol in `api/graph_reads.py` exposes `subgraph(tenant: str, keys: list[str]) -> Subgraph`, `path(tenant: str, from_key: str, to_key: str) -> ToolResult`, `stats(tenant: str) -> Stats`. `get_graph_reads(request: Request) -> GraphReads` reads injected app state. Extend `create_app(..., graph_reads: GraphReads | None = None)` without breaking existing arguments. Concrete calls into A's implementation must be agreed first.

- [ ] **Step 1: Write failing tests** in `tests/test_graph_routes.py`: `test_live_routes_use_selected_tenant_and_reader` asserts all three methods see the configured tenant and actual query inputs, not fixtures; `test_unknown_tenant_never_calls_reader` expects 404 and zero reader calls; `test_live_reader_error_is_503_without_fixture_fallback` expects a sanitized 503. Keep existing `tests/test_api_shell.py` fixture expectations unchanged.
- [ ] **Step 2: Run** `python -m pytest tests/test_graph_routes.py -q`; expect failures for missing live settings/adapter injection, not unrelated collection errors.
- [ ] **Step 3: Implement** the protocol/adapter and route dispatch. `load_settings()` accepts exactly `fixtures|live`; invalid values fail startup. Live calls use A's selected tenant graph and named queries. Reader construction must not connect in fixture mode; initialize/close the real client through app lifecycle. Use existing query allowlists, alias names, and response models; sanitize backend errors. Leave agent routes fixture-labelled until A owns the live implementation.
- [ ] **Step 4: Add boundary tests**: `test_encoded_keys_round_trip` passes a key containing `#` and `/` through HTTP query encoding; `test_blank_keys_rejected` expects 422 before graph access; `test_subgraph_cap_and_endpoints` asserts `len(nodes) <= 150` and both edge endpoints exist for >150 candidates; `test_empty_path_and_subgraph` expects empty valid models; `test_stats_counts_are_not_double_counted_by_node_label` excludes the auxiliary `Node` label from kind totals. Ask A to test cap/seed retention in Cypher, not just slice returned nodes in Python.
- [ ] **Step 5: Pin the key-list encoding**: use a JSON array string in the existing `keys` query parameter in live mode (`keys=["issue:owner/repo#1"]`); accept the existing single-key form for compatibility. This keeps comma-bearing keys unambiguous without a shared response-contract change. Reject malformed arrays, blank elements or more than 150 distinct seed keys with 422; deduplicate stably. Test a comma-bearing key and duplicates.
- [ ] **Step 6: Map `Stats`** from A's counts query: `node_counts`, `edge_counts`, confirmed ingest timestamp or null, `proof: null`, `latencies: []` until A13/A14/B11. Existing optional values must not be populated from fixture stats. Ensure A's subgraph query selects seeds before neighbours with stable ordering, returns only selected-node edges, and supplies JSON-safe properties without embeddings/internal IDs.
- [ ] **Step 7: Verify and commit**: `python -m pytest tests/test_graph_routes.py tests/test_api_shell.py tests/test_contracts.py -q` → all pass. Stage the named API/test files; commit `B4: wire tenant-scoped graph read routes`.

### Task 2: One fixture/API switch and real graph view (~40 min)

**Interfaces:** `apiGet<T>(path: string, signal?: AbortSignal): Promise<T>` in `web/lib/api-client.ts`, using `NEXT_PUBLIC_API_BASE_URL` and URL/URLSearchParams, finite timeout and no cached live reads. `loadTenants(): Promise<TenantSummary[]>` and `loadWorkspaceData(tenant: string, issueKey: string | null): Promise<WorkspaceData | null>` in `web/lib/data-source.ts`. Add `getPath(tenant: string, fromKey: string, toKey: string, signal?: AbortSignal): Promise<ToolResult<PathData>>` and `getSubgraph(tenant: string, keys: string[], signal?: AbortSignal): Promise<Subgraph>` for B5. UI data is not a replacement for shared wire contracts.

- [ ] **Step 1: Write failing Playwright tests** in `web/tests/api-data.spec.ts`: `api mode shows supplied counts and selected real issue`, `API outage never displays fixture success`, `unknown API tenant is 404`, `changing issue discards stale graph response`, and `reserved characters survive issue selection`. Serve a deterministic fake API on a separate local port using Node's built-in HTTP server; server-side Next requests cannot be intercepted with `page.route`. Keep the test API read-only.
- [ ] **Step 2: Run** `npm run test:e2e -- tests/api-data.spec.ts` from `web/` with API-mode test-server configuration; expect mode-specific failures before implementation. Keep the default fixture suite separately configured to avoid mode leakage.
- [ ] **Step 3: Implement** data selection with `NEXT_PUBLIC_DATA_SOURCE=fixtures|api` (default fixtures, invalid value is an error). The fixture branch delegates to existing `fixture-data.ts` unchanged. Live mode fetches `/tenants`, `/stats` and `/subgraph`; no synthetic triage/apply results. Until real runs exist, show an explicit “No triage run selected” state, an issue-key form and a real node/edge summary. Use the `?issue=` key as the anchor. Unknown tenants return 404; missing issue is an input state, not an exception.
- [ ] **Step 4: Make both root and tenant routes dynamic for API reads**, removing fixture-only `dynamicParams=false`/static enumeration in the live path; follow installed Next 16 documentation before coding. Use no-store requests, visible loading/errors, and cancellation/request identity to prevent old issue/tenant results replacing new ones. No new client state/fetching library. Fetch graph for the issue plus real citations once real triage is available; source issue identity comes from the selected issue/run, not from `triage.run_key`.
- [ ] **Step 5: Expose live counts** beside the canvas and retain useful counts after B5 replaces it. Label fixture/live and unavailable agent capability honestly in header/card/trace. Preserve dark/light, mobile stacking and trace-copy behavior. Document backend mode, frontend flag, API origin/CORS and `?issue=` usage in `.env.example`/README.
- [ ] **Step 6: Verify and commit**: from `web/`, run `npm run typecheck`, `npm run test:empty`, both fixture/API Playwright configurations, and `npm run build` → all pass. Build API mode against a reachable test API, then change its counts and verify runtime output changes without rebuilding. Commit `B4: switch graph workspace between fixtures and API`.

### Task 3: Real-data deployed acceptance (~15 min, infrastructure permitting)

**Consumes:** A-loaded `tenant_falkordb`, A-verified issue key, deployed API and Vercel environment access. A fake API does not satisfy this gate.

- [ ] Enable `API_MODE=live` on the API and `NEXT_PUBLIC_DATA_SOURCE=api` with the public API URL on Vercel; confirm exact Vercel origin in CORS. No tokens/passwords enter frontend env.
- [ ] Request `GET /tenants/falkordb/stats` and `/subgraph` with the real key, validating responses as `Stats`/`Subgraph`. Compare counts and issue identity against A's fresh database output, not against constants in a test.
- [ ] Open deployed `/t/falkordb?issue=<encoded key>`; retain URL, screenshot, response bodies and count comparison in the task review. Expected: real issue node and non-fixture counts, no “Fixture geometry” label. If queries, data or deployment access are missing, record B4 as blocked—not complete.
- [ ] Run the backend suite, then `graphify update .` after code changes. Review only intended file changes before committing `B4: document live graph acceptance`.

## Explicit deferrals

Force canvas belongs to B5; replay/apply to B6; agent/run implementation to A10; polling and Proof/latency visuals to B11. Do not tick B4 until the deployed Done when check is shown to its owner.
