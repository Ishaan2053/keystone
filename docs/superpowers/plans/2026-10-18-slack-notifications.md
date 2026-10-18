# Slack Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send evidence-backed issue triage, meaningful feedback follow-ups, and processing failures to one maintainer Slack channel using an environment-configured incoming webhook.

**Architecture:** An injected, best-effort notification coordinator connects the existing webhook/command pipelines and the validated UI feedback/re-triage path to a deterministic renderer and a one-attempt HTTP sender. Additive tenant-graph provenance distinguishes verified webhook-origin runs and their feedback descendants from ordinary manual runs. Keep existing triage, sandbox apply, authorization, retry reservations, and fixture behavior intact.

**Tech Stack:** Existing Python/FastAPI, Pydantic 2, httpx 0.28.1, named FalkorDB Cypher, pytest, Next.js 16/React 19/TypeScript, and Playwright. No new dependencies or services.

**Spec:** `docs/superpowers/specs/2026-10-18-slack-notifications-design.md` (approved by the request to start this plan).

## Global Constraints

- One shared maintainer channel, selected when creating the Slack incoming webhook.
- Configure the webhook through environment variables; no Slack OAuth, bot token, channel picker, or Slack API credentials.
- Send standalone follow-ups, not threads, after feedback changes a recommendation.
- Fixtures, unrelated manual runs, and dry-run apply previews never notify Slack.
- Delivery is best-effort: Slack failures never fail triage, roll back GitHub actions, or cause GitHub jobs to be retried.
- Preserve the UI's existing two steps: saving feedback does not re-triage; the maintainer clicks Re-triage separately.
- No additional LLM calls, dependencies, durable delivery queue, delivery guarantees, or broader GitHub admission/write permissions.
- Retain exactly-one-tenant webhook composition, sandbox-only writes, collaborator-only GitHub commands, existing issue cooldown, and public dry-run-only apply.
- `SLACK_WEBHOOK_URL` is optional, secret, backend-only, excluded from representations/logs; invalid configuration disables Slack with a sanitized warning.
- One HTTP attempt with a five-second timeout, no redirects, no retries; 10,000-entry bounded, thread-safe, same-process attempt deduplication.
- V1 has no authentication changes. The UI remains public; lineage validation is not collaborator authentication or an anti-abuse guarantee.
- Before changing Next.js code, read the installed relevant guides in `web/node_modules/next/dist/docs/` (resolve the package symlink if necessary), as required by `web/AGENTS.md`. If unavailable, install existing locked dependencies before implementing, not during this planning task.
- After product-code changes, run `graphify update .` as required by root `AGENTS.md`; if unavailable or requiring a new persistent build, report the blocker rather than silently rebuilding an index.

## Review Focus

1. An apply write succeeds but result persistence fails: retry the same run and same parent, preserve the original exception, and distinguish failure from later recovery (Tasks 4–5).
2. A delayed UI feedback response arrives after navigating to another tenant/run: it must not attach feedback context to the new view (Task 6).
3. Historic incoming issues, forged parent/feedback references, or a run in another tenant must never inherit notification eligibility (Tasks 2 and 6).
4. A title or citation includes Unicode, Slack mention syntax, or a huge URL: keep payloads bounded and inert, without dropping the full-run link (Task 3).
5. Webhook processing and UI re-triage race on the same issue: reserve immutable parent snapshots, atomically deduplicate attempts, and do not let a later completion change an in-flight comparison (Tasks 3–6).

---

## File map and decisions

New production files:
- `api/slack.py`: validated destination, secret-safe one-attempt HTTP sender.
- `api/notification_store.py`: tenant-scoped lineage, issue metadata and feedback validation through named queries; reuse `WebhookStore.recall_run` and `node_links` instead of duplicating their row mappers.
- `api/notification_messages.py`: immutable message models, deterministic semantic comparison, bounded Block Kit renderer.
- `api/notifications.py`: optional-sender coordinator, parent reservations, deduplication and sanitized exception boundaries.
- `cypher/notification_mark_run.cypher`, `cypher/notification_latest_run.cypher`, `cypher/notification_run_meta.cypher`, `cypher/notification_ui_parent.cypher`: additive queries; never embed Cypher in Python.

