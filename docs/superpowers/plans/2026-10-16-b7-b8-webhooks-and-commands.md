# B7/B8 — Webhook Triage and Collaborator Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A verified sandbox issue event produces a real triage comment and working run link; collaborator rejects create module Rules and re-triage, and `why` explains graph paths.

**Architecture:** A thin FastAPI receiver verifies raw-body HMAC and event guards before enqueuing a small validated job. One lifespan-managed FIFO worker serializes local embedding, incoming graph upserts, A's persisted agent/memory services and the existing sandbox-only ActionService. Route tests inject fake services; live acceptance requires the actual A10/A11 adapters.

**Tech Stack:** Python 3.11+, FastAPI, asyncio/queue/threading from the standard library, httpx, FalkorDB, local embeddings; existing Next.js and Playwright for deep links. No Celery, Redis queue dependency or new graph framework.

**Spec:** `docs/spec.md` §§3, 7–9, 13; `docs/tasks.md` B7/B8; `CLAUDE.md`; `2026-10-16-a7-a8-b7-b8-coordination.md`; prior B6 plan for public dry-run policy.

## Global Constraints

- “Bad signature → 401.” HMAC-SHA256 is over raw bytes and uses constant-time comparison.
- “Reply 202 at once and process in a single background worker queue, one job at a time.” Do not use one FastAPI background task per delivery.
- Deduplicate `X-GitHub-Delivery`; skip ordinary issue triage completed within the last 60 seconds.
- Ignore wrong repository and Keystone bot events. Collaborators only for both comment commands; reject PR comment payloads.
- Incoming Issues use `incoming: true`, own sandbox number, local 384-d vectors, AUTHORED and optional REPLAY_OF. Replay URLs accept optional backticks.
- Only internal webhook flow calls `apply_triage(..., dry_run=False)`; existing public live-write refusal remains.
- Comments must not ping maintainers or backlink upstream. Reuse the existing sanitization/fallback behavior and fixed-repo writer.
- Reject feedback uses `scope='module'` and the same `record_feedback` as HTTP. Rules change ranking inside Cypher, not by filtering an LLM paragraph.
- Exactly one enabled tenant/process is the proposed deployment constraint; pending jobs/delivery dedupe are not restart-durable in this version.
- Shared contracts remain unchanged. Query additions in A's lane require agreement, not inline Cypher workarounds.

## Review Focus

1. Validly signed but wrong-repo, bot, PR, malformed or unauthorized events cause no graph/GitHub/LLM work.
2. Duplicate or concurrent deliveries, full queues and worker failures must not yield duplicate comments or kill subsequent jobs.
3. Replay metadata and repository-number collisions cannot redirect writes or leak tenant state.
4. Duplicate/ambiguous rejection cannot create multiple rules or re-triage before persisted feedback exists.
5. A run deep link loads the exact persisted tenant/run and cannot silently fall back to fixture data.

---

## File map and dependencies

- Create `api/webhooks.py`: route, private validated job dataclass, signature/event admission, delivery/pending/cooldown state and serialized worker.
- Create `api/webhook_store.py`: incoming issue upsert and latest run/assign-action lookup using named queries; no separate memory implementation.
- Create `api/comment_commands.py`: strict command parsing and safe confirmation/path formatting.
- Modify `api/config.py`, `api/main.py`, `api/routes/reserved.py`, `.env.example`, `docker-compose.yml`, `README.md`: service configuration/lifecycle, remove only webhook placeholder and document single-process deployment.
- Add agreed `cypher/upsert_incoming_issue.cypher`, `cypher/webhook_latest_run.cypher`, `cypher/webhook_node_links.cypher` (A-owned query lane; coordinate before editing).
- Tests: `tests/test_webhooks.py`, `tests/test_webhook_worker.py`, `tests/test_webhook_store.py`, `tests/test_comment_commands.py`, `tests/test_webhook_integration.py`; update webhook expectations in `tests/test_api_shell.py` without weakening unrelated fixture tests.
- Deep-link changes: `web/lib/api-client.ts`, `web/lib/data-source.ts`, `web/app/t/[tenant]/page.tsx`, `web/components/workspace.tsx` as needed; create `web/tests/run-link.spec.ts`, extend `web/tests/fake-api.ts`.
- A10/A11 service signatures and ActionStore adapter are defined in the coordination plan. Do not implement fake live substitutes or modify A-owned agent/memory routes in B7/B8.

### Task 1: B7 verified receiver and lifecycle queue (~30 minutes)

