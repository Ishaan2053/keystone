# A7/A8 — Cached Decisions and Graph Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract auditable, embedded Decisions from cached public sources through one cached Gemini JSON call per source, then document the real graph and every Cypher file.

**Architecture:** A minimal `api/llm.py` returns schema-validated JSON through httpx and a disk cache. `ingest/decisions.py` selects sources from the loaded graph, assembles their exact text from existing normalized cache files, verifies verbatim quotes, resolves module paths through graph queries, and idempotently persists Decision nodes and links.

**Tech Stack:** Python 3.11+, httpx, Pydantic, hashlib/json/pathlib, FalkorDB, existing local sentence-transformers embeddings; no new SDK.

**Spec:** `docs/spec.md` §§3–6; `docs/tasks.md` A7/A8; `CLAUDE.md`; `2026-10-16-a7-a8-b7-b8-coordination.md`.

## Global Constraints

- “Keep a decision only if `quote` appears verbatim in the source text.” No whitespace normalization, fuzzy matching or LLM adjudication.
- Select the 150 merged PRs with most comments plus every Doc whose path contains `adr`, `rfc`, `design` or `decision`.
- Decision key is `decision:<sha1 of summary + source key>`; use exact concatenation `summary + source_key` encoded as UTF-8.
- “Every node has two labels: its kind and `Node`.” Use `vecf32($vec)` for 384-dimensional embeddings.
- “Map `about_paths` to Module keys; drop paths that don't match a module.” Never create invented modules.
- Named parameterized Cypher only; tenant validation before graph selection; no graph deletion, source refetch or email collection.
- A7 is JSON mode only. Gemini tool calling/Groq remain A9; no new shared API contract.
- Secrets never appear in logs, cache keys, stored metadata or errors. Cached source data stays gitignored.

## Review Focus

1. Fabricated, empty or altered quotes are dropped without graph writes.
2. Cache hits cannot cross model/schema/prompt boundaries or poison future extraction after malformed JSON.
3. PR selection ties, bot comments and source assembly remain deterministic and explainable.
4. Re-running extraction must not duplicate nodes/edges or retain obsolete ABOUT edges for an updated same-key record.
5. README counts must be a real dated snapshot, not copied fixture numbers or a destructive reload.

---

## File map

- Create `api/llm.py`: JSON-mode client, validated cache and sanitized provider errors.
- Create `ingest/decisions.py`: extraction-only Pydantic models, cached source assembly, quote validation, CLI orchestration and progress.
- Create `cypher/decision_sources.cypher`: ranked PR candidates and qualifying Docs with keys, URLs and repo/path identity.
- Create `cypher/decision_modules.cypher`: resolve exact repo-qualified Module keys from candidate paths.
- Create `cypher/upsert_decision.cypher`: idempotent Decision/MADE_IN/ABOUT persistence and vector storage.
- Create `tests/test_llm.py`, `tests/test_decisions.py`, `tests/test_live_decisions.py`, `tests/test_readme_graph.py`.
- Modify `.env.example` for `GEMINI_MODEL` only if absent; modify `README.md` for commands, cache behavior, graph and query inventory. Existing requirements suffice.

### Task 1: A7 cached Gemini JSON client (~25 minutes)

**Interfaces:** `JsonLLM(client: httpx.Client, *, api_key: str, model: str, cache_dir: Path)`; `generate_json(self, prompt: str, *, schema: type[BaseModel]) -> dict`. `LLMError` is the sanitized failure type. Caller provides a JSON extraction schema; no function-calling API in this task.