Existing production files modified:
- `api/config.py`: optional Slack setting and loader.
- `api/main.py`: shared coordinator construction/injection, app state and HTTP client lifecycle.
- `api/webhook_pipeline.py`: issue processing boundaries and outcomes.
- `api/comment_commands.py`: exact-parent feedback follow-ups, preserving existing authorization/reservations.
- `api/schemas.py`, `web/lib/types.ts`: optional feedback context on issue-key triage requests only.
- `api/routes/agent.py`: backend validation and best-effort follow-ups around explicit UI re-triage, not feedback submission.
- `web/components/workspace.tsx`: session-local successful-feedback context and explicit Re-triage request enrichment.
- `.env.example`, `docker-compose.yml`, `README.md`: deployment, secret handling and documented limits.

Tests are owned by the tasks below; no fixture JSON or existing memory schema rewrite is needed. New graph properties live only on existing `AgentRun` nodes. Do not change `agent.run_triage`, `memory.record_run`, or existing recall response contracts to carry notification state.

### Shared internal contracts

Define in `api/notification_messages.py`:
- `Cause = Literal['issue_opened', 'issue_edited', 'github_feedback', 'ui_feedback']`.
- Frozen `RunSnapshot(data: schemas.RecallRunData, title: str, replay_of: str | None)`; take a deep copy when reserving a parent so later feedback/action mutations cannot alter comparison.
- Frozen `NotificationAttempt(tenant: str, issue_key: str, event_id: str, cause: Cause, title: str, replay_of: str | None, parent: RunSnapshot | None)`.
- Frozen `NotificationMessage(attempt: NotificationAttempt, current: RunSnapshot | None, applied: schemas.ApplyResult | None, failure_stage: Literal['ingest', 'triage', 'apply'] | None, recovered: bool = False)`.

`event_id` is server-constructed: `delivery:<X-GitHub-Delivery>` for issue jobs, `comment:<comment_id>` for GitHub feedback, or `ui:<parent-run-key>:<feedback-key>` for UI transitions. Tenant and canonical issue are separate mandatory identity fields. Never include raw tokens, request bodies, feedback reasons, or arbitrary exception text in these models.

### Task 1: Optional configuration and secret-safe Slack transport

**Files:** Create `api/slack.py`, `tests/test_slack.py`, `tests/test_slack_config.py`; modify `api/config.py`, `.env.example`, `docker-compose.yml`; create `tests/test_compose_slack.py`.

**Interfaces:**
- Produces `validate_webhook_url(value: str | None) -> str | None` and `SlackSender(webhook_url: str, client: httpx.Client).send(payload: dict) -> bool` in `api/slack.py`.
- Produces `Settings.slack_webhook_url: str | None = field(default=None, repr=False)`, loaded from `SLACK_WEBHOOK_URL`.

- [ ] **Step 1: Write failing configuration/transport tests.** Use `httpx.MockTransport`, `monkeypatch` and `caplog`; no live webhook. Assertions:

```python
def test_secret_setting_is_optional_and_redacted(monkeypatch):
    monkeypatch.delenv('SLACK_WEBHOOK_URL', raising=False)
    assert load_settings().slack_webhook_url is None
    monkeypatch.setenv('SLACK_WEBHOOK_URL', 'https://hooks.slack.com/services/T/B/secret')
    settings = load_settings()
    assert settings.slack_webhook_url is not None
    assert 'secret' not in repr(settings)
```

Add `test_invalid_url_disables_without_echoing_secret` parameterized over HTTP, foreign/suffix hosts, userinfo, explicit ports, query, fragment, empty path, and encoded path separators. Assert `None` and a fixed warning, with neither input nor token in logs. `test_send_success` asserts exactly one POST, JSON payload unchanged, no Authorization header, disabled redirects, and five-second timeout settings. `test_send_failures_are_one_attempt_and_sanitized` covers 301, 429, 500, 200 with non-`ok` body, connection/timeout/client exceptions: returns `False`, one attempt, no response body/URL/exception message in logs. For success require HTTP 200 and stripped body `ok`.