**Interfaces:** `verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool`; `WebhookWorker.start() -> None`, `WebhookWorker.enqueue(job: WebhookJob) -> bool`, `WebhookWorker.close() -> None`. Frozen `WebhookJob` contains `delivery_id`, configured `tenant`, `event`, `action`, `issue_number`, `title`, `body`, `issue_state`, `issue_author_login`, `issue_author_url`, `issue_author_is_bot`, `issue_created_at`, `sender_login`, `comment_id: int | None`, `comment_body: str | None`; no raw user payload. `create_app(..., webhook_worker: WebhookWorker | None = None)` supports injected testing.

- [ ] **Step 1: Write failing tests** in `tests/test_webhooks.py`: valid HMAC/allowed issue returns 202 with one admitted job; missing/malformed/mismatching signature returns 401 even for malformed JSON; valid signature with invalid JSON or missing required fields returns 400; wrong repo, configured bot and unsupported action return 202 ignored with zero enqueue; unknown/non-live configuration returns sanitized 503 after valid signature validation; missing delivery ID returns 400; full queue returns 503 and the same delivery can be retried. Assert no embedding/GitHub/agent call occurs in the request.
- [ ] **Step 2: Run** `python -m pytest tests/test_webhooks.py -q`; expect missing receiver failures.
- [ ] **Step 3: Implement** config fields for secret/bot identity with secrets excluded from repr, validate one configured tenant, and register `/webhooks/github` in place of its placeholder while retaining `/mcp`. Compute `sha256=<hex>` HMAC using `hmac.compare_digest`, validate signed JSON, normalize repository/login case for comparisons. Accept only `issues.opened`, `issues.edited`, `issue_comment.created`; reject payloads containing `issue.pull_request`. Use `queue.Queue(maxsize=100)` and one dedicated worker thread whose job runner can use `asyncio.run()` for A's async triage; graph/embedding/httpx work never runs on the FastAPI event loop. Lifespan starts the worker, stops admission on shutdown, drains accepted work then joins and closes owned clients; document deployment stop grace must cover the drain. Worker errors are logged with delivery/issue context, no tokens/raw body, and do not kill the thread.
- [ ] **Step 4: Run** receiver tests and add `tests/test_webhook_worker.py` assertions: maximum one active job, FIFO order, failure followed by successful job, and shutdown joins the worker without losing an admitted test job. Configure Compose for one API worker; default fixture-mode creation makes no provider/GitHub connection.
- [ ] **Step 5: Commit** `B7: verify webhook events and serialize processing`.

### Task 2: B7 dedupe, incoming issue and replay provenance (~30 minutes)

**Interfaces:** `WebhookStore.upsert_issue(tenant: str, job: WebhookJob, vector: list[float], replay_key: str | None) -> str` returns the canonical incoming issue key; `parse_replay(body: str, allowed_repos: set[str]) -> str | None` returns a canonical upstream issue key. Query params use scalar properties only, never arbitrary webhook dictionaries.

- [ ] **Step 1: Write failing tests** in worker/store tests: same delivery twice creates one job; different delivery IDs for a pending same issue do not duplicate triage; successful issue within 60 s is skipped and at 60 s can run; failed issue releases pending state and permits manual redelivery; bounded delivery retention does not evict queued/in-flight IDs. Replay parser accepts plain/backticked `Replayed from` lines for configured upstream repos and rejects malformed hosts, credentials, PR URLs, sandbox self-targets and unknown repo URLs. `test_author_is_issue_user_not_editor` asserts an edit retains `issue.user` authorship, not `sender`; `test_upsert_uses_sandbox_own_number` proves no write identity comes from replay.
- [ ] **Step 2: Run** `python -m pytest tests/test_webhook_worker.py tests/test_webhook_store.py -q`; expect new tests to fail.
- [ ] **Step 3: Implement** admission state under one lock: delivery reservation only with successful queue insertion; per-issue pending guard; monotonic 60-second completion cooldown only for ordinary issue jobs. Bound completed delivery IDs to 10,000 (retain pending IDs), and forget failed delivery IDs after pending cleanup so a manual redelivery can retry. No automatic retry storm. Embed through `encode([issue_text(title, body)])`. `upsert_incoming_issue.cypher` MERGEs the incoming Issue:Node and human Person:Node, preserves original created_at, sets body/title/state/url from validated sandbox identity, and reconciles AUTHORED. Replay targets must already exist in the tenant graph: MERGE only the edge, never fabricate an upstream Issue with an empty embedding. For edited bodies, remove obsolete REPLAY_OF before attaching a validated target; absence of a loaded replay target is logged explicitly and creates no dangling edge. Bot issue authors get no Person edge, consistent with the loader.
- [ ] **Step 4: Run** the worker/store tests; add real FalkorDB integration assertions in `tests/test_webhook_integration.py` for Issue/Node labels, AUTHORED direction, `vecf32` vector retrieval, REPLAY_OF direction and idempotent edits. Use a dedicated test graph and delete only that graph in cleanup. A skip is not live acceptance.
- [ ] **Step 5: Commit** `B7: deduplicate issue jobs and upsert replay provenance` after A reviews named queries.

