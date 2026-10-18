# Slack triage notifications — v1 design

Status: approved for implementation planning by the maintainer's request to write and commit the plan. Product implementation awaits plan review and an execution-method choice.

## Intent

Give maintainers one shared Slack triage channel where they can understand a new issue's triage, inspect the evidence, notice revised recommendations after feedback, and see when processing needs attention without watching Keystone continuously.

## Agreed requirements

- One shared maintainer channel, selected when creating the Slack incoming webhook.
- Configure the webhook through environment variables; no Slack OAuth, bot token, channel picker, or Slack API credentials.
- Send an evidence-backed message after real GitHub webhook-triggered issue triage.
- Send standalone follow-ups, not threads, after feedback changes a recommendation.
- Support feedback from both GitHub `/keystone reject` and Keystone's UI, but only for issues originally triaged through a verified GitHub webhook.
- Preserve the UI's existing two steps: saving feedback does not re-triage; the maintainer clicks Re-triage separately.
- Fixtures, unrelated manual runs, and dry-run apply previews never notify Slack.
- Delivery is best-effort: Slack failures never fail triage, roll back GitHub actions, or cause GitHub jobs to be retried.

## Existing system and boundaries

`api/webhooks.py` verifies and admits GitHub events, with sandbox/bot guards, delivery deduplication, and issue cooldown. `api/webhook_pipeline.py` composes incoming-issue upsert, persisted triage, and sandbox apply. `api/comment_commands.py` authorizes collaborator commands and re-triages after a persisted rejection rule. `api/routes/agent.py` handles manual triage and UI feedback; `web/components/workspace.tsx` calls feedback and Re-triage independently.

The existing webhook worker supports exactly one configured tenant and writes only to `SANDBOX_REPO`. V1 preserves those restrictions. It does not enable upstream production writes or broaden GitHub event admission. A genuine signed sandbox webhook, including an admitted replay issue, qualifies; a replay is identified as such rather than presented as an upstream write.

No graphify graph or zvec index is present in this worktree; existing source and repository documentation informed this design. Creating an index is not part of this work.

## Notification rules

### Initial triage

The first eligible issue triage produces one initial message after processing reaches its outcome. Include the persisted run's triage even if GitHub apply subsequently fails, but clearly mark the apply failure. If triage itself fails, send a failure notification without pretending that a summary or evidence exists.

Eligibility comes only from the verified webhook job and server-owned provenance, never a client-supplied `notify` flag, an issue number alone, or the existence of an incoming Issue node. Store successful webhook-origin run lineage in the tenant graph so UI eligibility survives application restart. Do not infer eligibility for historical runs without recorded provenance.

Design call: an accepted `issues.edited` event that actually runs triage is also eligible. If an initial eligible result already exists, send a follow-up only for a changed recommendation and label its cause as an issue edit. Upsert-only cooldown jobs send nothing. This uses the existing supported issue lifecycle instead of introducing new GitHub events.

For an edit, reserve the latest successfully persisted eligible result for the same tenant and canonical issue immediately before starting triage. This includes validated feedback descendants and excludes unrelated manual runs. Preserve this exact parent with any pending run across same-process apply retries. Later concurrent UI completions do not change the reserved comparison baseline. If no proven eligible parent exists, treat the edit as the first eligible triage. Restart/eviction loses pending reservations under the existing bounded retry limitation, not a claim of cross-process ordering.

### GitHub feedback

After the existing collaborator-only reject command successfully records feedback, creates/reuses a rule, re-triages, and attempts apply, compare the new result with the exact parent run resolved for that command. Notify only if the recommendation changes. No notification for ignored commands, `/keystone why`, authorization refusals, or feedback which produces no re-triage.

Preserve the command's existing retry reservation and pending-run behavior. Do not resolve a different latest run on retry merely to build a Slack message.

### UI feedback