- [ ] **Step 2: Verify red.** Run `python -m pytest tests/test_slack.py tests/test_slack_config.py tests/test_compose_slack.py -q`; expect missing Slack symbols/setting or missing Compose mapping, not an external network failure.
- [ ] **Step 3: Implement the interfaces.** Validate stripped `https://hooks.slack.com/services/<nonempty>/<nonempty>/<nonempty>` URLs with no userinfo/port/query/fragment/params or percent escapes. Validation raises no value-bearing errors. Use existing httpx with `timeout=5.0`, `follow_redirects=False`, no retries; suppress low-level `httpx`/`httpcore` request logging for the owned Slack client so INFO/DEBUG logs cannot expose the secret path. Catch notification transport exceptions and log fixed category/status only. Add `SLACK_WEBHOOK_URL=` to `.env.example`, and `${SLACK_WEBHOOK_URL:-}` to the API container environment, never to `NEXT_PUBLIC_*` or the web service. Document that the httpx timeout is a network-operation timeout, not an exactly-five-second wall-clock guarantee.
- [ ] **Step 4: Verify green.** Re-run Step 2. The Compose test follows `tests/test_compose_llm_cache.py`/`tests/test_infra.py`: use a dummy webhook value, assert its API mapping and absence from frontend settings; Docker unavailable is a reported verification gap, not a pass. Add a test at INFO/DEBUG logging levels proving the full secret URL never appears.
- [ ] **Step 5: Commit.** `git add api/slack.py api/config.py tests/test_slack.py tests/test_slack_config.py tests/test_compose_slack.py .env.example docker-compose.yml` then `git commit -m "feat: add optional secret-safe Slack webhook transport"`.

### Task 2: Persist and validate webhook-origin lineage

**Files:** Create `api/notification_store.py`, the four queries in the file map, `tests/test_notification_store.py`, `tests/test_notification_store_integration.py`; create the shared immutable models in `api/notification_messages.py`.

**Interfaces:** `NotificationStore(sandbox_repo: str, graph_factory: Callable[[str], object] = tenant_graph)` provides:
- `load_run(tenant: str, run_key: str) -> RunSnapshot | None`.
- `latest_eligible(tenant: str, issue_key: str) -> RunSnapshot | None`.
- `eligible_parent(tenant: str, issue_key: str, parent_run_key: str, *, ui_feedback_key: str | None = None) -> RunSnapshot | None`.
- `mark_run(tenant: str, run_key: str, *, issue_key: str, cause: Cause, event_id: str, parent_run_key: str | None) -> bool`.
- `node_links(tenant: str, keys: list[str]) -> dict[str, str]` (delegate to existing store).

- [ ] **Step 1: Write failing store tests.** Use the recording-graph style in `tests/test_webhook_store.py`. Pin named-query parameters and mapping, plus these real-FalkorDB assertions in a dedicated disposable `slacktest` tenant graph:

```python
def test_lineage_survives_new_store_instance(store, new_store, tenant, issue_key, run_key):
    assert store.mark_run(tenant, run_key, issue_key=issue_key,
                          cause='issue_opened', event_id='delivery:d1', parent_run_key=None)
    assert new_store.latest_eligible(tenant, issue_key).data.run.run_key == run_key
```

`test_ui_parent_requires_exact_feedback_and_origin`: assert `None` for an unmarked historic/incoming/manual run, different tenant/issue, unknown feedback, feedback on a different parent's Action, or `given_by != 'ui'`; accept both persisted verdicts. `test_mark_does_not_fabricate_nodes_or_reparent_run`: missing HANDLED run returns `False`; valid repeated write preserves original event/parent/time; conflicting reparent returns `False`. `test_latest_eligible_ignores_later_manual_runs` selects eligible feedback descendants; tie-break deterministically by key. `test_snapshot_is_detached` mutates recalled actions after reservation and asserts parent unchanged.

- [ ] **Step 2: Verify red.** Run `python -m pytest tests/test_notification_store.py tests/test_notification_store_integration.py -q`; expect missing symbols/queries. Integration follows the dedicated-graph lifecycle of `tests/test_webhook_integration.py`; only delete `slacktest`, never configured application graphs. Unreachable FalkorDB is a skip and remains unverified.
- [ ] **Step 3: Implement models, queries and store methods.** Use properties `notify_root`, `notify_parent`, `notify_event`, `notify_cause`, `notify_at` on existing `AgentRun`. Parentless issue events set `notify_root` to their run key; all eligible descendants, including issue edits with a parent, inherit the exact parent's root. Parentless `issue_edited` may establish the first origin. All mark operations MATCH an existing HANDLED incoming issue in the configured sandbox and validate its canonical key; descendants additionally MATCH same-issue proven parent. Only issue jobs may create roots. Validate same issue on metadata/recall mapping; exclude adhoc runs. Set `notify_at` once using supplied integer milliseconds, order latest by that then key, and do not change it on retries. Resolve title and actual REPLAY_OF key in `notification_run_meta`; never infer a replay from arbitrary body text in Slack. `notification_ui_parent` additionally MATCHes `parent-[:PROPOSED]->action<-[:ON]-feedback` and `feedback.given_by='ui'`. Use graph parameters, not interpolated query text. Reuse existing recall/URL readers and deep-copy returned snapshots.
- [ ] **Step 4: Verify green.** Re-run Step 2 and `python -m pytest tests/test_webhook_store.py tests/test_contracts.py -q`. Confirm real-query tests pass with FalkorDB, or explicitly record that acceptance is pending. Store exceptions are allowed here; the coordinator owns fail-closed boundaries.
- [ ] **Step 5: Commit.** Stage the Task 2 files explicitly; `git commit -m "feat: persist validated Slack notification lineage"`.

