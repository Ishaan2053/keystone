# B1–B3 contract proposal — approval required

Status: proposed by the planning agent; **not agreed by B or A**. No product code or shared contracts have been created. Read with `CLAUDE.md`, `docs/spec.md` (especially sections 5, 7–9, 11, 13–14), and `docs/tasks.md` in full.

## Approval checklist

- [ ] B and A approve the response shapes and fixture inventory below before T0 implementation.
- [ ] B and A approve B2's explicit, nonfunctional webhook/MCP placeholders until B7/B10.
- [ ] B and A resolve the Proof metric discrepancy before T0 finalizes `Proof`.
- [ ] B approves the proposed environment additions and test dependencies before installation.

These are decisions to review, not boxes for the implementing agent to tick. The user's no-commit instruction overrides all branch/commit steps in the handoff and skills. Plans are preparation only; product code starts no earlier than Oct 15, 12:01 AM IST.

## Existing contracts: preserve exactly

`ToolResult`, `Claim`, and `TriageResult` are defined in spec sections 5 and 7. Preserve their exact field names and types. `ToolResult.data` stays unconstrained JSON; do not add UI-only fields to it or pretend every tool returns the same data. All references are stable node keys; timestamps are integer epoch seconds and durations integer milliseconds. Lists are never null. UI shows `@login`; generated GitHub comment Markdown uses backticks without `@`.

No `fallback` field is added to `TriageResult`: it belongs to the run metadata. No reviewer `reason` property is invented on `Claim`; longer reasons remain cited claims/comment Markdown or tool data until both owners explicitly extend the shared schema.

## Proposed additional schemas (`api/schemas.py`, mirrored in `web/lib/types.ts`)

| Model | Exact fields |
| --- | --- |
| `HealthResponse` | `status: Literal['ok']` |
| `TenantSummary` | `slug: str`, `name: str` |
| `TenantsResponse` | `tenants: list[TenantSummary]` |
| `TriageRequest` | `issue_number: int \| None = None`, `title: str \| None = None`, `body: str = ''`; exactly one mode: positive issue number OR nonblank title; reject a supplied title/body alongside number |
| `ApplyRequest` | `dry_run: bool = True` |
| `ActionResult` | `action_key: str`, `kind: Literal['label','assign','comment']`, `value: str`, `status: Literal['applied','dry_run','refused']`, `note: str \| None = None` |
| `ApplyResult` | `run_key: str`, `dry_run: bool`, `actions: list[ActionResult]` |
| `FeedbackRequest` | `action_key: str`, `verdict: Literal['accept','reject']`, `reason: str = ''`, `scope: Literal['module','global'] = 'module'`; reject requires nonblank reason |
| `FeedbackResult` | `feedback_key: str`, `action_key: str`, `verdict: Literal['accept','reject']`, `rule_key: str \| None` |
| `AskRequest` | `question: str`; nonblank after trimming |
| `AskResult` | `answer: list[Claim]`, `trace: list[ToolResult]` |
| `RunSummary` | `run_key: str`, `at: int`, `issue_key: str`, `summary: str`, `fallback: bool`, `model: str`, `llm_calls: int`, `ms: int` |
| `RunsResponse` | `runs: list[RunSummary]` |
| `RecordedAction` | `action_key: str`, `kind: Literal['label','assign','comment']`, `value: str`, `module: str \| None`, `status: Literal['proposed','applied','dry_run','refused','rejected']`, `note: str \| None`, `feedback: list[FeedbackResult]` |
| `RecallRunData` | `run: RunSummary`, `evidence: list[str]`, `actions: list[RecordedAction]`, `triage: TriageResult` |
| `PathData` | `keys: list[str]`, `rels: list[str]`; a nonempty path has `len(rels) == len(keys) - 1` |
| `GraphNode` | `key: str`, `kind: str` restricted to the 16 kinds in spec section 3, `label: str`, `properties: dict[str, JSON value]` |
| `GraphEdge` | `source: str`, `target: str`, `type: str`; endpoints reference `GraphNode.key` |
| `Subgraph` | `nodes: list[GraphNode]`, `edges: list[GraphEdge]`; at most 150 nodes, no dangling endpoints |
| `ToolLatency` | `tool: str`, `p50_ms: float`, `p95_ms: float` |
| `Stats` | `node_counts: dict[str,int]`, `edge_counts: dict[str,int]`, `last_ingest_at: int \| None`, `proof: Proof \| None`, `latencies: list[ToolLatency]` |