### Task 3: B7 real agent/apply integration and selected-run link (~35 minutes)

**Interfaces:** consume A's `triage_issue`, existing `ActionService.apply_triage`, configured `PUBLIC_BASE_URL`, and live recall route. Add `getRun(tenant: string, runKey: string, signal?: AbortSignal): Promise<ToolResult<RecallRunData>>` in `web/lib/api-client.ts`; canonical UUID HTTP path, canonical `run:<uuid>` inside contracts. Add optional run selection to `loadWorkspaceData` without breaking its existing issue selection.

- [ ] **Step 1: Write failing tests**: worker verifies upsert → persisted triage → `apply_triage(tenant, result.run_key, dry_run=False)` order; agent failure makes no apply call; persistence failure makes no false completion; partial refused actions remain visible in persisted results and are logged rather than reported as full success. Missing real adapters disables admission with 503. Existing public `/apply` with false still returns 403. In `web/tests/run-link.spec.ts`, request a real-shaped fake recall result by `?run=<uuid>` and assert its exact triage, evidence and actions appear; unknown/malformed run or wrong tenant shows an error with no fixture substitution; plain `?issue=` still works.
- [ ] **Step 2: Run** backend integration tests and from `web/` run `npm run test:e2e:api -- tests/run-link.spec.ts`; expect failures until integration exists.
- [ ] **Step 3: Wire** real services through `create_app` with one shared ActionStore/ActionService, reject missing sandbox writer or public base URL for webhook live admission, and preserve existing safe comment marker/run-link rendering. Before writing frontend code, read the relevant installed Next.js guides under `web/node_modules/next/dist/docs/` per `web/AGENTS.md`; use current Promise-based params/searchParams. In API mode validate `run` as one UUID (optionally strip one `run:` prefix in the client), fetch recall and verify returned run identity, populate the existing workspace triage/trace/evidence; `run` takes precedence over `issue` only when valid. No runs polling or Proof panel in this task.
- [ ] **Step 4: Run** `python -m pytest tests/test_webhooks.py tests/test_webhook_worker.py tests/test_webhook_store.py tests/test_webhook_integration.py tests/test_apply_route.py tests/test_api_shell.py -q`; from `web/` run `npm run typecheck`, the run-link suite and `npm run build`. Update old webhook 501/OpenAPI assertions to the new security contract, leaving MCP 501 checks intact. Expected tests pass; explicitly report integration skips. Run `graphify update .` after code changes.
- [ ] **Step 5: Commit** `B7: wire persisted triage live apply and run deep links`. With permission, open a real sandbox issue from a phone, retain the Keystone comment and exact working run URL; send a bad-signature request and retain 401; send a correctly signed wrong-repo event and retain ignored output plus zero processing. Only those real results satisfy B7 acceptance.

### Task 4: B8 collaborator reject and module-rule re-triage (~30 minutes)

**Interfaces:** `parse_command(body: str) -> RejectCommand | WhyCommand | None`; frozen `RejectCommand(login: str, reason: str)` and `WhyCommand(login: str)`; `WebhookStore.latest_run(tenant: str, issue_key: str) -> RecallRunData | None` backed by the agreed lookup query. Consume `record_feedback(tenant, run_key, FeedbackRequest(...), given_by=sender_login)` and A's agent service.

