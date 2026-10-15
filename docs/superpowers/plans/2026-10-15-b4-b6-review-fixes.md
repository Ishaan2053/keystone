# B4–B6 scoped review fixes

**Goal:** Fix the four existing-code defects identified in review, with TDD, one implementer and scoped reviewer per task, then an independent final review.

**Spec:** `docs/spec.md` §§7, 9, 11, 13; `design/graph-canvas.md`; original B4–B6 plans. The user's request authorizes these four fixes, not completion of deferred graph/action/agent integrations.

## Global Constraints

- Sandbox only: no real GitHub writes during development or tests; mock external requests.
- Posted reviewer logins must not ping real maintainers; unsafe ambiguous Markdown must fail closed.
- Never mark an action applied without confirmed execution; preserve existing `ActionResult` contracts.
- Evidence paths come from the API, never invented by client-side graph traversal; stable keys, maximum 150 displayed nodes, no dangling edges.
- Preserve tenant/run and cancellation guards, fixture behavior, and shared schema/fixture contracts.
- Do not implement the deferred A5/A10/B11 integrations, change public-write policy, or fix unrelated font tooling.
- Use existing dependencies. Follow TDD: record failing regression output, implement the smallest root-cause fix, run covering tests and self-review before committing.
- Work in the existing isolated `feat/graph-canvas-sandbox-actions` branch. No pushes, merges or GitHub resources.

## Task 1: Fetch complete evidence path context

**Files:** `web/components/workspace.tsx`, `web/lib/evidence-selection.ts` if needed for a focused testable orchestration helper, and `web/tests/graph-canvas.spec.ts` or a narrowly scoped new test file.

**Defect:** Live claim resolution checks only `citationKeys` against the current graph, so existing citation endpoints suppress fetching of missing path intermediates/edges.

- [ ] Reproduce with current graph containing Issue and Person, API path Issue → PR → Person, and PR absent. Also test all path nodes present but a returned relationship segment absent.
- [ ] Write regression tests that execute production orchestration (not a reimplementation in tests); assert the context fetch occurs, its seeds include issue/path/citations, the merged graph contains the API-provided segments, and no path is fabricated.
- [ ] Fix the missing-context decision to include returned path nodes and typed adjacent segments; request `/subgraph` when either nodes or required segments are missing, respecting reverse traversal of stored edges. Keep cap/endpoint closure, aborted/stale selection handling, empty paths and missing-evidence notices truthful.
- [ ] Test complete context avoids unnecessary fetches, empty/unreachable paths stay empty, and stale/aborted responses cannot apply. Run focused tests and `npm run typecheck`; browser startup errors may be documented, but pure orchestration regressions must run without Next startup. Use existing Playwright runner with a no-web-server config if required rather than adding a test framework.
- [ ] Run the feasible web checks once, update the knowledge graph using `graphify update .` (only intended outputs), and commit `B5: fetch missing evidence path context` with only task files. Do not wire live triage UI as part of this fix.

## Task 2: Close escaped-backtick mention bypass

**Files:** `api/actions.py`, `tests/test_actions.py`.

**Defect:** Regex code-span masking hides `@victim` in `Literal backticks: \`@victim\`` although escaped backticks render as literal text, not code delimiters.

- [ ] Write a failing regression using `sanitize_comment` and the action execution path proving ambiguous/escaped-backtick mention text cannot reach the mocked comment POST.
- [ ] Make code-span handling account for escaped delimiters and delimiter-run lengths, or conservatively refuse ambiguous mention-bearing syntax. Do not add a Markdown parser dependency. Known reviewer mentions must remain safe bare code spans; legitimate existing code spans and ordinary comments must preserve compatibility.
- [ ] Cover escaped opening/closing delimiters, odd/even backslash parity, multiple-backtick spans, unmatched delimiters and unknown/team mentions. Verify the output is demonstrably safe, not merely that the input string changed.
- [ ] Run focused RED/GREEN tests then `.venv/bin/python -m pytest -q`; run `graphify update .` after code changes without staging unrelated graph churn. Commit `B6: reject active mentions behind escaped backticks`.

## Task 3: Confirm GitHub assignment outcomes

**Files:** `api/github.py`, `tests/test_actions.py` (and `tests/test_replay.py` only if shared writer coverage belongs there).

**Defect:** A 200/201 assignment response without the requested assignee is treated as successful, recorded as applied and skipped forever on retry.

- [ ] Write failing transport/service regressions for `201 {"assignees": []}`, missing/malformed confirmation and invalid JSON. Assert the service does not report/persist `applied`; keep the collaborator gate and fixed sandbox target.
- [ ] Validate the successful issue response has an `assignees` list containing the requested `login`, case-insensitively. Raise the existing sanitized writer error on absent, malformed or unconfirmed results so the service uses the existing refused/uncertain note contract. Do not require every unrelated assignee entry to be perfect if valid confirmation exists.
- [ ] Update existing success mocks to return realistic assignment confirmation. Add positive confirmation, mixed-case login, unchanged HTTP failure, and retry-eligibility coverage. Do not broaden this to unrelated GitHub operations.
- [ ] Run focused RED/GREEN tests then `.venv/bin/python -m pytest -q`; run `graphify update .` after code changes without staging unrelated graph churn. Commit `B6: verify requested assignee in GitHub response`.

## Task 4: Fail closed on incomplete comment searches

**Files:** `api/github.py`, `tests/test_actions.py`.

**Defect:** After 20 full pages (100 comments each), `find_comment` returns false despite not knowing whether the marker occurs on later pages; retry then duplicates a posted comment.

- [ ] Write a failing test for all `MAX_COMMENT_PAGES` pages full without a marker; assert the lookup reports uncertainty and the action service performs zero comment POSTs and never records applied.
- [ ] Preserve the bounded search but raise the existing sanitized writer error when its budget is exhausted without proof of absence. Short final pages establish absence; any found marker establishes presence. No unbounded pagination, new persistence system or increased write permissions.
- [ ] Cover marker found on the last permitted page, short/empty final page, HTTP/transport failure, budget exhaustion through the service, and recording-failure retry behavior. Verify comment POST occurs exactly once only after proven absence.
- [ ] Run focused RED/GREEN tests then `.venv/bin/python -m pytest -q`; run `graphify update .` after code changes without staging unrelated graph churn. Commit `B6: refuse comment posting after incomplete dedupe search`.

## Final verification and independent review

- [ ] Verify each task's implementation with a fresh task-scoped subagent; fix important findings and obtain scoped re-review before proceeding.
- [ ] Run backend tests, web typecheck and empty-render checks, plus runnable targeted regressions; report browser/font startup limitations accurately.
- [ ] Dispatch a fresh independent reviewer over the full fix range and its affected call sites; verify all four original findings are addressed and no regressions introduced. Deferred integrations are known scope boundaries, not fixes claimed here.
- [ ] Address final-review findings in one fix wave with scoped re-review. Leave the branch local and report commits, tests, limitations and remaining integration dependencies.