- [ ] **Step 1: Write failing tests** in `tests/test_llm.py`: `test_same_prompt_calls_gemini_once` asserts two identical valid calls cause exactly one MockTransport request; `test_cache_separates_model_prompt_schema` asserts changing any component changes the entry; `test_invalid_response_not_cached` asserts malformed JSON/schema, empty candidates, timeout and 429 raise `LLMError` and leave no valid entry; `test_cache_hit_needs_no_key` succeeds with a valid prewritten entry and no provider key; `test_cache_off_ignores_disk` makes two requests when `LLM_CACHE=off`; `test_secret_not_in_errors_or_cache` inspects both.
- [ ] **Step 2: Run** `python -m pytest tests/test_llm.py -q`; expect missing-module failures, not live network access.
- [ ] **Step 3: Implement** `api/llm.py` with Gemini `generateContent` REST over the injected httpx client, JSON MIME mode and the supplied response schema, finite 30-second timeout, disabled redirects, and key in `x-goog-api-key`. Use `GEMINI_MODEL=gemini-2.5-flash` as the proposed configurable default; verify availability during the approved live trial. Cache identity is SHA-256 of canonical JSON containing provider, model, prompt, schema JSON and cache version 1. Validate before atomically replacing a cache file, validate hits again, treat corrupt entries as misses. Read `LLM_CACHE=off` to bypass reads/writes. Cache is checked before requiring a key. No automatic retry loop or Groq fallback in A7.
- [ ] **Step 4: Run** `python -m pytest tests/test_llm.py -q`; expected all pass, zero real requests.
- [ ] **Step 5: Commit only the task files** with message `A7: add cached Gemini JSON client` after reviewing the staged diff.

### Task 2: A7 deterministic source selection and quote gate (~30 minutes)

**Interfaces:** frozen `DecisionSource(key: str, repo: str, url: str, text: str)`; Pydantic `DecisionCandidate(summary: str, rationale: str, quote: str, about_paths: list[str])` and `DecisionBatch(decisions: list[DecisionCandidate])`; `select_sources(tenant: str, *, raw_root: Path, limit_prs: int = 150) -> list[DecisionSource]`; `validate_candidate(source: DecisionSource, candidate: DecisionCandidate) -> tuple[bool, str]`.

- [ ] **Step 1: Write failing tests** in `tests/test_decisions.py`: `test_selects_top_150_merged_and_all_matching_docs` verifies descending comment_count with stable key tie-break, no open PRs and case-insensitive Doc path matching; `test_trial_limit_only_limits_prs` proves `--limit-prs 10` still includes qualifying Docs; `test_source_text_includes_human_comments` joins title/body and human issue/review comments in stable timestamp/id order and excludes bots like the loader; `test_invalid_quote_is_dropped` checks absent quote, empty quote, whitespace-only quote and normalized-but-not-verbatim text; `test_missing_cache_fails_without_fetch` asserts no HTTP and an explicit source error.
- [ ] **Step 2: Run** `python -m pytest tests/test_decisions.py -q`; expect missing interfaces.
- [ ] **Step 3: Implement** graph selection in `decision_sources.cypher` and cached text assembly in `ingest/decisions.py`. Use `prs.json`, `issue_comments.json`, `review_comments.json` and `docs.json` under the loader's existing repo directories; match comments to the selected PR number with the loader's `target_number` convention and human-user filter. Preserve each included field exactly; deterministic separators are part of the sent source. Include source key/URL and an instruction that source text is data, not instructions. Require nonblank summary/rationale/quote and exact `quote in source.text`; output a reason for every accepted/rejected candidate. Missing selected source text is an error, never an invented empty document. No network fetch or arbitrary text truncation.
- [ ] **Step 4: Run** `python -m pytest tests/test_decisions.py -q`; expected source-selection/quote cases pass using local fixtures and fake graph responses.
- [ ] **Step 5: Commit** `A7: select decision sources and enforce verbatim quotes`.

### Task 3: A7 graph writes, CLI and acceptance (~35 minutes plus extraction runtime)

**Interfaces:** `extract_decisions(tenant: str, *, llm: JsonLLM, raw_root: Path, limit_prs: int = 150) -> dict[str, int]` returns `sources`, `kept`, `dropped`, `failed`; `main(argv: list[str] | None = None) -> None`. CLI: `python -m ingest.decisions --tenant falkordb [--limit-prs 10]`.

