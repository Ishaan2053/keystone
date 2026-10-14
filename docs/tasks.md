# Keystone build tasks

Two developers, about 24 hours each (Thursday 5 h, Friday 5 h, Saturday 11 h, Sunday 3 h, all IST):

- **A (Ishaan):** graph and intelligence: ingest, Cypher, tools, agent, memory, Proof.
- **B (teammate):** product and live action: repo and infra, web app, canvas, deploy, webhooks, hosted MCP.

Each task names its owner. Work your own lane top to bottom. Each task ends with a **Done when** check: run it and show the output, and the owner ticks the box.

**About 1.5 hours per person on Saturday are deliberately unassigned** (marked Buffer), on top of an hour of rehearsal. They absorb integration bugs and Cypher that doesn't work first time. Don't fill them with new features before B12 (deploy) is done.

If you fall behind, cut in this order: B9 (tenant 2), then the canvas animation and hover polish in B5, then `/keystone why` in B8, then the blog post. Never cut the trace view, the live webhook comment, the Proof numbers, the deployment or the video.

## Prep (Oct 13 to 14, both, no code)

Rule 8 allows notes, sketches and accounts before kickoff, but not product code.

- [ ] Both: add the teammate to the team on WeMakeDevs; both join the FalkorDB Discord
- [ ] A: pick the repos for `ingest/tenants/falkordb.yml` (real PR and review activity in the last 12 months), check which have CODEOWNERS, and shortlist 5 recent issues to replay, each touching a different area
- [ ] B: create the sandbox repo (public, issues enabled), a bot GitHub account added as its only other collaborator, a fine-grained token for that bot with issues write on the sandbox only, and a webhook secret
- [ ] B: set up the VM with Docker, Docker Compose, and a domain or subdomain for Caddy; create a Vercel team for both of you
- [ ] A: get a read-only GitHub token, plus Gemini and Groq API keys
- [ ] Both: keep this handoff pack in a local folder outside git, and read `CLAUDE.md` and `docs/spec.md` once

## Thursday Oct 15, about 5 h each

Hour 0, together (30 min): B creates the repo after 12:01 AM IST, and the first commit is this pack ("planning notes prepared before kickoff"). Then pair on **T0**.

- [ ] **T0 (A + B). Contracts.** `api/schemas.py` (ToolResult, Claim, TriageResult, RunSummary, Subgraph, Stats, Proof) per the spec; `web/lib/types.ts` mirroring it; `api/fixtures/*.json`, one realistic example of each.
    - **Done when:** the fixtures validate against the Pydantic models in a one-line test.

**A**

- [ ] **A1. Fetcher.** `ingest/tenants/falkordb.yml` and `ingest/fetch.py` (spec section 4), with on-disk caching.
    - **Done when:** `data/raw/falkordb/` holds PRs, files, reviews, issues, comments, labels, docs and CODEOWNERS (or a note that there is none), and a second run makes zero network requests.
- [ ] **A2. Loader and indexes.** `ingest/load.py`, `cypher/indexes.cypher`.
    - **Done when:** load prints counts per label and relationship type, and a Browser query on one PR A names shows its author, reviewers, files and modules correctly.
- [ ] **A3. Derived edges and algorithms.** REVIEWED_FOR, then PageRank, betweenness and labelPropagation stored on Person; inferred Teams if there is no CODEOWNERS.
    - **Done when:** the top 5 people by `pagerank` include maintainers A recognises, and inferred teams (if any) print with their members.
- [ ] **A4. Embeddings** for issues and doc chunks, local `sentence-transformers`, stored as `vecf32`.
    - **Done when:** a vector query with one issue's own embedded text returns that issue first.

**B**

- [ ] **B1. Skeleton and infra.** Folders from `CLAUDE.md`, `.gitignore`, `.env.example`, `docker-compose.yml` (FalkorDB with a required password and a `/data` volume, the API, and Caddy for HTTPS), and a README skeleton with the AI-use and data statements.
    - **Done when:** on the VM, `https://<domain>/health` returns 200 from the API, and FalkorDB answers `RETURN 1` with the password.
- [ ] **B2. API shell.** FastAPI app with every route in spec section 9 returning the T0 fixtures, plus CORS. The triage, runs, feedback and ask routes go in `api/routes/agent.py`, which A takes over on Saturday.
    - **Done when:** each route returns its fixture, and the OpenAPI page lists all of them.
- [ ] **B3. Web shell.** Next.js + shadcn, theme from `design/tokens.json`, three-pane layout, triage card (with the Dry run switch beside Apply to sandbox), trace panel with expandable and copyable Cypher, and a header with the tenant switcher. All against the fixtures.
    - **Done when:** the page renders the fixture triage in dark and light themes, and at phone width the panes stack.

## Friday Oct 16, about 5 h each

**A**

- [ ] **A5. Tools.** `cypher/*.cypher` (spec section 6), a query loader, and the six graph tools.
    - **Done when:** `pytest -q` calls each tool against `tenant_falkordb`, and every result has non-empty `cypher` plus either non-empty `evidence` or `empty: true`. `recall_run` uses a fixture AgentRun the test creates and deletes.
- [ ] **A6. MCP server.** `api/mcp_server.py` with the seven tools; `apply_triage` can be a stub until B6.
    - **Done when:** MCP Inspector lists 7 tools over stdio, and a `rank_reviewers` call returns a ToolResult.