Use explicit required nullable fields where there is no default above. JSON value means recursive JSON-serializable data, not arbitrary Python objects. `GraphNode.properties` contains public display data only, no internal IDs, embeddings or email addresses. Run status is not invented: B3 labels fixture content “Fixture preview”; B11 can agree an actual operational status contract later.

**Recall compatibility decision:** `GET /runs/{run}` returns `ToolResult(tool='recall_run', data=RecallRunData, ...)`. This requires A to persist or reconstruct `triage` when implementing A10; the sample recall Cypher alone does not return it. A must approve this interface, or replace it with an agreed UI reconstruction contract before T0. B must not silently change A's query or write an agent implementation.

### Proof: unresolved source conflict

Section 14 names reviewer hit@3, module hit@1 **and** hit@3, and label precision@2: four rates per method, twelve across three methods. Tasks A13 says “all 9 rates,” and the UI calls for three metric groups. Proposed resolution: the primary chart groups reviewer hit@3, module hit@3, and label precision@2 (nine rates); preserve module hit@1 as an additional reported metric (three more rates). Owners must approve this interpretation; do not drop a required measurement to force nine.

Proposed `Proof`: `n: int`, `methods: dict[Literal['graph','codeowners','similar_issues'], ProofRates]`, `rows: list[dict[str, JSON value]]`. `ProofRates`: `reviewer_hit_at_3: float`, `module_hit_at_1: float`, `module_hit_at_3: float`, `label_precision_at_2: float`, each in `[0,1]`. At B1–B3, `Stats.proof = null` is valid and preferable to invented benchmark numbers. Proof charts/latency measurement are B11/A13/A14, not this work.

## Fixture inventory and identity

T0 supplies these JSON files, validates them against the approved models, and keeps Python/TypeScript in agreement:

- Existing required examples: `tool_result.json`, `claim.json`, `triage_result.json`, `run_summary.json`, `subgraph.json`, `stats.json`, `proof.json` (Proof blocked on decision above).
- Route examples: `tenants.json`, `runs.json`, `recall_run.json` (ToolResult wrapper), `path.json` (ToolResult wrapper), `apply_dry_run.json`, `apply_refused.json`, `feedback_accept.json`, `feedback_reject.json`, `ask.json`.
- Shared fixture identity: tenant `falkordb`; run UUID `00000000-0000-4000-8000-000000000001`; full run key `run:00000000-0000-4000-8000-000000000001`; action key `action:00000000-0000-4000-8000-000000000002`.
- Use a public sandbox Issue key, two cited reviewers, labels, a decision, a risk, and a trace whose evidence covers every claim. URLs must be real public source URLs chosen in prep, not fabricated evidence. Fixture statistics must be visibly illustrative, not reported as observed counts.
- `apply_dry_run.json` has only `dry_run` actions. `apply_refused.json` has only `refused` actions, with notes explaining fixture mode never writes. Accept feedback has `rule_key = null`; rejected assign feedback has a stable Rule key.
- Request/response fixtures are not generated independently in the web app. B3 reads approved JSON from `api/fixtures/` using server-side filesystem access resolved from the `web/` root, then passes typed serializable props. Verify this in a production build. B4 replaces this one loader with an API fetch behind one flag.

## Route behavior proposed for fixture mode

