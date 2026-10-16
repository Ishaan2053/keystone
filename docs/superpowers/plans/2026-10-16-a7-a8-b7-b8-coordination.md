# A7, A8, B7 and B8 Coordinated Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver cached, quote-grounded Decision extraction and truthful graph documentation alongside sandbox webhook triage and collaborator comment commands.

**Architecture:** Use the existing named Cypher loader, tenant graph client, local embeddings, sandbox-bound GitHub writer and ActionService. Keep the two subsystems in separate implementation plans, coordinated through the existing graph and explicit A10/A11 handoffs; A7/A8 alone do not make B7/B8 runnable end to end.

**Tech Stack:** Python 3.11+, FastAPI lifespan, standard-library queue/HMAC/hashlib, httpx, Pydantic, FalkorDB, sentence-transformers; existing Next.js/TypeScript/Playwright for run links. No new dependency proposed.

**Spec:** `docs/tasks.md` A7/A8/B7/B8; `docs/spec.md` §§3–9 and 13; `CLAUDE.md`; the two linked plans below.

## Global Constraints

- “Work one task from `docs/tasks.md` at a time.” Coordinate the batch, but execute and review named deliverables sequentially; do not tick the owner's acceptance boxes.
- “Don't edit files in the other lane.” Both lanes are included in planning at the user's request; implementation ownership still follows `CLAUDE.md` unless explicitly reassigned.
- “Cypher lives in `cypher/`.” Use named, parameterized queries and stable keys, never internal node IDs.
- “Tenants are graphs.” Validate configured tenants before selecting `tenant_<slug>`.
- “Sandbox only.” Public HTTP apply remains dry-run-only; only verified webhook processing explicitly invokes live apply.
- “Public data, no personal details.” Store no email or entire webhook/user payload.
- Keep shared schemas/types/fixtures unchanged unless both developers agree.
- No product code, live GitHub writes, paid calls, task completion claims or commits are authorized by saving this plan.

## Review Focus

1. A7/A8 must not be mistaken for A10/A11: live acceptance stays blocked until real persisted runs and feedback exist.
2. A forged replay URL must never choose a write target or another tenant.
3. Delivery dedupe and issue cooldown must not suppress an authorized reject's re-triage.
4. Invalid quotes and cache contamination must not create fabricated Decisions.
5. A comment's run link must load that exact real run, not a fixture or merely an issue graph.

---

## Plans and suggested order

1. [A7/A8: LLM cache, Decisions and graph documentation](2026-10-16-a7-a8-decisions-and-docs.md).
2. [B7/B8: webhook triage and collaborator commands](2026-10-16-b7-b8-webhooks-and-commands.md).
3. Reconcile A8's query inventory/count snapshot after B7/B8 queries land and before final acceptance.

A7/A8 are independent of the webhook receiver and can finish first. Within the live-action plan, B7 precedes B8. A7's overnight extraction may continue while B7 is developed, but do not run loader/count acceptance concurrently with live demo writes: capture a dated snapshot during a quiet period.

## Current-code baseline (2026-10-16)

- `api/llm.py`, `ingest/decisions.py`, `api/webhooks.py`, `api/agent.py` and `api/memory.py` are absent.
- `api/routes/agent.py` deliberately returns 503 in live mode; its fixture feedback is not persistent memory.
- `api/actions.py` has `ApplyContext`, `ActionStore`, guarded live apply, safe comments and per-run retry markers. `create_app()` accepts an injected apply store but has no default real adapter.
- `api/github.py` provides fixed-repo writes, collaborator lookup and comment-marker lookup.
- `api/embeddings.py` provides `encode()` and `issue_text()`; use these for every new vector.
- `ingest/load.py` reads normalized cached files under `data/raw/<tenant>/<owner>__<repo>/`, preserves graph memory, and prints live counts.
- `api/cypher.py` loads named queries; `find_decisions.cypher` already expects Decision vectors and MADE_IN/ABOUT links.
- `/webhooks/github` is a 501 placeholder in `api/routes/reserved.py`.
- `api/actions.py` generates `/t/<tenant>?run=<uuid>`, but `web/app/t/[tenant]/page.tsx` currently accepts only `issue`.
- README lacks the required complete graph model and per-file Cypher inventory.