### Task 3: Render bounded notifications and coordinate best-effort attempts

**Files:** Modify `api/notification_messages.py`; create `api/notifications.py`, `tests/test_notification_messages.py`, `tests/test_notifications.py`.

**Interfaces:**
- `recommendation_fingerprint(run: RunSnapshot) -> str` and `render_message(message: NotificationMessage, *, public_base_url: str, evidence_links: dict[str, str]) -> dict` in `api/notification_messages.py`.
- `SlackNotifications(store: NotificationStore, sender: SlackSender | None, *, public_base_url: str, limit: int = 10_000)` with:
  - `begin_issue(*, tenant: str, issue_key: str, delivery_id: str, action: Literal['opened', 'edited'], title: str, replay_of: str | None) -> NotificationAttempt | None`.
  - `begin_github_feedback(*, tenant: str, issue_key: str, parent_run_key: str, comment_id: int) -> NotificationAttempt | None`.
  - `begin_ui_feedback(*, tenant: str, issue_key: str, parent_run_key: str, feedback_key: str) -> NotificationAttempt | None`.
  - `complete(attempt: NotificationAttempt | None, *, run_key: str, applied: schemas.ApplyResult | None) -> None`.
  - `failed(attempt: NotificationAttempt | None, *, stage: Literal['ingest', 'triage', 'apply'], run_key: str | None = None) -> None`.

- [ ] **Step 1: Write failing renderer/coordinator tests.** Assertion examples, using local `make_snapshot` and recording-sender fixtures:

```python
def test_fingerprint_ignores_execution_metadata(base, reordered):
    assert recommendation_fingerprint(base) == recommendation_fingerprint(reordered)

def test_changed_reviewer_or_citations_changes_fingerprint(base, changed):
    assert recommendation_fingerprint(base) != recommendation_fingerprint(changed)
```

`reordered` changes trace timings/order, run IDs, action IDs/statuses, comment/summary prose, list order, duplicates and login case, but not recommendations. Parameterize `changed` over reviewer login/module/citations, label value/citations, risk and decision text/citations. `test_render_caps_and_inert_content` uses 100 long Unicode claims and strings `<!channel>`, `<@U123>`, `<!subteam^S123>`, `@here`, `@channel` and forged links; assert no untrusted content in mrkdwn, <=3 claims/category, <=5 evidence entries, JSON UTF-8 <=24,000 bytes, and retained run link. `test_statuses_and_empty_evidence_are_honest` covers fallback, missing citations, partial refusal, UI no-apply and sanitized apply failure with available triage. `test_same_process_attempt_is_atomic` calls the same event concurrently and asserts one sender call. `test_failed_send_is_not_retried` asserts one call even when sender raises; `test_processing_failure_then_recovery` permits one failure plus one recovery. `test_parent_is_reserved_before_concurrent_completion` asserts repeated begin returns the same detached parent. `test_notification_dependencies_cannot_break_core` parameterizes store lookup/write, evidence lookup, formatter and sender exceptions; public coordinator methods never raise. `test_bounded_caches` uses `limit=2`, asserts FIFO eviction and documents possible resend after eviction.