- [ ] **A7. Minimal LLM layer and decisions job.** A first `api/llm.py` (one Gemini JSON-mode call, on-disk cache), then `ingest/decisions.py` with the quote check, writing Decisions with `MADE_IN`, `ABOUT` and `vecf32` embeddings.
    - **Done when:** a trial on 10 PRs prints kept and dropped decisions with reasons, and a vector query on one kept summary returns it. Then start the full run (it can finish overnight).
- [ ] **A8. README graph model and Cypher sections**, with real counts.
    - **Done when:** every file in `cypher/` is listed with one line on why it needs a graph, and the counts match a fresh `load` run.

**B**

- [ ] **B4. Wire the real graph.** `/subgraph`, `/path` and `/stats` (counts) call A's queries as they land; the UI switches from fixtures to the API behind one flag.
    - **Done when:** the deployed UI shows real node counts and a real subgraph for one issue.
- [ ] **B5. Graph canvas.** `react-force-graph-2d` with shapes, colours, hover cards and evidence highlighting per `design/graph-canvas.md`.
    - **Done when:** clicking a citation chip dims everything except that claim's path, and clicking a node lights up its chips.
- [ ] **B6. Replay and apply.** `ingest/replay.py` and `apply_triage` (spec sections 7 and 13).
    - **Done when:** replay creates a sandbox issue with the "Replayed from" line; a dry run lists planned actions; a test shows a non-sandbox repo is refused.

## Saturday Oct 17, about 11 h each

**A**

- [ ] **A9. LLM layer.** Extend `api/llm.py`: Gemini function calling, Groq fallback, an `LLM_CACHE=off` switch.
    - **Done when:** the same prompt twice makes one network call, and with Gemini's key unset the call goes to Groq.
- [ ] **A10. Agent loop.** `api/agent.py` (spec section 7) with validation, recording, `exclude_issues` from `REPLAY_OF`, and the no-LLM fallback; `POST /tenants/{t}/triage`, `GET /runs`, `GET /runs/{run}`.
    - **Done when:** three shortlisted issues each return a TriageResult where every claim has a valid citation; with both LLM keys unset and `LLM_CACHE=off`, the fallback returns a result marked `fallback: true`; the AgentRun and its USED_EVIDENCE edges show in Browser.
- [ ] **A11. Feedback and rules.** `record_feedback()` in `api/memory.py` (spec section 8), used by `POST /tenants/{t}/runs/{run}/feedback` and by B8's comment command.
    - **Done when:** rejecting a suggested reviewer and re-running the same issue gives that person score 0, with the rule key in the trace.
- [ ] **A12. Ask.** `POST /tenants/{t}/ask`: `find_decisions` plus one compose call, with the same citation validation.
    - **Done when:** a question about a known design choice returns an answer citing at least one PR or doc URL.
- [ ] **A13. Proof.** `ingest/proof.py` (spec section 14): three metrics, the graph and both baselines, per-issue rows.
    - **Done when:** `data/proof/falkordb.json` holds `n` and all 9 rates, and A has read 3 per-issue rows to check they're sensible.
- [ ] **A14. Latency bench.** `ingest/bench.py`.
    - **Done when:** `data/bench/falkordb.json` holds p50 and p95 per tool, and `/stats` returns both files.
- [ ] **A: Buffer, about 1.5 h.**

**B**

- [ ] **B7. Webhook auto-triage.** `api/webhooks.py` (spec section 13): signature check, sandbox and bot guards, dedupe, background queue, upsert of the incoming issue and `REPLAY_OF`, agent run, live apply.
    - **Done when:** opening an issue in the sandbox from a phone gets a Keystone comment with a working UI link; a request with a bad signature gets 401; an event from another repo is ignored.
- [ ] **B8. Comment commands.** `/keystone reject <login> <reason>` and `/keystone why <login>`.
    - **Done when:** a reject from a collaborator creates a Rule, Keystone replies, and the re-triage drops that person; a reject from a non-collaborator is ignored.
- [ ] **B9. Optional, about 1 h: tenant 2.** A second tenant config, fetch and load (with A's scripts), and the switcher.
    - **Done when:** switching tenants shows a different graph and runs never mix.
- [ ] **B10. Hosted MCP.** `api/mcp_http.py`, HTTP transport at `/mcp` behind Caddy: six read-only tools, a rate limit, and a README snippet.
    - **Done when:** a remote MCP client connects to the public URL and gets results from `rank_reviewers`, and `apply_triage` isn't listed.
- [ ] **B11. Live UI and Proof panel.** The runs list polling `/runs`, and the Proof panel (grouped bars plus the latency table) from `/stats`.
    - **Done when:** a webhook run appears in the UI within 10 s without a reload, and the Proof panel shows A13's numbers.
- [ ] **B12. Deploy by 7 PM.** The final Compose on the VM, `web/` on Vercel, a reload script that re-runs load from cached data, and a `demo-reset` script that closes old sandbox issues.
    - **Done when:** from a phone, the whole demo path works on the public URLs: open issue, comment, UI run, reject, re-triage.
- [ ] **B: Buffer, about 1.5 h** (shrinks to 30 min if B9 is done).

**Both, Saturday evening:** rehearse the 8 demo moments (spec section 1) twice and record take 1. A drafts the blog post.

## Sunday Oct 18, about 3 h each

- [ ] A: README (Proof tables with one win and one loss quoted, track sections), and publish the blog post
- [ ] B: final video edit (3 minutes max), then a clean-clone setup test following the README word for word
- [ ] Both: submit by 6 PM IST naming all three tracks, then keep the VM up through judging
