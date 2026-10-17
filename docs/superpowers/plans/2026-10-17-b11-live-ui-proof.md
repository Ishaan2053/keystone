# B11 Live UI and Proof Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface persisted webhook/UI runs without reload within 10 seconds of their becoming available through `/runs`, and display real A13/A14 Proof and latency results from `/stats`.

**Architecture:** Extend the existing live workspace with tenant-scoped client polling and guarded run selection. Enrich the B-owned stats reader with validated on-disk artifacts, then use the existing Sheet component for a Proof panel. Polling refreshes data, not the entire workspace; run selection commits triage, trace, issue anchor and graph together.

**Tech Stack:** Existing Next.js 16 App Router, React, TypeScript, Tailwind/shadcn, FastAPI and Pydantic. No new dependencies; grouped bars use HTML/CSS and existing chart tokens.

**Spec:** `docs/tasks.md` B11; `docs/spec.md` sections 9, 11 and 14; `design/README.md`; `design/tokens.json`. Read the installed Next.js guides under `web/node_modules/next/dist/docs/` before implementation.

## Global Constraints

- Planning only in this change: no product implementation, dependency installation, deployment or task checkbox updates.
- B owns `web/`, `api/graph_reads.py` and graph routes. Do not edit A-owned `api/routes/agent.py`, `api/memory.py`, `ingest/proof.py`, `ingest/bench.py` or `cypher/`.
- Shared contracts (`api/schemas.py`, `web/lib/types.ts`, `api/fixtures/`) require both developers' agreement. This plan does not change them.
- One FalkorDB graph per tenant; every request, selection and polling lifecycle is scoped to the current tenant.
- Stable keys, never internal ids. Preserve `run:<uuid>` in state and bare UUIDs in route paths using `runKeyUuid()`.
- Live data failures never fall back to fixtures. Missing measurements are not zero; measured zero is a valid result.
- Poll `/tenants/{t}/runs?limit=20` every 5 seconds; newest first. Do not infer queued/running/failed jobs from persisted AgentRuns.
- Preserve sandbox-only writes, dry run on by default, existing Apply refusal behavior and evidence selection semantics. Polling and run selection perform GETs only.
- Preserve granite/amber identity, Instrument Sans/JetBrains Mono, dark default and existing responsive three-pane layout.
- Graph = `chart-1`, CODEOWNERS = `chart-2`, similar-issues = `chart-3`. Show actual values even when the graph loses.
- Start execution on a B11 branch/worktree through the repository workflow. Do not commit unrelated work or tick B11 on behalf of the owner.

## Interview decisions

- User approved: keep the current run open when another arrives; prepend new runs with a “New” indicator. Automatically select the newest only when no run is selected.
- User approved: show all four existing rates: reviewer hit@3, module hit@1, module hit@3 and label precision@2. These are four rates across three metric families; this resolves the spec's “three metrics”/“9 rates” ambiguity without changing the contract.
- User approved: explicit “Not measured yet” states; refresh `/stats` when the Proof panel opens and while it remains open.
- User approved: include the small B-owned `/stats` artifact wiring, while A retains metric-generation ownership.

## Builder calls (not additional user requirements)

- Refresh open-panel stats every 5 seconds; stop its interval on close. Fetch stats once at workspace entry (existing behavior) and again on opening. The header uses the latest successful stats snapshot.
- Show run list status “Recorded” or “Recorded · fallback”, based on `fallback`. Applied/refused/dry-run statuses belong to recalled actions, not the list contract.
- A successful manual issue load counts as an intentional selection: suspend automatic newest-run selection until tenant navigation/reset, preventing polling from replacing the chosen graph.
- Mark runs arriving after the first successful list snapshot as “New”; initial history is not new. Clear a run's marker when successfully selected; markers are session-local.
- Use the existing right-hand Sheet for Proof, with an internally scrolling body; keep chart values readable at phone width through four vertically arranged metric groups.
- Proof with `n=0` means no evaluable sample: show `n = 0` and an explanatory empty state, not a headline percentage. Latencies remain independently visible.
- Keep last successful list/stats during transient errors, explicitly marked as stale. First-load errors show unavailable/retry, not an empty-success state.

## Review Focus

