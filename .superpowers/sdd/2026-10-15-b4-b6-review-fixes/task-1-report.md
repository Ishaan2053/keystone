# Task 1 report — fetch complete evidence path context

## Outcome and scope

Implemented Task 1 on `feat/graph-canvas-sandbox-actions`, based on `bf3b5befefddba7d53683596974408db4147aec4`. No live triage wiring, backend changes, real GitHub requests, font-tooling changes, pushes, merges, or subagent dispatches.

The worktree was clean at entry. Read the Task 1 brief, global constraints, `web/AGENTS.md`, and installed Next documentation at `web/node_modules/next/dist/docs/01-app/03-api-reference/01-directives/use-client.md` before editing. The required semantic lookup reported `INDEX_MISSING`; no index was created. The initial graphify query reported that `graphify-out/graph.json` did not exist; focused source reads established the orchestration defect.

## Root cause and implementation

The live workspace resolved API paths but checked only `resolved.citationKeys` against graph nodes. Existing Issue and Person endpoints suppressed context reads even when the API returned Issue → PR → Person with the PR absent, or the relevant typed edge absent.

Extracted the live orchestration into `resolveClaimContext`, used directly by `LiveWorkspace` inside the existing selection guard. Before fixing the decision, ran regressions against this extracted citation-only decision to demonstrate the production behavior failing.

The fixed helper:

- Checks issue, API-returned path nodes, and citations for missing nodes.
- Checks every adjacent returned path segment for matching relationship type and endpoints, permitting traversal against a stored edge's direction. A parallel edge of another type does not count.
- Fetches `/subgraph` when nodes or segments are missing, with distinct issue/path/citation seeds.
- Uses the existing priority merge, 150-node cap, and endpoint closure. Computes missing nodes and supported-absence rings from the actual displayed graph rather than the uncapped union of responses.
- Preserves API paths verbatim, including empty paths; never constructs paths or edges from graph connectivity. Failed or incomplete context responses do not fabricate evidence.
- Checks abortion after path/context responses. The existing monotonic selection guard prevents stale or cancelled results from applying either graph or selection state.

## RED evidence

Command, from `web/`:

```text
npx playwright test --config playwright.evidence.config.ts
```

Ran after the tests and mechanical orchestration extraction, before changing the citation-only missing-context decision:

```text
Running 10 tests using 1 worker
5 failed
5 passed (1.3s)
```

Failures and relevant assertion output:

1. `fetches missing intermediate node even when citation endpoints exist`: context requests expected length `1`, received `0`, `[]`.
2. `fetches missing typed relationship segment even when citation endpoints exist`: context requests expected length `1`, received `0`, `[]`.
3. `context seeds include citations outside the returned path without inventing paths`: expected `[issue, pr, person, missing-module]`; received repeated issue/person seeds.
4. `failed context fetch preserves API paths and reports missing intermediates`: expected missing keys `[pr]`, received `[]`.
5. `context merging caps nodes, closes edges and reports evidence dropped by the cap`: expected missing keys `['person:149', 'person:150']`, received `[]`.

Complete/reverse-edge context, unreachable/empty paths, supersession, and cancellation already passed in that run.

## GREEN and covering checks

Final focused command, from `web/`:

```text
npx playwright test --config playwright.evidence.config.ts
Running 12 tests using 1 worker
12 passed (597ms)
```

Coverage: absent intermediate, wrong-type parallel relationship, complete reverse-direction context, unreachable citation, empty paths, distinct issue/path/all-citation seeds, failed context request, incomplete returned segments with no invented edges, abort during path resolution, 150-node closure/missing notices, superseded context response, cancelled context response. Only external `fetch` is replaced; the production resolver, HTTP client, merge, and selection guard execute. No Next startup or browser fixture is needed.

An initial typecheck caught test fixture mistakes (`props` instead of `properties`, and readonly table inference); corrected those test types. Final:

```text
npm run typecheck
> tsc --noEmit
exit 0
```

Existing pure graph tests used a temporary no-server config outside the repo, selecting only browser-independent cases:

```text
npx playwright test --config /private/var/folders/n0/ddvg_8_s52j9gfqw91mthh7h0000gn/T/opencode/task-1-pure.config.cjs --grep 'force graph leaves input|graph cap collapses|late response cannot|unreachable citation'
4 passed (288ms)
```

The four cases were input immutability, 150-node collapse, late selection response, and unreachable citation.

Ran the full web test command once:

```text
npm test
> npm run test:empty && npm run test:e2e
ALL EMPTY RENDER CHECKS PASSED
> playwright test
[WebServer] Error: listen EADDRINUSE: address already in use :::3001
Error: Process from config.webServer was not able to start. Exit code: 1
exit 1
```

This run did not reach browser assertions. The user-reported existing Next/Turbopack font-resolution failure was not independently reached because server startup first failed on the occupied port. Did not stop another session's server or modify unrelated font tooling.

Required graph update:

```text
graphify update .
AST extraction: 161/161 files (100%) [10 workers]
[graphify watch] Rebuilt: 1448 nodes, 2627 edges, 120 communities
[graphify watch] graph.json, graph.html and GRAPH_REPORT.md updated in graphify-out
Code graph updated.
```

`graphify-out/` is ignored; generated outputs were not staged. `git diff --check` passed.

## Intentional files

- `web/components/workspace.tsx`: delegates live selection orchestration to the tested production helper; applies only guarded graph/selection results.
- `web/lib/evidence-selection.ts`: complete-context decision, API context read, existing capped merge, actual-display missing-node classification, abort checks.
- `web/tests/evidence-context.spec.ts`: browser-independent production-orchestration regressions.
- `web/playwright.evidence.config.ts`: existing Playwright runner without a web server or browser dependencies.
- `.superpowers/sdd/2026-10-15-b4-b6-review-fixes/task-1-report.md`: this report (explicitly staged despite the report directory's ignore rule).

## Self-review

- Reviewed the scoped diff against the brief and global constraints. No fixture/schema/action/API policy changes.
- Missing segment detection compares both adjacent stable keys and the API relationship type, in either stored direction; complete reverse edges avoid redundant reads.
- Tests assert actual merged nodes/edges and unchanged API paths, not a test reimplementation of the fetch decision. Removing node/segment detection, typed matching, reverse matching, priority deduplication, capped missing classification, or stale-response guards is covered by the assertions.
- All edge creation remains confined to merging actual API graph edges. An incomplete context response leaves the missing edge absent and keeps the original returned path; no inferred relationship is drawn.
- Context errors preserve API evidence and accurately list missing displayed nodes. Existing UI notices describe missing nodes only; no new segment-warning UI was introduced.
- Existing issue/reset cancellation and fixture selection remain in place. Deferred live triage integration remains deferred.

## Concerns and limitations

- Browser verification remains blocked: this attempt encountered occupied port 3001 before the previously reported font-resolution failure. No browser success is claimed.
- No independently dispatched reviewer was used, per the task instruction; this report records implementer self-review only.
- Generated graph outputs are local/ignored, not part of the task commit.
