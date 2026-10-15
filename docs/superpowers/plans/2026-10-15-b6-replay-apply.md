# B6 — Replay and apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Copy a real upstream issue into the sandbox and produce auditable, sandbox-only triage actions with safe dry-run behavior.

**Architecture:** Keep replay as a small CLI using read-only source credentials and a sandbox-bound writer. `api/actions.py` loads a trusted tenant/run context, validates the handled issue before any GitHub write, executes persisted proposed actions, then records results through A's named query. The HTTP/UI route stays dry-run-only pending the public-live-write decision; verified webhooks can later call the internal service live.

**Tech Stack:** Python 3.11+, `httpx`, PyYAML, FastAPI/Pydantic, FalkorDB through A's query layer; existing Next.js/Playwright frontend.

**Spec:** `docs/spec.md` §§3, 5, 7, 9, 12–13; `docs/tasks.md` B6; `CLAUDE.md` hard rules 3–6 and environment; `2026-10-15-b4-b6-review.md` for decisions and A handoffs.

## Global Constraints

- “Never write to any other repo. Refuse in code if the target is not `SANDBOX_REPO`.”
- `GITHUB_TOKEN` is read-only; `GITHUB_WRITE_TOKEN` is the only token that writes, scoped to the sandbox.
- `dry_run=true` is the service default; `DRY_RUN=true` is the deployment default. The later webhook deliberately passes false.
- Apply only to `incoming: true` Issues in `SANDBOX_REPO`, using **their own number**, never the `REPLAY_OF` target number.
- Missing sandbox labels are created; assign only sandbox collaborators, otherwise `dry_run` with a note.
- Posted logins are code spans without `@`; suggestions for non-collaborators remain in the comment.
- All action outcomes use existing `ApplyResult`/`ActionResult` and are recorded by `record_actions.cypher`.
- Replay first line is exactly `Replayed from <upstream url>`; no webhook, embeddings or agent-loop implementation in B6.
- B does not edit `ingest/fetch.py`, `api/mcp_server.py`, A-owned tools/queries or shared contracts without agreement.

## Review Focus

1. A forged run/body, upstream original or wrong tenant cannot redirect writes beyond the sandbox.
2. Dry-run and refusal paths make zero GitHub writes, including label creation.
3. Multi-repo issue-number collisions and PR payloads cannot replay the wrong source.
4. Ineligible reviewers, unsafe comment mentions and GitHub failures never become false “applied” outcomes.
5. Repeated apply requests do not repost comments or reapply rejected/applied actions accidentally.

---

## File map and prerequisites

- Create `api/github.py`: small sandbox-bound GitHub writer plus collaborator lookup, using `httpx`.
- Create `api/actions.py`: `ApplyContext`, injected store adapter, action planning/guard/execution and comment safety.
- Modify `api/config.py`, `api/main.py`, `api/routes/actions.py`: settings, service dependency and live/fixture dispatch.
- Create `ingest/replay.py`: CLI, tenant repo/source selection, read-only upstream fetch and sandbox issue creation.
- Create `tests/test_replay.py`, `tests/test_actions.py`, `tests/test_apply_route.py`; mock HTTP using existing `httpx.MockTransport`, no added mocking dependency.
- Modify `web/lib/api-client.ts`, `web/components/workspace.tsx`, `web/components/triage-card.tsx`; create `web/tests/apply.spec.ts`; modify `.env.example`, `README.md`.
- A must supply a run-context query/lookup and `record_actions.cypher` executor. No fixture run is eligible for live writes. B6 unit tests can proceed with a fake store; real graph acceptance waits for A's persisted run support.
- Obtain approval for public-write policy before changing HTTP behavior. This plan recommends refusing public `dry_run=false` regardless of sandbox eligibility; internal service remains live-capable.

### Task 1: Replay an unambiguous upstream issue (~25 min)

**Interfaces:** `replay_issue(tenant: str, issue_number: int, repo: str | None, *, read_client: httpx.Client, writer: SandboxGitHub, tenant_root: Path) -> str` in `ingest/replay.py` returns the new sandbox issue URL. `SandboxGitHub(sandbox_repo: str, client: httpx.Client)` in `api/github.py` fixes the write target; `create_issue(title: str, body: str) -> str` cannot accept an arbitrary target repo.