1. Late responses after run or tenant changes must never mix triage, graph, trace or Apply targets (Task 2 tests).
2. Slow/failing polling must not overlap requests, clear valid data or interrupt inspection (Task 2 tests).
3. Missing, partially written or malformed metric artifacts must not break graph counts or masquerade as measured zeros (Task 1 tests).
4. Invalid/wrong-tenant run links and manual issue selection must not be silently replaced by auto-selection (Task 2 tests).
5. Zero values, graph losses, long labels and keyboard/phone usage must remain honest and usable (Task 3 tests).

## Existing seams and file map

Current `/runs` already returns `RunsResponse`; recall returns `ToolResult<RecallRunData>`, including stored triage. Deep links are already resolved in `web/lib/data-source.ts`. `LiveWorkspace` currently derives its run from initial props and must gain mutable selected-run state. `Header` currently hardcodes “Proof not measured”. `LiveGraphReads.stats()` currently hardcodes empty metrics through `build_stats()`.

- Create `api/metric_artifacts.py`: independently validate/read A13 Proof and A14 benchmark latency artifacts.
- Modify `api/graph_reads.py`: enrich fresh graph counts with artifact values.
- Create `tests/test_metric_artifacts.py`; extend `tests/test_live_graph_routes.py` for route-level wiring.
- Modify `web/lib/api-client.ts`: typed list read, reusing request budget and cancellation.
- Create `web/components/live-runs.tsx`: tenant polling, list state, “New” markers and selection callback.
- Modify `web/components/workspace.tsx`: selected-run transitions, coherent pane state and Proof refresh ownership.
- Create `web/components/proof-panel.tsx`: accessible grouped bars, exact numeric values, sample size and latency table.
- Modify `web/components/header.tsx`: clickable real reviewer headline and Proof trigger.
- Modify `web/app/globals.css`: scoped list, chart, panel and stale-state styling using existing tokens.
- Extend `web/tests/fake-api.ts`; create `web/tests/live-runs.spec.ts`, `web/tests/proof.spec.ts`; register both in `web/playwright.api.config.ts`.
- Modify `web/components/triage-card.tsx` only as needed to place the list above the issue input via an optional `runsList?: React.ReactNode` prop on `LiveTriageCard`.

## Task 1: Expose real metric artifacts through `/stats` (about 20 minutes)

**Interfaces:** Create `read_metric_artifacts(tenant: str, data_dir: Path) -> tuple[schemas.Proof | None, list[schemas.ToolLatency]]` in `api/metric_artifacts.py`. `data_dir` defaults at the caller to repository-root `data/`, derived from `__file__`, not the process working directory. Consume existing schemas unchanged.

- [ ] Write `tests/test_metric_artifacts.py` against temporary directories: exact Proof round-trip for all three methods/four rates, latency extraction from `{latencies: [...]}`, extra benchmark metadata accepted, tenant-isolated paths, no files => `(None, [])`, one file missing leaves the other usable, invalid JSON/schema/unreadable file => only that artifact unavailable. Include `n=0` and valid zero rates; preserve graph-losing values unchanged.
- [ ] Run `pytest -q tests/test_metric_artifacts.py`; expect failure because the new reader is absent.
- [ ] Implement the reader. Read `data/proof/<tenant>.json` as `Proof`; read `data/bench/<tenant>.json` and validate its `latencies` as `ToolLatency` objects. Validate tenant against a safe slug pattern before forming paths; no traversal. Catch expected filesystem/JSON/validation failures separately per artifact and log a concise warning, never artifact contents. Read on every request; no process-lifetime memoization that would hide A's newly written files.
- [ ] Extend `LiveGraphReads.stats(tenant)` to call the reader and enrich the existing `Stats` result (e.g. model copy). Keep counts from fresh `stats.cypher`, never from benchmark snapshots. Keep `build_stats()` and fixture mode behavior compatible.
- [ ] Add route regression assertions in `tests/test_live_graph_routes.py`: files become visible on the next stats request without restart; counts survive missing/corrupt artifacts; invalid tenant is rejected before artifact access. Do not require real FalkorDB for these unit/route tests.
- [ ] Run `pytest -q tests/test_metric_artifacts.py tests/test_live_graph_routes.py tests/test_graph_routes.py`; expect all passing.
- [ ] Commit only this task's files with `B11: expose Proof and benchmark artifacts in stats`.

## Task 2: Live list and coherent selected-run loading (about 45 minutes)