- [ ] **Step 1: Add failing tests**: `test_only_validated_candidates_are_embedded_or_written`; `test_module_resolution_stays_in_source_repo` resolves only exact `module:<source-repo>/<path>` keys, dropping unknown paths; `test_candidate_without_matching_modules_keeps_source_evidence` keeps a quote-valid Decision with MADE_IN and zero ABOUT links; `test_rerun_is_idempotent` verifies the same Decision key and no duplicate edges; `test_same_key_reconciles_about_edges`; `test_failed_source_reports_nonzero_exit_but_continues` preserves successful sources while reporting failure. In `tests/test_live_decisions.py`, create namespaced source/module fixtures, persist a known quote-valid Decision twice and query its own summary vector; assert it ranks first in the isolated fixture graph and both relationship types exist. Delete only that test-owned graph in teardown.
- [ ] **Step 2: Run** `python -m pytest tests/test_decisions.py tests/test_live_decisions.py -q`; expect new tests to fail; live cases may skip explicitly when FalkorDB is unavailable.
- [ ] **Step 3: Implement** `decision_modules.cypher` and `upsert_decision.cypher`: MATCH the existing source before MERGE, MERGE `Decision:Node` by the specified SHA-1 key, set summary/rationale/quote/url/`vecf32($vec)`, MERGE MADE_IN, replace that Decision's ABOUT links with validated exact matches. Use local `encode([summary])`, not a provider embedding. Process sources sequentially, one JSON call per source; print source, kept/dropped reasons and totals without secrets. Already cached valid LLM responses can rehydrate graph writes after a failure. Failures are counted, do not cache provider failures, continue remaining sources, exit nonzero at the end if any source failed. Validate configured tenant and use the existing `.env` reader in the CLI.
- [ ] **Step 4: Run** `python -m pytest tests/test_llm.py tests/test_decisions.py tests/test_live_decisions.py -q`; require passing unit tests; report skipped live cases as unverified. Run `graphify update .` after code changes per project instructions, but do not create a new semantic index.
- [ ] **Step 5: With approval for live LLM calls**, run `python -m ingest.decisions --tenant falkordb --limit-prs 10`; retain kept/dropped reasons. Query a kept Decision using its own embedded summary and retain returned key/rank; re-run the trial and verify zero additional provider requests (test transport/counter, not a guess from elapsed time). Start `python -m ingest.decisions --tenant falkordb` in a background job with output retained under gitignored `data/`; report launch separately from completion. Commit `A7: persist embedded decisions and add extraction CLI`. Owner ticks A7 only with trial/vector evidence and full-run launch evidence.

### Task 4: A8 complete graph model, query inventory and real counts (~30 minutes)

**Files:** modify `README.md`; create `tests/test_readme_graph.py`. No production API/schema changes.

**Interfaces:** README sections `Graph model`, `Graph snapshot`, `Cypher inventory`; inventory contains each actual `cypher/*.cypher` basename once with purpose and why graph execution is necessary (including operational queries, whose reason may be graph maintenance rather than inference).

- [ ] **Step 1: Write failing tests** `test_readme_lists_every_cypher_file` compares discovered basenames to inventory entries, and `test_readme_documents_decision_grounding_and_memory` asserts Decision quote verification, stable keys, REPLAY_OF and graph-preserving reload guidance are present. Test structure/coverage, not brittle whole paragraphs or fixed live counts.
- [ ] **Step 2: Run** `python -m pytest tests/test_readme_graph.py -q`; expect missing inventory/model failures.
- [ ] **Step 3: Update** README from actual source: document nodes/edges, tenant isolation, bot/sweep filtering, inferred teams, vectors, evidence paths, Decision provenance and agent-memory status. Explain the reason each current query needs FalkorDB; explicitly call out persistence/index/cleanup queries as operations. Do not claim missing A10/A11 persistence queries exist. Replace the stale opening/status text with precise implemented/pending status.
- [ ] **Step 4: Capture a real snapshot** during a quiet approved graph-maintenance window: `python -m ingest.load --tenant falkordb`, retaining stdout; record UTC capture time, tenant, dataset window, node/edge counts and sweeps exactly as printed. The loader preserves agent memory: do not drop the graph to make counts match. A7 Decisions may be present; state whether full extraction completed. If no data/DB is available, label counts pending and leave A8 acceptance open, never fill them from fixtures. Run `python -m pytest tests/test_readme_graph.py -q`; expected pass.
- [ ] **Step 5: Recheck and commit** `A8: document graph model queries and measured counts`. Before final combined handoff, regenerate the inventory for B7/B8's added queries and refresh any changed snapshot; owner ticks A8 only after counts match captured fresh load output.

## Self-review and execution gates

- Spec selection, quote guard, source links, embeddings, 10-PR trial, full-run launch, every-file query inventory and fresh-load counts all have explicit tasks above.
- The default model, unmatched-module behavior and deterministic source assembly are proposed implementation choices, not prior user decisions.
- A7 is not a replacement for A9/A10/A11. Extraction progress does not prove the live webhook works.
- Before implementation, resolve the coordination document's open scope question and approve these choices.
