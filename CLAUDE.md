# Keystone

Keystone is a triage agent for an open-source org, built for the WeMakeDevs x FalkorDB "Graph Hacks: Context for AI Agents" hackathon (Oct 15, 12:01 AM IST to Oct 18, 11:59 PM IST). It loads an org's public GitHub history into a FalkorDB graph. When an issue arrives, it finds the affected code, ranks reviewers by expertise and load, surfaces past decisions, flags bus-factor risk, and applies a triage action to a sandbox repo, live, through a GitHub webhook. Every claim it makes carries the graph path and Cypher that prove it, and a Proof suite measures it against two baselines.

Tracks entered: 1 (Agents That Act on Connected Data, the headline), 3 (Company Brain), 2 (Agent Memory). A team can win only one.

Read these before starting any task:

- @docs/spec.md: graph model, tool contracts, agent and memory loops, Cypher starting points
- @docs/tasks.md: the build plan, one checklist per day, each task with a "done when" check
- @design/README.md: the visual system (read before any UI work); tokens are in `design/tokens.json`

## Team

Two developers, each running their own Claude Code session. At the start of a session, ask which developer you're working with if it isn't clear, and work only on that person's lane in `docs/tasks.md`.

- **A (Ishaan):** `ingest/` (except `replay.py`), `cypher/`, `api/tools/`, `api/agent.py`, `api/llm.py`, `api/memory.py`, `api/mcp_server.py` (stdio), and `api/routes/agent.py` (triage, runs, feedback, ask)
- **B (teammate):** `docker-compose.yml`, `Caddyfile`, `web/`, `api/main.py` and every other file in `api/routes/`, `api/webhooks.py`, `api/actions.py` (incl. `apply_triage`), `ingest/replay.py`, and `api/mcp_http.py` (the same MCP server served over HTTP, read-only tools only)
- **Shared contracts:** `api/schemas.py`, `web/lib/types.ts`, `api/fixtures/`. Change them only after both agree, and update all three in the same commit.

Don't edit files in the other lane. If you need something from it, write down the exact function or route signature you need and stop.

## Stack

- **Graph:** FalkorDB (`falkordb/falkordb` in Docker Compose), Python client `falkordb`
- **Backend:** Python 3.11+, FastAPI, the standalone `fastmcp` package (server, plus its in-memory client for the agent), `httpx` for GitHub
- **Embeddings:** local `sentence-transformers`, `all-MiniLM-L6-v2` (384 dimensions)
- **LLM:** Gemini free tier with function calling; Groq free tier as fallback, behind one `llm.py` interface
- **Frontend:** Next.js (App Router) + TypeScript + shadcn/ui + Tailwind, `react-force-graph-2d` for the canvas
- **Deploy:** Docker Compose on one VM (FalkorDB + API + Caddy); `web/` on Vercel

## Layout

```
api/        FastAPI app, agent loop, MCP server, tools, GitHub actions, webhooks, memory, schemas, fixtures
ingest/     GitHub fetcher, loader, embeddings, decisions job, replay, proof, bench
web/        Next.js UI (Vercel root directory)
cypher/     every query the product depends on, one .cypher file each
design/     tokens and visual rules (read-only reference)
docs/       spec and tasks
data/       raw GitHub JSON and LLM caches (gitignored)
```

## Commands

Fill these in as they come to exist. Keep them working.

```bash
docker compose up -d falkordb            # FalkorDB on :6379, Browser on :3000
python -m ingest.fetch --tenant falkordb # GitHub -> data/raw/falkordb/*.json (cached)
python -m ingest.load --tenant falkordb  # data/raw -> graph tenant_falkordb
python -m ingest.decisions --tenant falkordb  # LLM job, cached; run once
python -m ingest.proof --tenant falkordb      # graph vs baselines -> data/proof/
python -m ingest.bench --tenant falkordb      # query latency -> data/bench/
python -m ingest.replay --tenant falkordb --issue <n>  # copy an upstream issue into SANDBOX_REPO; the webhook triages it
uvicorn api.main:app --reload            # API on :8000
python -m api.mcp_server                 # MCP server (stdio)
cd web && npm run dev                    # UI on :3001
pytest -q
```

## Hard rules

1. **The graph does the work.** Every product answer comes from a Cypher query or a FalkorDB algorithm. Never compute expertise, ownership or paths in Python from data pulled out of the graph wholesale. If the product would still work with FalkorDB removed, the design is wrong (hackathon Rule 3).
2. **Every tool returns its evidence.** Every tool returns the `ToolResult` shape in the spec: `tool`, `data`, `cypher`, `params`, `evidence` (node keys), `ms`, `empty`. No tool returns bare data.
3. **Cypher lives in `cypher/`.** Load query text from `cypher/*.cypher` by name. No Cypher inline in Python, except trivial MERGEs in the loader. Always pass parameters; never format values into query strings.
4. **Stable keys, not internal ids.** Every node has a `key` property (`person:octocat`, `pr:FalkorDB/FalkorDB#123`). Evidence, citations and the UI use `key`. Never expose FalkorDB internal ids.
5. **Sandbox only.** GitHub write actions (labels, assignees, comments) go to the repo in `SANDBOX_REPO` only. `DRY_RUN=true` is the default everywhere except the webhook flow (spec section 13). Never write to any other repo. Refuse in code if the target is not `SANDBOX_REPO`. Webhooks are rejected without a valid signature.
6. **Public data, no personal details.** Store GitHub logins, display names and profile URLs only. Never store emails, even from commit metadata (hackathon Rule 6).
7. **LLM budget.** 5 or fewer LLM calls per triage. Cache every LLM response on disk keyed by a hash of the prompt. Embeddings are local and never call an API.
8. **No agent frameworks.** No LangChain, LlamaIndex, Graphiti, Cognee or Mem0. The agent loop is hand-written so it can be explained to judges (Rule 12).
9. **Tenants are graphs.** One FalkorDB graph per org, named `tenant_<slug>`. Every API route and tool takes a `tenant` argument and selects that graph. Agent memory lives in the same tenant graph.
10. **UI follows `design/`.** Use the token names in `design/tokens.json` as CSS variables. Amber (`primary`, `evidence`) is only for evidence and the one primary action per view.