**Interfaces:** Add `getRuns(tenant: string, limit = 20, signal?: AbortSignal): Promise<RunsResponse>` to `web/lib/api-client.ts`. Create `LiveRuns({tenant, selectedRunKey, autoSelect, onSelect}: {tenant: string; selectedRunKey: string | null; autoSelect: boolean; onSelect: (runKey: string) => Promise<boolean>})`. `LiveWorkspace` owns selected `RecallRunData`, graph, issue and selection lifecycle; initialize them from the server-resolved deep link.

- [ ] Extend the fake API with mutable per-tenant run lists and recall bodies, delayed responses and independent list outages; default existing tests to an empty list so auto-selection does not alter their scenarios. Retain request logging and existing known-run behavior.
- [ ] Add/register `live-runs.spec.ts`. Tests assert: immediate initial fetch; newest-first/deduplicated list capped at 20; arrival visible within 10,000 ms with no document reload; stable selected card/trace/graph after arrival; “New” marker; empty-view auto-select; no auto-select over explicit invalid run link or manually loaded issue; switching runs updates all panes and URL; rapid A/B selections ignore delayed A; tenant navigation cannot leak old results; transient outage preserves list/selection and recovers.
- [ ] Run `npm run test:e2e:api -- live-runs.spec.ts` from `web/`; expect failures for missing list/polling.
- [ ] Implement `getRuns()` through `apiGet`, encoded tenant and `limit=20`. Implement immediate list read plus a fixed 5,000 ms interval that skips ticks while a request is in flight. Abort and clear timers on unmount/tenant change. Do not use `router.refresh()` or remount the graph every tick. Retry on subsequent ticks after a failure.
- [ ] Render a compact accessible list above the issue input, with issue key/number, timestamp (epoch seconds converted to milliseconds), status word, selected state and “New” marker. A list-item button selects the run; do not fetch every historical run just to enrich its status. “No runs yet” is distinct from unavailable/stale. Use polite announcements only for meaningful new arrivals, not every poll.
- [ ] Implement `selectRun(runKey: string): Promise<boolean>` inside `LiveWorkspace`: guard with monotonically increasing request id and `AbortController`; fetch `getRun()`, reject `empty`/missing data and mismatched run or triage identity, then fetch the recalled issue's subgraph. Commit selected run, issue key/input, triage/trace, graph and cleared evidence selection together only after successful reads. A failed load keeps the prior coherent view and exposes a selection error; never bind Apply to a pending run.
- [ ] Cancel stale path/issue requests at selection transitions; existing request guards must cover cross-operation races, not just two run reads. Reset Apply-local state and dry-run switch to on when selected run changes (key the triage card by selected run if appropriate). Keep the graph component mounted across list-only updates.
- [ ] On selection success use the existing `?run=<uuid>` convention and remove conflicting `?issue=`. For reload/back/forward, reconcile incoming server props and URL changes with selected state; do not leave state initialized to an earlier run forever. A malformed/unknown explicit run remains an error, not an auto-selected substitute. Selecting manually from the list is allowed recovery.
- [ ] Run `npm run test:e2e:api -- live-runs.spec.ts run-link.spec.ts apply.spec.ts api-data.spec.ts`; expect passing tests, no unexpected POST from polling/selection and existing manual issue behavior preserved.
- [ ] Commit this task's files with `B11: poll recent runs and preserve selected evidence`.

## Task 3: Measured Proof headline and refreshing panel (about 35 minutes)

**Interfaces:** Create `ProofPanel({open, onOpenChange, stats, loading, stale, error, onRetry}: {open: boolean; onOpenChange: (open: boolean) => void; stats: Stats | null; loading: boolean; stale: boolean; error: string | null; onRetry: () => void})`. Extend Header with optional `stats?: Stats | null` and `onOpenProof?: () => void`, keeping existing callers valid. `LiveWorkspace` owns refreshed stats; use that same snapshot for header, panel and canvas counts.