- [ ] **Step 1: Write failing tests** in `tests/test_replay.py`: `test_replay_preserves_title_body_and_prefix` asserts new body equals `f"Replayed from {url}\n\n{body or ''}"`; `test_replay_writes_only_configured_sandbox` inspects request URL and write credential; `test_ambiguous_issue_requires_repo` expects a clear CLI error and zero writes; `test_pr_payload_and_unconfigured_repo_are_refused` rejects `pull_request` payloads and repos absent from tenant config. Also test missing source, missing sandbox/write token and null body.
- [ ] **Step 2: Run** `python -m pytest tests/test_replay.py -q`; expect missing-module/function failures.
- [ ] **Step 3: Implement** CLI `python -m ingest.replay --tenant falkordb --repo FalkorDB/GraphRAG-SDK --issue <n>`. Validate tenant against configured tenants and repo against its YAML; validate positive issue number. With no `--repo`, select only if one configured repo or one unambiguous matching cached issue; otherwise fail with instructions, never choose the first repo or probe every repo speculatively. Read existing cached normalized issue data if available; otherwise GET the explicit source issue with `GITHUB_TOKEN`. Use returned canonical GitHub URL after validating source repo/number; reject a source already in the sandbox.
- [ ] **Step 4: Implement** the sandbox writer's strict `owner/name` validation, fixed `https://api.github.com` base, finite timeout and disabled redirects. Require `GITHUB_WRITE_TOKEN` for creation. Never include tokens in errors/output. The CLI explicitly creates one issue (it is not an apply dry run); document that distinction and print only the created issue URL. Do not reuse the A-owned fetcher's client for writes or import/edit its environment handling; use a small local env reader or require exported env and document it.
- [ ] **Step 5: Verify and commit**: `python -m pytest tests/test_replay.py -q` → pass with no real network. Commit `B6: add sandbox issue replay CLI`.

### Task 2: Guarded, recorded action execution (~45 min)

**Interfaces:** In `api/actions.py`, `ApplyContext(run_key: str, issue_key: str, repo: str, issue_number: int, incoming: bool, actions: list[RecordedAction], comment_markdown: str)` is an internal immutable context. `ActionStore.load_context(tenant: str, run_key: str) -> ApplyContext` and `ActionStore.record_results(tenant: str, run_key: str, results: list[ActionResult]) -> None` are proposed adapter methods awaiting A's backing lookup/query. Service entry point: `apply_triage(tenant: str, run_key: str, dry_run: bool = True) -> ApplyResult`; dependencies are configured by app/service construction, not untrusted request fields. `sanitize_comment(markdown: str, logins: list[str]) -> str` ensures known reviewer logins are unprefixed code spans; reject residual user/team/broadcast mention syntax before posting.

- [ ] **Step 1: Write failing tests** in `tests/test_actions.py`: `test_upstream_issue_refused_even_when_replay_target_is_sandbox` and inverse target setup; `test_sandbox_requires_incoming_true`; `test_dry_run_makes_zero_github_requests`; `test_incoming_issue_uses_own_number`; `test_cross_tenant_context_is_not_found`. Refusal produces per-action `status='refused'`, useful notes and zero GitHub calls; missing tenant/run uses route 404 rather than a fabricated action list.
- [ ] **Step 2: Run** `python -m pytest tests/test_actions.py -q`; expect missing service failures.
- [ ] **Step 3: Implement** context validation before constructing a writer or using credentials. Repo comparison uses validated case-normalized owner/name; stored Issue key and own number must agree with context, `incoming` must be exactly true. Never read target repo/number from comment URLs, browser payloads or `REPLAY_OF`. Derive actions from persisted `PROPOSED` links in that tenant/run. Reject a forged store context or Action not owned by the run. Fixture mode cannot invoke the live service.
- [ ] **Step 4: Implement** dry-run planning as a GitHub-free operation: eligible planned actions are returned/recorded as `dry_run`, retaining their values and notes; include collaborator verification as pending, not falsely successful. Invalid targets return `refused` even if dry run was requested. No write token required for a plan. Use A's `record_actions.cypher` for status/note updates with `key=action_key`; graph persistence is allowed in dry run, GitHub mutation is not.
- [ ] **Step 5: Add failing live-action tests**: `test_missing_label_created_then_attached`, `test_noncollaborator_assign_is_dry_run_but_stays_in_comment`, `test_only_collaborator_can_be_assigned`, `test_comment_has_no_mentions`, `test_write_token_never_used_for_upstream_read`, `test_github_failure_never_marked_applied`. Use `MockTransport` to verify request order/paths/bodies/credentials and test 403, 404 and 429 separately.
- [ ] **Step 6: Implement** fixed-target writer operations `ensure_label(name: str) -> None`, `add_labels(number: int, labels: list[str]) -> None`, `is_collaborator(login: str) -> bool`, `assign(number: int, login: str) -> None`, `comment(number: int, body: str) -> None`. For labels, paginate existing labels or perform an exact encoded-name lookup; default new label colour `ededed` is an implementation choice. For collaborator check, 204 means eligible, 404 means not; 403/429 are lookup failures, not proof of non-membership. Catch “already exists” races only after confirming the label exists. Record each action only after its specific successful response. For failed/uncertain actions use `refused` with a sanitized explanatory note (existing contract has no `failed` status); do not retry non-idempotent POSTs automatically.
- [ ] **Step 7: Make comments safe**: build final text from validated claims/source URLs, rendering reviewer logins as backticked bare logins; strip leading `@` in reviewer action values. Run `sanitize_comment` and block any remaining mention syntax, including team/HTML mention forms, rather than regex-rewriting arbitrary Markdown into a misleading comment. Preserve evidence links and non-collaborator suggestions. Test `@login`, existing code spans, team mentions, newlines and malicious Markdown. Append a run link based only on configured `PUBLIC_BASE_URL`, tenant and run UUID, e.g. `/t/falkordb?run=<uuid>`; actual run-link selection is completed with B11/A10, not falsely claimed functional in B6.
- [ ] **Step 8: Add and implement retry tests**: `test_reapply_skips_applied_and_rejected_actions` and `test_prior_dry_run_can_later_apply`. Never reapply `applied` or `rejected` records; returned notes explain skipped state without converting a rejected action back to proposed. Existing `ActionResult` lacks `rejected`: report such an execution attempt as `refused` but do not persist that refusal over its stored `rejected` state. Serialize same tenant/run applies in-process; B7's single worker remains the concurrency model. Include a stable per-run comment marker and check existing sandbox comments for that marker before posting, covering a retry after GitHub success but graph-recording failure. If graph recording fails, return a sanitized 503, do not report complete success, and do not blindly repost on retry. Cross-process exactly-once delivery is not promised by B6.
- [ ] **Step 9: Verify and commit**: `python -m pytest tests/test_actions.py tests/test_replay.py -q` → pass. Commit `B6: guard and record sandbox triage actions`. A registers the same service as stdio MCP `apply_triage`; request that handoff without modifying A's file.