- [ ] **Step 2: Verify red.** Run `python -m pytest tests/test_notification_messages.py tests/test_notifications.py -q`; expect absent functions/coordinator.
- [ ] **Step 3: Implement fingerprint and renderer.** Canonical sorted unique tuples from all recorded label/assign actions regardless of later status; join their value/module identities to triage claim citation sets. Strip whitespace, case-fold reviewer logins and strip leading `@`; preserve label/module case. Normalize risk/decision text by whitespace only; include their sorted citation keys. Hash canonical JSON with SHA-256. Do not parse `comment_markdown` for identities. Renderer uses plain_text blocks for untrusted content (including neutralized `@` using a zero-width separator), application-built mrkdwn only for validated links, top-level fixed text, `parse='none'`, `link_names=False`, and both unfurls disabled. Cap title at 160 characters, summary at 500, each claim at 240, each citation key at 160, each change entry at 180, and each validated URL at 2,048. Use at most 3 change entries and 5 evidence entries; cap each plain_text block at 2,500 UTF-8 bytes and total JSON at 24,000 bytes. Trim optional evidence/claims/changes before core identity/status/run link, append explicit truncation/full-run notice. Build encoded `/t/<tenant>?run=<uuid>` links from `PUBLIC_BASE_URL`; allow evidence URLs only for stored HTTPS `github.com` URLs without userinfo or Slack markup characters. Keys without safe URLs remain plain text. Failure-only messages require no graph read and contain fixed stage copy, not exception text.
- [ ] **Step 4: Implement coordinator methods.** `begin_issue` reserves latest eligible parent for either issue action; no parent means initial. `begin_github_feedback`/`begin_ui_feedback` validate exact eligible parent (UI also exact feedback). Keep thread-safe FIFO reservation and attempted-outcome caches, each at most `limit`; do not hold locks during graph/HTTP work. Reservations must atomically select one immutable parent per trusted event even if two callers race. `complete` marks lineage before rendering/sending, even with sender disabled, reloads current run and compares against reserved parent, skips unchanged follow-ups unless recovering from a recorded core failure. Catch and sanitize all notification-only exceptions; missing/broken provenance suppresses success/follow-up rather than guessing. `failed` requires an already-eligible attempt, uses available current triage for apply failures, otherwise fixed stage copy; it never writes success lineage without a persisted run. Deduplicate by `(tenant, issue_key, event_id, outcome)` where outcome is `completed` or `failed:<stage>`; record an attempt before HTTP even if it fails. On successful core completion release its parent reservation; keep attempted-outcome entries. Preserve failed processing reservations for retry. Only recovery from a core failure—not a failed Slack send—permits the additional recovery message. All `None` attempts are no-ops.
- [ ] **Step 5: Verify green.** Re-run Step 2 plus Tasks 1–2 unit tests. Assert no LLM/tool invocation in renderer/coordinator tests and no external HTTP.
- [ ] **Step 6: Commit.** Stage Task 3 files; `git commit -m "feat: coordinate bounded best-effort Slack notifications"`.

### Task 4: Connect verified issue jobs and application lifecycle

**Files:** Modify `api/webhook_pipeline.py`, `api/main.py`; extend `tests/test_webhook_pipeline.py`, `tests/test_webhook_retry_runs.py`; create `tests/test_slack_lifecycle.py`, `tests/test_slack_webhook_flow.py`.

**Interfaces:** Consume Task 3 coordinator; add optional `notifications: SlackNotifications | None = None` to `WebhookPipeline`, `_build_webhook_worker` and `create_app`. Publish the shared object as `app.state.notifications`. Add optional injection `slack_client: httpx.Client | None = None` to `create_app` for MockTransport tests; application owns/closes only its internally created client.