Saving feedback alone sends nothing. On the subsequent explicit Re-triage, include a parent-run reference only when the current UI flow has successfully saved feedback for that run. The backend must validate the same tenant, exact parent run, same canonical issue, persisted feedback, and webhook-origin lineage before treating the request as a feedback follow-up. The reference is not authority by itself. The resulting run inherits lineage only after those checks; ordinary manual triage remains silent even for an issue that previously had a webhook run.

The UI re-triage remains read-only with respect to GitHub: no new live apply behavior. Its notification says recommendations were updated, not that GitHub assignments or labels were changed. Reloading before clicking Re-triage may lose the UI's pending-feedback context in v1; an ordinary Re-triage after that stays silent unless the UI explicitly restores and submits validated feedback context.

Both accepted and rejected persisted feedback may establish UI feedback context; notification still requires an actual changed recommendation. Validation failures must not turn a normal manual triage into a notification-capable run.

### Meaningful change

Compare the new result against its server-validated parent result, not against the last successfully delivered Slack message. A failed Slack send must not change the meaning of later triage comparisons.

Design call: compare a canonical representation of proposed label values, reviewer logins with modules, and risk/decision claims with citation keys. Label/reviewer identity comes from recorded proposed actions rather than parsing explanatory prose. Normalize surrounding whitespace, GitHub-login case, duplicates and ordering. Ignore run IDs, timestamps, tool timings, trace ordering, comment wording, and reviewer/label explanatory prose. Changed risk/decision text or citation sets do count. Different evidence for an otherwise identical reviewer counts if its claim citation set changes. A change in trace alone does not count.

### Failure notifications

Report failures of an admitted eligible issue-processing job after admission: issue ingestion/upsert, triage, or apply. For feedback, report re-triage/apply failures only once the command or UI follow-up is established as eligible. Invalid signatures, malformed requests, unauthorized/ignored commands, rejected lineage references, fixture failures, and unrelated manual failures never notify.

Use sanitized stage descriptions and issue/run links when available. Do not post exception messages, tracebacks, tokens, raw issue bodies, feedback reasons, graph connection details, or HTTP response bodies. Preserve all existing core exception and retry semantics.

If triage succeeds but apply fails, one message for that attempt includes the available triage and an explicit apply-failure status, rather than separate success and failure messages. Partial refused/dry-run action results are visible as outcomes and never described as full application success. A completed fallback triage is labeled as fallback/degraded, not described as an exception failure.

## Message content

Use deterministic formatting of persisted results; no additional LLM calls.

- Tenant, repository, issue number/title, event cause, and real GitHub issue URL.
- A concise persisted summary, suggested labels/reviewers, important risks and related decisions.
- Up to three items per claim category and up to five distinct cited evidence keys, with clear truncation and a link to the full Keystone run. Do not invent source URLs or graph paths; use stored verified evidence where available and otherwise show citation keys.
- A Keystone run link built from the existing `PUBLIC_BASE_URL` and encoded tenant/run identifiers.
- Initial/GitHub follow-up messages distinguish recommendations from actual `applied`, `dry_run`, or `refused` outcomes. UI follow-ups explicitly say no GitHub actions were applied by that re-triage.
- Follow-ups identify the parent run and the cause, and include a compact before/after description of changed recommendations.
- Missing evidence is explicitly unavailable; never fill gaps with fixtures.

Keep the payload below Slack limits using fixed application caps, plain-text sections for untrusted content, disabled automatic parsing and disabled link/media unfurls. Untrusted titles, summaries, claim text, usernames and citations must not create Slack user, user-group, channel, `@here`, or `@channel` mentions. Only application-built, validated URLs are clickable. Do not paste raw `comment_markdown` directly into Slack markup.

## Configuration and delivery

Design calls proposed for approval:

- `SLACK_WEBHOOK_URL`: optional secret; blank means disabled. Exclude it from settings representations and logs. No separate channel variable, since the incoming webhook selects the destination.
- Accept only an HTTPS incoming-webhook URL on `hooks.slack.com` with a nonempty `/services/...` path; reject credentials, query strings, fragments, and other hosts. Government Slack is outside v1. Validate without echoing any URL or token in errors.
- An invalid configured Slack URL disables Slack with a sanitized configuration warning, rather than preventing core triage startup.
- Use one bounded HTTP attempt per notification with a five-second network-operation timeout, no redirects, and no retries for timeouts, network errors, 429s, or 5xxs. The existing httpx dependency's timeout is per network operation, not a strict five-second total wall-clock deadline. Slack success requires its documented successful response; anything else is a sanitized delivery failure. No real Slack call in tests.
- A small injected notifier keeps formatting/delivery separate from webhook and feedback orchestration and allows deterministic tests. Fixture mode never constructs an active notifier.
- Bound same-process notification-attempt deduplication to 10,000 recent event identities, using trusted tenant, issue, delivery/comment or validated UI transition identity, and outcome type. Mark attempts even on ambiguous transport failure: v1 favors avoiding duplicate pings over retrying a potentially delivered message. Distinguish a later successful recovery from an earlier processing failure so recovery can notify.
- Do not add a durable delivery queue, retry scheduler, background service, or delivery database. Crash, restart, bounded-cache eviction, and ambiguous network responses can lose or duplicate a message; exactly-once or guaranteed delivery is not promised.
- Formatting, provenance lookup/write, evidence lookup, and transport failures in notification-only code all fail closed for Slack without changing core operation results. Missing provenance suppresses follow-ups rather than guessing. The five-second bound applies to the Slack HTTP attempt; no unbounded extra notification work is allowed.

## Non-goals and anti-goals

No threads, bot interactions, Slack actions, OAuth, user mapping or automatic mentions; no settings UI, per-repository routing, multiple channels, reminders, digests, resolution alerts, or reviewer-workload scheduling. No changes to collaborator authorization, sandbox-only writes, issue cooldown, GitHub deduplication, LLM budgets, or manual apply safety. No spam on every feedback save, prose-only reviewer explanation changes, or ordinary manual Re-triage.

The existing UI is unauthenticated. This work does not add authentication or claim that UI feedback is collaborator-authorized. Server-side lineage checks and best-effort deduplication limit accidental notifications, not abuse by an attacker who can already use the public feedback/triage API. Authentication/rate-limit hardening is a separate project.

## Acceptance checks

- Missing webhook URL and fixture mode produce no Slack traffic.
- A signed, admitted issue event produces a bounded summary with real citations/links and truthful action status; rejected/duplicate/cooldown-upsert events do not.
- GitHub reject and UI feedback-followed-by-Re-triage notify for eligible changed recommendations, not unchanged results, feedback saves, or unrelated manual runs.
- Parent mismatch, tenant mismatch, absent lineage/feedback, raw-title input, and historical unproven runs cannot acquire notification eligibility.
- Eligible provenance survives app restart; notification delivery deduplication is explicitly only same-process.
- Ingestion, triage and apply failures retain original behavior and produce sanitized, appropriately linked notifications when eligible. Partial refusals and fallback runs are not misrepresented.
- Timeout, 429, 5xx, redirects, unexpected responses, formatter/provenance failures and notification-client exceptions never affect triage/apply/feedback results or leak secrets.
- No extra LLM calls; Unicode/long/malicious content stays within payload caps and cannot ping a Slack audience.
- Existing webhook, command, agent-route and frontend feedback/re-triage regression tests remain passing.

## Left to the implementation plan / builder

Exact module boundaries, Pydantic request-field name and validation shape for feedback lineage, additive graph properties/queries, notification caps in characters, and HTTP-library choice should follow existing repository patterns. The plan must pin these decisions and their tests before implementation. Prefer existing dependencies and injected seams; no broad restructuring.

## Workflow checklist

- [x] Interview maintainer and inspect existing trigger paths.
- [x] Write proposed design and distinguish agreed choices from design calls.
- [x] Fresh builder review; resolved the issue-edit parent baseline as a documented design call.
- [x] Maintainer approval of the full design (request to start planning).
- [x] Write and self-review `docs/superpowers/plans/2026-10-18-slack-notifications.md` using writing-plans.
- [ ] Maintainer reviews plan and chooses execution method before implementation.