- [ ] **Step 1: Write failing tests** in `tests/test_comment_commands.py` and worker tests: exact command line recognizes reject/why and preserves multiline reason only if the entire remaining text is the reason; quoted/fenced/incidental commands and missing reason are ignored; logins compare case-insensitively and optional leading `@` is removed. Collaborator 204 permits, 404 ignores, 403/429 fails closed with no feedback/agent writes. Latest exact-issue run supplies the matching assign action; no match or multiple matching actions produces safe explanatory reply and no rule. Rejected action is not recorded again. A rejection within 60 s still re-triages after feedback commit; duplicate delivery/comment ID never creates another rule.
- [ ] **Step 2: Run** `python -m pytest tests/test_comment_commands.py tests/test_webhook_worker.py -q`; expect new cases to fail.
- [ ] **Step 3: Implement** strict leading `/keystone reject <login> <reason>` and `/keystone why <login>` parsing in `api/comment_commands.py`; ignore ordinary text. Check actual comment sender via `SandboxGitHub.is_collaborator`, not `author_association`. In the same single worker resolve latest HANDLED run ordered by `at` then run key, find exactly one assign action matching the person and require non-null module. Call shared feedback with `verdict='reject', scope='module'`, trusted action key and sender login; never accept an action/run/module from comment text. Confirm only a persisted returned rule key using a safe comment with a stable marker based on GitHub comment ID, then re-triage and live-apply. Command jobs bypass issue cooldown but retain dedupe by both delivery ID and comment ID using the same bounded completed/in-flight retention policy. During same-process retries retain the resolved run/action identity, reload its feedback, and reuse the existing rejection's rule key rather than create another rule; use confirmation marker lookup before posting. Do not require reason/given_by fields absent from RecallRunData. Do not promise cross-process exactly-once: feedback-commit/re-triage crash recovery is a documented limitation of this in-memory worker. No automatic remove-assignee operation is added.
- [ ] **Step 4: Run** command/worker tests, plus an A11-backed isolated integration test: reject a fixture assign action; assert Feedback ON Action, rejected Action, active module Rule with DERIVED_FROM/APPLIES_TO; re-run rank_reviewers and assert score 0 and rule key in trace, then verify the new triage does not recommend that person. Fake feedback cannot satisfy this integration test. Simulate feedback persistence failure: no success reply or re-triage.
- [ ] **Step 5: Commit** `B8: authorize reviewer rejects and retriage with module rules`. Owner acceptance requires an actual collaborator reject and non-collaborator ignore; store the run/rule keys and reply URL, not private credentials.

### Task 5: B8 graph-backed why, docs and final acceptance (~20 minutes)

**Interfaces:** consume existing `explain_path(tenant: str, from_key: str, to_key: str) -> ToolResult`; `format_why(path: PathData, links: dict[str, str], run_url: str) -> str`. Query `webhook_node_links.cypher` resolves only the returned keys' stored URLs in the current tenant; do not synthesize source URLs from untrusted text.

- [ ] **Step 1: Write failing tests**: collaborator why calls explain_path from the incoming issue key to the existing canonical Person key, renders keys and relationship types in order with known safe links and run URL; empty path says no evidence, no invented chain; unknown person/no run gets a safe explicit reply; non-collaborator and bot get no graph query or reply. All confirmation/why output has no live @mentions or upstream issue backlinks, and retry marker lookup prevents duplicate replies.
- [ ] **Step 2: Run** `python -m pytest tests/test_comment_commands.py -q`; expect missing why formatting/handling failures.
- [ ] **Step 3: Implement** why using existing tool evidence, `PathData` validation and stored URLs. Use sanitizer/code spans for GitHub source links where needed to prevent backlinks; preserve a clickable configured UI run URL. Explain empty or greater-than-four-hop evidence as absent, never compute a replacement graph path in Python. Document webhook setup for issues/issue_comment, collaborator token permission checks, secrets, one process, 100-job capacity, 60-second cooldown, restart/redelivery limits, command grammar, no auto-unassignment, and intentional public dry-run policy. Keep data/LLM credentials out of output.
- [ ] **Step 4: Run** `python -m pytest -q`; from `web/` run `npm run typecheck`, `npm run test:empty`, `npm run test:e2e`, `npm run test:e2e:api`, `npm run build`. Run `graphify update .`. Record actual pass/fail/skip output, not expected numbers. Refresh A8's inventory for every new query.
- [ ] **Step 5: With live-write permission**, rehearse issue → Keystone comment → exact UI run → collaborator reject → persisted Rule reply → changed triage, and collaborator why → evidence reply. Verify a signed non-collaborator command is ignored. Retain URLs, run/rule keys and sanitized output. Commit `B8: explain reviewer evidence and document live commands`. Owners alone tick B7/B8; no deployment or polling acceptance is claimed here.

## Self-review and approval gates

- All §13 webhook guards, queue semantics, incoming vector/provenance writes, live apply, safe run link and both collaborator commands map to tasks above.
- A10/A11 and their real adapter/recall route are blocking external deliverables, not hidden B work. Unit-test success alone cannot clear acceptance.
- The queue durability, tenant routing, rejection resolution and retry limits need approval from the coordination document before implementation.
- If scope exceeds about two hours per named task, first drop `why` per `docs/tasks.md`; do not cut security guards, live comment, persisted feedback or the working run link.