- [ ] **Step 1: Write failing pipeline/lifecycle tests.** `test_signed_opened_job_notifies_after_apply` asserts one triage message and actual statuses. `test_rejected_duplicate_and_upsert_only_jobs_are_silent` covers bad signatures, unsupported/bot/out-of-sandbox events, duplicate deliveries, and `triage=False`. `test_processing_stage_failures_preserve_exception` injects ingestion/embedding, triage and apply failures and asserts the exact same exception instance escapes, downstream stages remain uncalled, and only one stage-specific message is attempted. `test_apply_retry_preserves_run_and_notification_parent` uses the existing pending-run failure fixture: one triage, same run on apply retry, same parent despite an intervening eligible completion, one failure message plus recovery, no duplicate comment. `test_issue_edit_changed_vs_unchanged` asserts only meaningful changed follow-up; no proven parent produces initial. `test_partial_refusal_and_fallback_render_truthfully` checks no full-success wording. `test_injected_notifier_exception_does_not_fail_job` deliberately supplies a throwing stub to exercise call-site isolation too. Lifecycle tests assert fixture mode constructs neither sender nor store/client; live missing/invalid Slack URL has no HTTP; owned client closes after worker drain and injected client remains open.
- [ ] **Step 2: Verify red.** Run `python -m pytest tests/test_slack_lifecycle.py tests/test_slack_webhook_flow.py tests/test_webhook_pipeline.py tests/test_webhook_retry_runs.py -q`; new assertions fail because hooks do not exist.
- [ ] **Step 3: Implement issue processing hooks.** Resolve canonical sandbox issue key from construction-time configuration before embedding/upsert, so ingest failure has trusted identity. Establish attempt before ingest; guard notification preparation separately so its failures do not enter core failure handling. Wrap only existing core stages with `failed(...stage...)` plus bare re-raise; after apply returns, call `complete(..., applied=result)`. On apply error include the pending persisted run key; never release `PendingTriageRuns` early or re-triage to render Slack. Success message includes ingest/triage/apply outcome as specified; no separate optimistic message before apply. Upsert-only jobs do not prepare or notify. Existing partial-refusal logging stays intact.
- [ ] **Step 4: Compose shared coordinator/client.** In live single-tenant sandbox composition, build `NotificationStore` and coordinator once, sender only for valid nonblank URL. Do not open/connect graph at construction. Share this object with pipeline, command lane (wired in Task 5) and app state. Record lineage when Slack transport is disabled so enabling it later can recognize proven runs; all such optional graph failures remain isolated. Catch builder/client-construction errors and disable Slack without disabling the webhook worker. For fixture mode set state to `None` regardless of injected notifier/client/settings. Close an owned sender client in lifespan after worker drain, including startup-error paths; no client creation per event. Calls into injected notification methods also need safe fixed-log boundaries so test/custom adapters cannot change core results.
- [ ] **Step 5: Verify green.** Re-run Step 2 and `python -m pytest tests/test_webhooks.py tests/test_webhook_intake.py tests/test_webhook_worker.py -q`; existing authorization, cooldown, deduplication and retry assertions must remain passing.
- [ ] **Step 6: Commit.** Stage Task 4 files; `git commit -m "feat: notify Slack for verified issue triage outcomes"`.

### Task 5: Connect GitHub feedback follow-ups without changing command semantics

**Files:** Modify `api/comment_commands.py`, `api/main.py`; extend `tests/test_comment_commands.py`, `tests/test_command_feedback_errors.py`, `tests/test_webhook_retry_runs.py`; create `tests/test_slack_comment_commands.py`.

**Interfaces:** Add optional `notifications: SlackNotifications | None = None` to `CommentCommandLane`. Extend private `_retriage(self, job: WebhookJob, issue_key: str, parent_run_key: str) -> None`; obtain the parent from `_resolve`'s exact reserved run, never latest lookup after feedback.

- [ ] **Step 1: Write failing command tests.** `test_reject_changed_recommendation_notifies` asserts event ID based on comment ID and exact-parent before/after. `test_unchanged_retriage_is_silent` ignores changed timings/comment. `test_why_ignored_and_unauthorized_commands_are_silent` covers `/why`, malformed commands, noncollaborator/bot, absent assignment, absent rule, refused feedback, and unproven parent. `test_command_retry_keeps_original_parent_and_run` injects write-then-raise apply failure followed by another run completing; assert same original parent/run, no repeated feedback, one failure and one recovery. `test_feedback_preparation_and_slack_failures_do_not_change_command` asserts existing confirmation/re-triage/apply behavior even if all notification adapters throw. `test_eligible_retriage_failure_notifies_and_reraises` checks the exact exception instance; authorization/feedback-resolution failures send nothing.
- [ ] **Step 2: Verify red.** Run `python -m pytest tests/test_slack_comment_commands.py tests/test_comment_commands.py tests/test_command_feedback_errors.py tests/test_webhook_retry_runs.py -q`.
- [ ] **Step 3: Implement exact-parent hooks.** After existing authorized `_resolve` and successful confirmation, begin GitHub feedback notification with the resolved parent run and comment ID. Pass that identity through `_retriage`; use stage-separated boundaries as in Task 4 and `complete` only after apply. Preserve `_resolved` reservations, command confirmation markers, pending-run lifetime, no automatic assignee removal, and existing collaborator checks. A notification failure cannot suppress the GitHub confirmation or live apply. Wire the shared coordinator from app composition.
- [ ] **Step 4: Verify green.** Re-run Step 2; confirm retries neither create a new triage solely for Slack nor emit a success before apply outcome is known.
- [ ] **Step 5: Commit.** Stage Task 5 files; `git commit -m "feat: send Slack follow-ups for GitHub feedback retriage"`.

### Task 6: Bind UI follow-ups to persisted feedback and explicit Re-triage