## A10/A11 integration contract proposal — agree before live-action implementation

These are requested interfaces, not claims that the functions already exist. A implements these in its lane; B consumes them through injected dependencies. If A chooses different signatures, update the live-action plan before coding rather than building a competing agent or feedback store.

```python
# A10: api/agent.py; canonical stable key avoids cross-repo number collisions.
async def triage_issue(tenant: str, issue_key: str) -> TriageResult: ...

# A11: api/memory.py; same function backs HTTP feedback and webhook rejection.
def record_feedback(
    tenant: str, run_key: str, request: FeedbackRequest, *, given_by: str
) -> FeedbackResult: ...

# A10: concrete implementation of existing api.actions.ActionStore.
def load_context(tenant: str, run_key: str) -> ApplyContext: ...
def record_results(
    tenant: str, run_key: str, results: list[ActionResult]
) -> None: ...
```

- `triage_issue` returns only after AgentRun/HANDLED/PROPOSED/evidence persistence succeeds; it excludes both the incoming issue and its REPLAY_OF original, and supports the no-LLM fallback.
- `record_feedback` verifies action ownership in the supplied tenant/run, rejects non-assign or missing-module module feedback, persists the rule atomically with feedback/action rejection, and returns only after persistence succeeds.
- A supplies live `GET /tenants/{tenant}/runs/{uuid}` using the existing ToolResult/RecallRunData contract. B owns selecting it in the UI.
- B owns incoming issue/replay upsert and latest-run/action lookup queries; A owns agent/memory persistence queries. Agree on query additions rather than crossing ownership silently.
- Tests with fake adapters prove B behavior only. Missing adapters produce a clear unavailable service, never a fixture-backed live run or a falsely successful Rule reply.

## Decisions proposed for review

1. **Queue:** one API process, one bounded in-memory FIFO worker; standard library only. Capacity 100; queue full returns 503 before marking delivery accepted. Restart can lose pending work; GitHub redelivery is the recovery path. Durable queue/multi-worker delivery is outside this batch.
2. **Tenant routing:** while one sandbox serves one live tenant, enable the webhook only when exactly one configured tenant exists. Multiple tenants fail closed until an explicit sandbox-to-tenant mapping is agreed. Never infer the tenant from issue text or a replay URL.
3. **Cooldown:** ordinary issue open/edit triage has a 60-second completion cooldown plus a pending/in-flight guard. Authorized reject commands bypass that cooldown, still deduplicated and serialized.
4. **Rejection target:** resolve the latest persisted run for that exact incoming issue and require one matching assign action with a module. Never invent a global rule or select an older run to make an ambiguous command succeed. No automatic unassignment is added by B8.
5. **Scope:** A9 function calling/Groq, A10 agent implementation, A11 rule implementation, B11 polling and deployment remain separate tasks, even when their handoffs are coordinated together. `why` is included but is the first cut allowed by the task pack if time slips.

## Clarifications still needed before implementation

- Does “all together” also authorize implementing A10/A11 here, or will Ishaan deliver those interfaces separately? This changes the size materially; the four named tasks cannot meet live acceptance without them.
- Is the documented single-process, restart-loss queue acceptable for the hackathon, or is restart-safe delivery required?
- Confirm one webhook-enabled tenant for this sandbox and availability of its secret, bot login, write token and public UI URL through private environment configuration; never paste secrets into the plan.

## Coordination checklist

- [ ] Approve the proposed decisions and settle A10/A11 ownership/readiness.
- [ ] Execute A7, including the 10-PR trial and start of the cached full extraction.
- [ ] Execute A8 with real, dated counts and a complete query inventory.
- [ ] Execute B7 isolated receiver/worker tests, then real-adapter integration and exact-run deep links.
- [ ] Execute B8 reject and why tests against the same feedback/agent services.
- [ ] Refresh A8 documentation for final query additions and counts.
- [ ] Run the four task acceptance checks and save factual outputs; owners tick `docs/tasks.md` themselves.

Each named task should stay near the project's two-hour limit. If A10/A11 must be built here or durable delivery is required, stop and expand the plan explicitly rather than burying that work in B7/B8.