- [ ] Extend fake stats state to accept the shared `Stats` type, distinct tenant measurements, absent/available artifacts and stats-only errors. Add/register `proof.spec.ts` with a deterministic sample (`n=10`, graph reviewer rate `0.6`, CODEOWNERS `0.7`, similar-issues `0.4`; all four rates and latency values populated).
- [ ] Test header shows 60% reviewer hit@3; opening exposes 12 method/rate values and `n=10`; the graph loss remains visible; p50/p95 keep `ms` units. Test a measured 0% stays 0%, null/n=0 Proof does not look measured, latencies can render without Proof, and missing latencies have their own empty state.
- [ ] Test opening immediately fetches stats, changed results appear after 5,000 ms while open, closing stops panel polling, and transient errors retain visibly stale measurements with Retry. Add keyboard open/Escape/focus-return and phone-width overflow checks with long tool labels; repeat visual assertions in light and dark themes.
- [ ] Run `npm run test:e2e:api -- proof.spec.ts` from `web/`; expect failures for missing panel/headline.
- [ ] Implement the header trigger: `metric` styling only for the graph reviewer hit@3 when `proof.n > 0`; otherwise “Proof not measured”. Make both states clickable so users can inspect available latencies. Do not introduce claims that the graph wins.
- [ ] Implement the Sheet using existing accessible primitives. Four labelled groups each show three horizontal bars on a shared 0–100% scale, legend in graph/CODEOWNERS/similar-issues order, values to one decimal percentage point (omit trailing `.0`), sample size below, and a semantic latency table with Tool / p50 (ms) / p95 (ms). Exact values must be readable without color or hover; provide accessible text/table equivalents. No chart animation or new chart dependency.
- [ ] Implement immediate-on-open and every-5,000-ms stats reads with single-flight, cancellation and tenant guards. Closing aborts only panel-owned refreshes; stale errors never replace good snapshots with empty data. Provide Retry. Fixture workspace may show its existing labelled fixture stats through the same panel, but must never poll the live API or relabel synthetic results as measured live Proof.
- [ ] Add scoped CSS, preserving phone stacking and existing trace drawer. Do not change graph styling or token definitions. Run the Impeccable detector once over changed UI files during execution, not during planning.
- [ ] Run `npm run typecheck` and `npm run test:e2e:api -- proof.spec.ts live-runs.spec.ts`; expect passing results.
- [ ] Commit this task's files with `B11: show measured Proof comparisons and tool latencies`.

## Task 4: Integration acceptance and owner handoff (about 15 minutes)

- [ ] Run `pytest -q tests/test_metric_artifacts.py tests/test_live_graph_routes.py tests/test_graph_routes.py`, then from `web/` run `npm run typecheck`, `npm run test:e2e:api`, `npm test` and `npm run build`. Record commands/output and distinguish unrelated environment failures from regressions. Do not claim any of these passed from this planning document.
- [ ] Run `graphify update .` after product code changes, per repository instructions; investigate generated changes before committing them. No index creation/rebuild is authorized by this plan.
- [ ] With API/web in live mode and real A13/A14 artifacts available, compare `/tenants/falkordb/stats` to those files: `n`, all 12 method/rate values and tool p50/p95 must match. Existing spec shorthand “9 rates” must not lead to dropping module hit@1 or hit@3.
- [ ] Keep run A open in the UI; create a sandbox replay/issue through the established B7 workflow; record when run B first appears in `/runs` and when the UI displays it. Assert UI latency <=10 seconds without reload and run A remains selected. Select B and verify triage, trace, graph evidence and Apply target all belong to B.
- [ ] Repeat from an empty tenant view: the first available run should open automatically. Open Proof and confirm refreshed real measurements, sample size and latency table. Smoke-test on phone and both themes; record output/screenshots in the handoff, not fabricated fixture acceptance.
- [ ] If the product requirement is interpreted as <=10 seconds from GitHub issue creation rather than from run availability, record the end-to-end duration separately: B11 cannot guarantee A10/B7 processing time before an AgentRun exists. Report the dependency rather than inventing queue statuses.
- [ ] Ask the owner to run/accept the B11 Done when check and tick it themselves. If real artifacts/webhook access are unavailable, mark live acceptance blocked with the exact prerequisite; automated fake-API tests do not substitute for it.

## Boundaries and execution readiness

- Intended implementation budget: about 115 minutes. If integration exceeds two hours, stop and propose a smaller version; do not sacrifice trace correctness, Proof honesty or deploy time.
- No SSE/WebSockets, new backend job/status model, UI redesign, A13/A14 reruns, per-issue Proof explorer, user auth, new Rule controls or deployment changes.
- A13 writes a `Proof` model JSON; A14 writes a benchmark object containing `latencies` plus metadata. Both formats were verified against the current producer source. If those formats change, agree the handoff rather than changing shared contracts unilaterally.
- Planning self-review: covers B11 plus its B-owned stats prerequisite; interfaces retain existing wire shapes; each task carries a failing-test/pass cycle; all five review-focus risks have explicit tests. Product tests and live acceptance remain unrun because this deliverable is a plan only.
