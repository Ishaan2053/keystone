# B11 live UI and Proof — verification handoff

Final product HEAD tested: `5bc87b358631fc1be655909c391a2e2356041487` (2026-10-17). Earlier `da733bd` and isolated-snapshot evidence below is historical, not the final verification result.

## Acceptance status

**Real acceptance is blocked, not passed.** Automated fake-API and fixture tests do not establish real metrics, webhook latency, or live phone acceptance. No sandbox issue/replay, metrics generation, deployment, secret inspection, or authored product/test changes were performed during this verification. Owner task checkboxes remain unchanged. Controller-confirmed product fixes are committed in the final HEAD, and the fresh automated results below cover them. Task 3's scoped review was pending during verification; its verdict and the controller's final whole-change review remain separate gates. No review approval is claimed here.

## Automated verification

All required commands reran in order on the final HEAD on 2026-10-17, starting at 16:05:14 UTC. Durations below are measured wall time. Full output and UTC start times are retained locally under **Final-head verification** in ignored `.superpowers/sdd/2026-10-17-b11-live-ui-proof/task-4-report.md`.

| Command | Result | Wall time |
| --- | --- | --- |
| Existing compatible Python `-m pytest -q tests/test_metric_artifacts.py tests/test_live_graph_routes.py tests/test_graph_routes.py` | PASS: 55 passed, 3 skipped; exit 0 | 1.55s |
| `cd web` → `npm run typecheck` | PASS; exit 0 | 0.84s |
| `npm run test:e2e:api` (all API suites) | PASS: 50 passed; exit 0 | 116.16s |
| `npm test` | PASS: empty-render checks and all 54 fixture browser tests passed; exit 0 | 89.36s |
| `npm run build` (required, unmodified) | FAIL: Turbopack cannot resolve external `web/node_modules` symlink; exit 1 | 1.29s |
| `npm run build -- --webpack` (**supplemental only**) | PASS: production compilation, TypeScript and page generation; exit 0 | 5.15s |
| `graphify update .` | PASS: AST-only update, 2635 nodes / 6296 edges / 161 communities; exit 0 | 2.63s |

Python executable: `/Users/pranavtripathi/.local/share/opencode/worktree/072ae0/b9-b10-plans/.venv/bin/python`. Web dependencies were reused through the existing temporary external symlink; nothing was installed. Graph outputs are ignored, with no tracked generated changes staged; no zvec index lifecycle operation ran.

### Failures and environment caveats

- Historical fixture failure on `da733bd`: `web/tests/graph-canvas.spec.ts:181`, assertion at line 197, expected canvas/stage width difference `<= 2`, received `440`. The isolated follow-up below diagnosed the observer-delivery race. On final HEAD `5bc87b3`, the amended resize test and all 54 fixture tests passed in the fresh required `npm test` run. That pass does not rewrite the earlier failed result or claim exhaustive flake elimination.
- The required default build failed before product compilation because Turbopack rejected a symlink leaving its filesystem root. The separate successful Webpack build is supplemental environment evidence, **not a passing result for `npm run build`**. Controller/owner must assess this and verify the required build in a supported dependency layout.
- The known React client-rendered script warning appeared in both unknown-tenant browser tests; those tests passed.
- The three targeted Python skips remain skips, not live-database verification.
- The production build regenerated tracked `web/next-env.d.ts`, changing its two generated imports from `.next/dev/types/*` to `.next/types/*`. It is intentionally unstaged and left for controller cleanup; the handoff commit contains no product files. The controller also owns dependency-symlink cleanup.

### Historical resize assertion follow-up (isolated snapshot)

The original integration results on `da733bd` remain historical: API **47 passed**, backend **55 passed / 3 skipped**, typecheck passed, original `npm test` **53 passed / 1 failed**, required Turbopack build failed on the external dependency symlink, and supplemental Webpack build passed. The table above supersedes these as final-HEAD evidence, without relabeling that original failure. The build failure is an environment limitation; no build-tool/configuration change is included.

Follow-up source: `e389cca` plus only the amended `web/tests/graph-canvas.spec.ts`. Root cause: the stage's layout shrinks before its `ResizeObserver` callback updates React state and `ForceGraph2D` applies the new canvas width. Waiting only for the stage then asserting canvas width synchronously races that lifecycle. A snapshot-only controlled observer-delivery probe deterministically measured stage **726px**, canvas **1166px**, difference **440px**; after releasing delivery, both were **726px** (difference **0**). The old immediate assertion failed; the eventual condition passed. Normal unmodified focused/full snapshot runs passed, consistent with an intermittent race, not evidence that the original failed integration passed.

The amended test polls the actual canvas/stage width difference in one browser evaluation, retaining the **<=2px** correctness requirement and existing theme/pixel checks. No sleeps, timeout increases, runner retries, or product changes were added. Focused test: **1 passed (7.7s)**. Full isolated `npm test`: empty-render checks passed and **54 browser tests passed (1.5m)**, exit 0. The known unknown-tenant React script warning remained.