**Files:** Modify `api/schemas.py`, `api/routes/agent.py`, `web/lib/types.ts`, `web/components/workspace.tsx`; extend `tests/test_contracts.py`, `tests/test_agent_routes.py`, `web/tests/triage-ask-feedback.spec.ts`; create `tests/test_slack_ui_feedback.py`. Update `web/tests/fake-api.ts` only if its request validator needs the additive fields.

**Interfaces:**
- Add `feedback_parent_run: UUID | None = None` and `feedback_key: str | None = None` to Python `TriageRequest`; matching optional string/null fields in TypeScript. Both must be supplied together, with a canonical `issue_key` mode only. Whitespace/blank feedback keys are invalid. Run UUID is prefix-free on the wire; backend expands it to `run:<uuid>`.
- Consume `app.state.notifications.begin_ui_feedback` before `agent.run_triage`, then `complete`/`failed` on that exact attempt; no GitHub apply call.

- [ ] **Step 1: Write failing request/backend tests.** `test_feedback_context_requires_pair_and_full_issue_key` pins valid UUID pair and rejects single fields, blank feedback, invalid UUID, number/title mode plus context with 422. Existing number/title/key requests remain valid. `test_ui_save_feedback_is_silent` posts feedback only and asserts zero notification calls. `test_validated_retriage_notifies_only_for_changed_result` checks same-tenant exact parent, persisted UI feedback and proven origin, while returning unchanged `TriageResult` contract. `test_invalid_lineage_remains_silent_manual_triage` parameterizes unknown/foreign/historical parent, other-issue feedback and feedback from GitHub: core triage still returns its normal result, no Slack and no new provenance; syntactically valid but ineligible context is not a new API failure. `test_manual_run_with_webhook_issue_is_silent` sends only issue_key and asserts no notification eligibility. `test_ui_followup_failure_is_sanitized_and_preserves_503` only sends a failure when begin established eligibility. `test_notification_failures_cannot_change_ui_result` includes store failure before agent and formatter failure after persistence. `test_raw_title_feedback_remains_manual` verifies the existing adhoc flow unchanged.
- [ ] **Step 2: Verify backend red.** Run `python -m pytest tests/test_contracts.py tests/test_agent_routes.py tests/test_slack_ui_feedback.py -q`.
- [ ] **Step 3: Implement backend context handling.** Keep fields additive and validate pairs in `TriageRequest` without breaking exact input modes or default `body`. Add FastAPI `Request` to the triage route to access app state; do not put notifier in agent globals. Syntactically valid references that fail server-side lookup/validation, or a failed notification lookup, fall back to ordinary silent triage. Obtain a validated immutable attempt before the agent call, and wrap only eligible core triage failures in `failed(stage='triage')` before original error translation. After a persisted result, call `complete(..., applied=None)`; no automatic apply and no notification code in the feedback-saving route. Existing unknown-tenant/404/409/503 behavior stays intact.
- [ ] **Step 4: Add failing frontend assertions.** Update the existing successful reject/Re-triage test to expect `{issue_key: KNOWN_RUN_ISSUE, feedback_parent_run: KNOWN_RUN, feedback_key: <returned feedback key>}`; assert no triage request before clicking Re-triage. Add `delayed feedback cannot enrich another run`, `failed feedback never supplies context`, `Run triage stays manual after feedback`, `reloading drops pending context`, and `retrying failed retriage keeps the same feedback identity`. Raw-title tests still expect only title/body, never lineage fields. Capture the fake API's actual returned feedback key, not a guessed constant.
- [ ] **Step 5: Verify frontend red.** In `web`, run `npm run test:e2e:api -- triage-ask-feedback.spec.ts`; expect the newly asserted request fields/guards to fail before changes, not dependency/server errors.
- [ ] **Step 6: Implement session-local UI context.** Read applicable installed Next.js guides first. Capture tenant/run/request-generation when sending feedback; attach the returned feedback key to that exact full-issue run only after successful response, and ignore stale responses. Pass the pair only from explicit Re-triage for that same run; never from Run triage or raw-title paths. Preserve context across a failed triage retry, clear after successful transition, navigation/tenant/run change, and reload; never read a global latest feedback value. This is request plumbing, not a UI redesign: labels, button behavior, and no-auto-retriage stay unchanged. Preserve existing abort/navigation guards.
- [ ] **Step 7: Verify green.** Re-run Steps 2 and 5, plus `npm run typecheck` in `web`. Ensure all existing fixture and raw-title request assertions still pass.
- [ ] **Step 8: Commit.** Stage Task 6 files; `git commit -m "feat: notify Slack after validated UI feedback retriage"`.