## Codebase search & navigation

Two local-first tools help agents navigate this repo faster than broad grepping and file reading. Both are **developer tooling for the LLM only** — they never enter `api/`, `ingest/` or `cypher/`, and they do not touch hard rule 8 (no agent frameworks in the product).

- **zvec-grep (`zg`)** — hybrid search over the workspace (ripgrep + BM25 + vector), all local. Index once with `zg index . --embedding local/potion-retrieval-32m` (stored in `.zvec-grep/`, gitignored). Search with `zg query "<question>"`, add `--human` for readable output or `--rg` for exact/regex, and `zg status` for freshness. Agents also see it as MCP tools (`zvec_grep_search`, and `zvec_grep_rg` where listed).
- **graphify** — a queryable knowledge graph of the code (tree-sitter AST, local, no API key). Rebuild after code changes with `graphify update .`. Query with `graphify query "<question>"`, trace relationships with `graphify path "<A>" "<B>"`, explain one concept with `graphify explain "<concept>"`. Outputs live in `graphify-out/`; the `/graphify` skill and hooks are registered in `.claude/` and `.opencode/`.

Rules:

1. **Route by evidence type.** Use `zg` when the wording or location is unknown, or the answer needs relationships, chronology, architecture or cross-file synthesis. Use plain grep/`rg` (or `zg query --rg`) only for exact anchors: a literal, identifier, filename, config key, error string or regex.
2. **Query the graph before reading source.** When `graphify-out/graph.json` exists, start codebase questions with `graphify query|path|explain`; read raw files only to modify or debug specific code, or when the graph lacks the detail. Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review.
3. **Treat a sufficient snippet as read.** `zg` hits carry bounded, source-linked snippets; open the cited file only when a needed detail falls outside the snippet.
4. **Keep the indexes current.** Run `graphify update .` after code changes (AST-only, no API cost). Creating or rebuilding a `zg` index is an explicit action, not a background step.
5. **Stop when the evidence is sufficient.** Don't repeat similar searches or broaden discovery just to reconfirm what the graph or snippets already establish.

## Working agreement

- Work one task from `docs/tasks.md` at a time. Plan first, then build, then run its "done when" check and show the output. Don't tick a task; I tick them.
- One short-lived branch per task, named after it (`a5-tools`). Rebase on `main` before opening a PR. The other developer reviews and merges it, so both can explain all the code (Rule 12). Commit messages name the task (`A2: loader + indexes`). Small commits are the proof of timing for the hackathon.
- When a Cypher query fails or returns something surprising, test it with the FalkorDB MCP server (`.mcp.json`; read-only queries only) or in FalkorDB Browser before changing code. The MCP server reads `FALKORDB_*` from the shell environment, not from `.env`, and falls back to localhost:6379, so export `FALKORDB_PASSWORD` before starting Claude Code. It runs `@falkordb/mcpserver@latest`; pin the version that works on Thursday.
- Prefer the simplest thing that passes the check. This is a 4-day, two-person build; don't add abstractions, config systems or tests beyond what a task asks for.
- If a task looks bigger than about 2 hours, stop and propose a smaller version.
- Ask before adding a dependency that isn't in the stack above.

## Environment

Variables live in `.env` (gitignored); `.env.example` lists them without values.

```
FALKORDB_HOST, FALKORDB_PORT, FALKORDB_USERNAME (empty by default), FALKORDB_PASSWORD (required)
GITHUB_TOKEN          # read-only, used for fetching public data
GITHUB_WRITE_TOKEN    # fine-grained, issues write on SANDBOX_REPO only; the only token that writes
SANDBOX_REPO          # owner/name of the sandbox repo
DRY_RUN=true
GEMINI_API_KEY, GROQ_API_KEY
LLM_CACHE=on          # set to off to test the no-LLM fallback
GITHUB_WEBHOOK_SECRET # shared with the sandbox repo's webhook
KEYSTONE_BOT_LOGIN    # the bot account that owns GITHUB_WRITE_TOKEN; its own events are ignored
PUBLIC_BASE_URL       # the deployed UI, for links in comments
TENANTS=falkordb      # comma-separated slugs, each with ingest/tenants/<slug>.yml
```

## AI disclosure

This project is built with the help of Claude Code, superseded by the developers' own engineering efforts and judgement. The README's "AI use" section says so (Rule 11). Keep it accurate as the build goes.

## Git Flow

For each task, open an issue with the relevant tags, assignee, effort, and description. Then, create a branch from `main` named after the task (e.g., `a5-tools`). Rebase on `main` before opening a PR. The other developer reviews and merges it, so both can explain all the code (Rule 12). Commit messages name the task (`A2: loader + indexes`). Small commits are the proof of timing for the hackathon.