Evidence snapshot retained at `/private/var/folders/n0/ddvg_8_s52j9gfqw91mthh7h0000gn/T/opencode/b11-resize-1am1i3cz`; port **62836**, snapshot-owned `.next`, linked existing dependencies, no fake API/server sharing. Logs: `red-controlled.log`, `green-controlled.log`, `green-focused.log`, `green-suite.log`; controlled RED trace retained under `evidence/red-controlled/`. Full local report: `.superpowers/sdd/2026-10-17-b11-live-ui-proof/integration-fix-report.md`. The historical snapshot worker ran no browser tests, typecheck or builds in the shared worktree; the later Task 4 final verification did run the required commands here, as recorded above.

**Final integration status:** the historical snapshot excluded concurrent Proof fixes, but the fresh actual-HEAD suites above now cover the committed final product. All 50 API tests passed, including the three initial stats-only outage/Retry recovery cases for bare tenant, run and issue URLs. This verifies their tested behavior, not a scoped-review verdict. Scoped review and final whole-change review remain controller gates. Real acceptance blockers and owner-only checkboxes below remain unchanged.

### Evidence files

The passing fake-API dark/light Proof tests wrote `web/test-results/proof-{dark,light}-{desktop,phone}.png`. The subsequent required fixture run cleared that output directory before preservation; **none of those PNGs is retained or presented as viewable evidence**. No live screenshots were captured. The preserved historical `da733bd` fixture failure artifacts are local, ignored and uncommitted:

- `.superpowers/sdd/2026-10-17-b11-live-ui-proof/evidence/graph-canvas-canvas-resize-0b23e-hanges-update-token-colours-chromium/trace.zip`
- `.superpowers/sdd/2026-10-17-b11-live-ui-proof/evidence/graph-canvas-canvas-resize-0b23e-hanges-update-token-colours-chromium/error-context.md`

These are synthetic test diagnostics, not real webhook or real Proof acceptance.

## Exact live blockers

- Neither this worktree nor the main checkout at `/Users/pranavtripathi/Documents/Projects/keystone` contains `data/proof/` or `data/bench/`, including the required `falkordb.json` files. Therefore no real `/tenants/falkordb/stats` artifact comparison or refreshed real Proof inspection was possible.
- `docker compose ps --format json` exited 1 before service inspection: `required variable FALKORDB_PASSWORD is missing a value: FALKORDB_PASSWORD is required`. This establishes a local configuration blocker, not a claim that a remote deployment is down. No secret values were inspected or supplied.
- No authorized, verified live sandbox/webhook session was available for this task. Real run-arrival timing, selected-run isolation, empty-tenant auto-opening, and live phone/both-theme smoke checks remain unperformed. Test screenshots, if retained below, are synthetic evidence only.

## Owner steps to unblock and accept

1. Supply the existing A13 and A14 artifacts through the agreed artifact paths in the API runtime; do not rerun producers merely to manufacture acceptance. Have the authorized operator configure graph access and a working live API/web session. Verify B7's deployed worker/adapters are wired; configuration alone does not prove that. Follow README's **Live webhook flow, commands and deployment** instructions: sandbox-only repository, signed webhook, `issues`/`issue_comment` events, live mode and one enabled tenant. Keep credentials private.
2. Read `/tenants/falkordb/stats` and compare against both real JSON files: exact `n`, **all 12 rates** (reviewer hit@1/hit@3 and module hit@1/hit@3 for graph, CODEOWNERS and similar-issues), and every tool's p50/p95. Do not omit module hit@1/hit@3 because older task text says “9 rates.” Missing measurements must stay unavailable, not zero.
3. Keep persisted run A selected in the live UI. Using the established B7 workflow, an authorized owner opens an issue or replay **only in the configured sandbox**. Record issue-creation time, the first observation of run B in `/tenants/falkordb/runs?limit=20`, and the first UI appearance. Without reload, require UI appearance within 10 seconds of run availability and verify A remains selected. Record observation cadence/clock and measured intervals. Separately report issue-creation-to-UI duration; A10/B7 processing before a persisted run exists is an upstream dependency, not a B11 queue-status promise.
4. Select B and verify triage, trace, graph evidence, citations and Apply target all belong to B, with stable run keys and the correct tenant. Keep public Apply dry-run-only; do not bypass its refusal of live writes.
5. Repeat with an empty tenant view, then make its first run available through the authorized workflow: it must open automatically. Open Proof and verify refreshed real measurements, sample size and latency table. On a phone, repeat the live run/Proof path in dark and light themes, including panel dismissal/focus and long-label/overflow checks. Record timestamps and real screenshots in the acceptance record.
6. **Owner: please run and accept B11's “Done when” check in `docs/tasks.md`, then tick B11 yourself only after the real webhook and A13-number checks pass.** The controller also needs to clear pending reviews and assess the required build result; this handoff does not waive either gate.