### Task 7: Verify composed behavior and document rollout

**Files:** Extend `tests/test_slack_webhook_flow.py`, `tests/test_slack_ui_feedback.py`, `tests/test_slack_lifecycle.py`; modify `README.md`.

**Interfaces:** Consume the composed `create_app(..., slack_client=mock_client)` with real notification coordinator/store adapters (recording graph or disposable FalkorDB), actual signed webhook route and stubbed agent/GitHub writer. No new production interface.

- [ ] **Step 1: Write final failing acceptance tests.** `test_signed_issue_to_feedback_round_trip` sends a signed opened job, waits for worker completion, then authorized GitHub rejection and UI saved feedback/Re-triage; assert initial and changed standalone follow-ups with real issue/run IDs, persisted origin and truthful action statuses. `test_restart_restores_lineage_not_delivery_guarantees` composes a new app/store against the same test graph and validates follow-up eligibility, without expecting transport deduplication across processes. `test_shutdown_drains_before_slack_client_close` asserts close order. `test_no_secret_in_logs_at_debug` exercises successful and failing composed requests at DEBUG for app/httpx/httpcore. `test_core_recovery_after_apply_failure` ensures only two attempts (failure/recovery), even with an unchanged comparison fingerprint. Extend the Compose test to prove no Slack secret is placed in public frontend configuration.
- [ ] **Step 2: Verify red.** Run `python -m pytest tests/test_slack_webhook_flow.py tests/test_slack_ui_feedback.py tests/test_slack_lifecycle.py -q`; each new test must expose a specific missing acceptance behavior. If already green, record that as acceptance coverage rather than inventing a failing production change.
- [ ] **Step 3: Document rollout and any integration correction revealed by tests.** Add a README section covering: create incoming webhook bound to the shared channel; store `SLACK_WEBHOOK_URL` only in backend environment/secret storage; retain current live webhook prerequisites and sandbox restriction; restart/recreate API after changes; blank removes delivery; invalid config warns and disables only Slack. Explain initial, issue-edit and feedback messages, explicit UI Re-triage, missing historical lineage, replay labeling, five-second network-operation timeout, one attempt/no retries, unauthenticated UI, no guaranteed or cross-process exactly-once delivery, and webhook-secret rotation. Include a manual smoke checklist using a dedicated test channel and a real signed sandbox issue; do not execute it or contact Slack without maintainer approval. Correct only test-demonstrated wiring defects; no scope expansion.
- [ ] **Step 4: Verify all unit/regression gates.** Run `python -m pytest -q` at repo root, `npm run typecheck`, `npm run test:e2e`, and `npm run test:e2e:api` in `web`, and `git diff --check`. Expected: zero failures and no whitespace errors. Record skipped FalkorDB/Docker/browser tests as unverified, never as acceptance. Use existing installed/locked dependencies; do not update dependency versions to make unrelated failures disappear. Run `graphify update .` after code changes; if the tool/index is unavailable, report it and do not create an unauthorized persistent index. Inspect any graph output rather than staging it indiscriminately.
- [ ] **Step 5: Commit.** Stage README and acceptance-test changes explicitly, plus any narrowly necessary wiring fix; `git commit -m "test: verify Slack integration and document rollout"`. Review the final diff for accidental secrets, new live-write paths, and unrelated changes.

## Plan self-review

- [x] Spec coverage: initial/edit notifications (Task 4), both feedback sources (Tasks 5–6), failures/recovery (Tasks 3–7), config/deployment (Tasks 1 and 7), provenance/restart (Tasks 2 and 7), safety/caps (Task 3).
- [x] Step scan: test names/assertions, exact interfaces, file ownership and red/green/commit gates specified. Fixture construction follows current tests; no implementation bodies transcribed.
- [x] Type consistency: all orchestration uses the same attempt/snapshot/message models; existing `TriageResult`/`FeedbackResult` stay unchanged; optional wire UUID is expanded only at the route boundary.
- [x] Review Focus: each of the five conditions is assigned explicit tests above, including concurrent reservation/deduplication and stale frontend feedback.
- [x] Proportion: one integrated feature, seven independently reviewable deliverables, no unrelated subsystem or dependency installation in this planning commit.
- [ ] Maintainer reviews the plan and chooses Native or Subagent-driven execution before product implementation.