| Route | Success contract |
| --- | --- |
| `GET /health` | HealthResponse, 200; process liveness, not graph readiness |
| `GET /tenants` | TenantsResponse from `TENANTS` configuration |
| `POST /tenants/{t}/triage` | TriageResult, 200 |
| `POST /tenants/{t}/runs/{run}/apply` | ApplyResult, 200; true → dry-run fixture; false → refusal fixture; never calls GitHub |
| `POST /tenants/{t}/runs/{run}/feedback` | FeedbackResult, 200; fixture only, no persistence |
| `GET /tenants/{t}/runs/{run}` | ToolResult containing RecallRunData, 200 |
| `POST /tenants/{t}/ask` | AskResult, 200 |
| `GET /tenants/{t}/path?from=&to=` | ToolResult containing PathData, 200; alias `from`/`to` to Python-safe argument names |
| `GET /tenants/{t}/subgraph?keys=...` | Subgraph, 200; comma-separated stable keys; return fixed fixture, document that requested neighbourhood lookup starts B4 |
| `GET /tenants/{t}/runs?limit=20` | RunsResponse, 200; limit integer 1–100, descending `at` |
| `GET /tenants/{t}/stats` | Stats, 200 |
| `POST /webhooks/github` | 501 `{'detail':'Webhook receiver not implemented'}` until B7, never 202 or writes |
| `POST /mcp`, `GET /mcp`, `DELETE /mcp` | 501 `{'detail':'Hosted MCP not implemented'}` until B10; documented reservations, **not** an MCP implementation |

Tenant endpoints reject unconfigured tenants with 404 before fixture loading. A configured tenant without a fixture dataset returns 503 rather than another tenant's data. UUID route parameters are parsed to full `run:<uuid>` keys internally; unknown runs return 404. Strict request validation returns 422; reject unexpected body fields. Existing error envelopes use FastAPI `detail`, not a new error framework. Path/key query inputs are nonblank; URL-encode keys in clients. Only B4 will implement actual query-dependent path/subgraph content.

**B2 acceptance adjustment requiring approval:** all implemented JSON routes return approved fixtures; webhook/MCP reservations appear in OpenAPI with explicit 501 responses. They cannot honestly return a T0 fixture for the eventual service yet. B7 replaces the webhook placeholder with signature verification before acceptance; B10 replaces MCP reservations with Streamable HTTP. Do not call the B2 reservations functional endpoints.

## Environment and tooling proposals

Preserve all environment variables in `CLAUDE.md`; `.env.example` contains blank secrets and specified nonsecret defaults. `FALKORDB_PASSWORD` is required by Compose; no baked-in fallback.

Proposed additions: `API_DOMAIN` (DNS hostname for Caddy), `CORS_ORIGINS` (comma-separated exact origins; default `http://localhost:3001`; add deployed Vercel origin explicitly), `API_MODE=fixtures` (shell-only selector until B4). `PUBLIC_BASE_URL` remains the deployed UI URL, not the API hostname. Python fixture mode is explicitly logged and documented.

Backend runtime: stack's FastAPI, Uvicorn, Pydantic; pytest/httpx for tests (httpx already in stack, pytest named by project). Frontend runtime: Next.js/App Router, React, TypeScript, Tailwind, shadcn/ui primitives and Lucide as prescribed by design. Propose Playwright as the single browser test dependency, subject to B approval. Use plain CSS/class state for theme switching; no additional theme library. Pin compatible versions and lock files at execution time, after kickoff; no unapproved dependency installation in planning.

## Scope boundary

B1 owns skeleton/container/HTTPS and a minimal health endpoint. B2 extends that app with fixture-backed HTTP routes and CORS. B3 owns the fixture web shell, theme, responsive panes and copyable trace. It does **not** implement a force graph (B5), live writes (B6), webhook handling (B7), polling/Proof charts (B11), or final Vercel deployment (B12). All three plans stop at their own acceptance check; human owners tick `docs/tasks.md` after seeing actual output.