### Task 3: Route/UI dry-run integration and acceptance (~20 min)

**Interfaces:** Existing `POST /tenants/{tenant}/runs/{run UUID}/apply`, `ApplyRequest`, `ApplyResult` remain unchanged. Route maps UUID to canonical `run:<uuid>`; `applyRun(tenant: string, runKey: string, dryRun: boolean): Promise<ApplyResult>` in `web/lib/api-client.ts` strips exactly one validated `run:` prefix when building the UUID route. `TriageCard` receives `onApply(dryRun: boolean): Promise<ApplyResult>` from Workspace in API mode, keeping the old fixture preview path.

- [ ] **Step 1: Write failing route/UI tests**: `test_live_apply_defaults_to_dry_run`, `test_public_live_write_refused_before_service_execution`, `test_unknown_run_is_404`, `test_fixture_apply_never_calls_service`; Playwright `API apply posts current tenant/run and dry_run`, `pending disables repeat submit`, `failure is not shown as applied`. Verify no repo, issue number, arbitrary actions or credentials are accepted from the browser.
- [ ] **Step 2: Run** `python -m pytest tests/test_apply_route.py -q` and, from `web/`, `npm run test:e2e -- tests/apply.spec.ts`; expect missing integration failures.
- [ ] **Step 3: Implement** service dependency and route dispatch; keep B2 fixture responses exactly. Under the recommended policy, live public `dry_run=false` returns HTTP 403 with a clear refusal; the internal service alone accepts live execution for trusted callers. Default service calls are dry-run; `DRY_RUN=true` must not silently override B7's explicit false later. Require sandbox config for eligibility, write token only for actual writes, and sanitized configuration errors.
- [ ] **Step 4: Wire UI** only when a real run is selected. Default switch remains on; Apply button sends canonical current run, shows each action's actual `applied|dry_run|refused` wording and note, and is disabled while pending. No real run means disabled apply, not fixture substitution. If live mode is disallowed publicly, explain the policy beside the switch and never imply that toggling it authorizes GitHub writes. Keep fixture controls/network-free behavior unchanged.
- [ ] **Step 5: Verify** `python -m pytest -q`; from `web/`, `npm run typecheck`, `npm run test:empty`, fixture/API Playwright suites, `npm run build`. Update README/env instructions for repo disambiguation, credentials, public refusal and dry run. Run `graphify update .` after code changes.
- [ ] **Step 6: Execute the Done when check with explicit sandbox permission**: run replay for an A-shortlisted source issue, retain new sandbox URL and exact first-line output; call apply with `{}` on a real persisted sandbox run and retain planned actions; run `test_sandbox_requires_incoming_true` and the non-sandbox refusal test, retaining output proving zero writes. If no persisted run exists, tests demonstrate behavior but live dry-run acceptance remains blocked on A10; do not call a fake run real. Commit `B6: integrate safe apply preview and acceptance`.

## Scope and operational limits

Creating a replay issue is a deliberate GitHub write and triggers B7 once installed; do it only during approved acceptance, not during automated unit tests. B6 does not create embeddings, ingest incoming issues, create `REPLAY_OF`, trigger agents or validate webhook signatures—those remain B7. Public live UI apply and a functional selected-run deep link remain explicitly gated by the review decision and A10/B11 respectively.
