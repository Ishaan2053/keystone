# Graph model and queries

The full reference for the tenant graph and every Cypher query Keystone runs. The [README](../README.md#the-graph) has the overview.

## Graph model

Each org is one FalkorDB graph, `tenant_<slug>`. The demo tenant is `tenant_falkordb`, built from FalkorDB/FalkorDB, FalkorDB/GraphRAG-SDK and FalkorDB/falkordb-browser. Every node has two labels, its kind and `Node`, plus a stable `key` such as `person:octocat` or `pr:FalkorDB/FalkorDB#123`. Evidence, citations and the UI use keys, never FalkorDB internal ids. Full property lists are in [docs/spec.md section 3](spec.md#3-graph-model).

| Node | Key | What it is |
| --- | --- | --- |
| Person | `person:<login>` | a human contributor: login and profile URL only, never an email |
| Team | `team:<org>/<slug>`, `team:user/<login>`, `team:inferred/<n>` | a CODEOWNERS owner, or a community found in the review graph |
| Repo | `repo:<owner>/<name>` | a repository |
| Module | `module:<owner>/<name>/<path>` | a file's first path segment, or its first two under `src`, `lib`, `packages` or `pkg`; root files go to `(root)` |
| File | `file:<owner>/<name>/<path>` | a file some PR touched |
| Issue | `issue:<owner>/<name>#<n>` | an issue, with a 384-d embedding of its title and body |
| PR | `pr:<owner>/<name>#<n>` | a pull request; `sweep` marks the ones that are not evidence of expertise |
| Comment | `comment:<github id>` | a human comment on an issue or PR |
| Label | `label:<owner>/<name>/<label>` | a repo label |
| Doc, Chunk | `doc:<owner>/<name>/<path>`, `chunk:<doc key>#<i>` | a markdown doc and its embedded sections |
| Decision | `decision:<sha1>` | a decision the LLM found in a PR thread or design doc, kept only if its quote is verbatim in the source |
| AgentRun, Action, Feedback, Rule | `run:`, `action:`, `feedback:`, `rule:` + uuid | agent memory: a triage run, what it proposed, what a human said, and the rule that feedback created |

| Relationship | From → To | Meaning |
| --- | --- | --- |
| AUTHORED | Person → PR, Issue | wrote it |
| REVIEWED | Person → PR | reviewed someone else's PR; `state` is their latest review |
| REVIEW_REQUESTED | PR → Person | an open review request: current review load |
| TOUCHES | PR → File | changed the file (none for sweeps) |
| IN | File → Module, Module → Repo | where code lives |
| FIXES | PR → Issue | "fixes / closes / resolves #n" in the PR body |
| REPLAY_OF | Issue → Issue | a sandbox issue → the upstream issue it replays |
| HAS_LABEL | Issue, PR → Label | labelled |
| WROTE, ON | Person → Comment → Issue, PR | who said what, where |
| OWNS | Team → Module | from CODEOWNERS directory rules |
| MEMBER_OF | Person → Team | inferred teams, and one-person CODEOWNERS teams; org team membership isn't public |
| REVIEWED_FOR | Person → Person | derived: reviewer → author, `n` = PRs reviewed |
| DESCRIBES, HAS_CHUNK | Doc → Module, Doc → Chunk | a doc about a module, split for vector search |
| MADE_IN, ABOUT | Decision → PR, Issue, Doc; Decision → Module | where a decision was made and what code it governs |
| HANDLED, USED_EVIDENCE, PROPOSED | AgentRun → Issue, any Node, Action | agent memory: the issue, every key cited, the actions proposed |
| ON, DERIVED_FROM, APPLIES_TO, TARGETS | Feedback → Action, Rule → Feedback, Rule → Person, Action → Person or Label | agent memory: feedback and the rules it creates |

Rules that shape the data:

- **Bots are not people.** Bot accounts (dependabot, Copilot, coderabbitai and others) get no Person, AUTHORED, REVIEWED, REVIEW_REQUESTED or WROTE, and their comments aren't loaded. About half of all reviews come from review bots, which would otherwise top every ranking.
- **Sweeps carry no expertise.** A PR that touches more than 300 files, or whose head is a same-repo branch that is the base of 3 or more PRs (a release merge such as `staging` into `main`), keeps every edge except TOUCHES.
- **Graph algorithms run in FalkorDB.** `algo.pageRank`, `algo.betweenness` and `algo.labelPropagation` run over REVIEWED_FOR and are stored on Person. Communities of 2 or more people become `team:inferred/<n>` Teams, because FalkorDB's own CODEOWNERS covers only `/build/` and `/.github/`.
- **Embeddings are local.** `all-MiniLM-L6-v2` (384-d) vectors on Issue, Chunk and Decision, stored with `vecf32()` behind cosine vector indexes.

### Counts

From a fresh `python -m ingest.load --tenant falkordb` on 2026-10-16, pasted as printed, trimmed to the per-repo summary and the counts. A load never drops the graph, which also holds agent memory. The 210 Decisions and their MADE_IN and ABOUT edges were written earlier by `python -m ingest.decisions`. No triage had run at that point, so the printout has no agent memory nodes; the live graph now also holds the AgentRuns, Actions, Feedback and Rules that triage and feedback have written.

```text
FalkorDB/FalkorDB: 1006 PRs, 33 sweeps (33 by size, 0 by branch)
  skipped 5997 bot comments
  skipped 2295 bot reviews
  skipped 287 bot-authored PRs (no AUTHORED)
  skipped 77 comments on PRs or issues outside the window
  CODEOWNERS /build/ @FalkorDB/falkordb-core -> build
  CODEOWNERS /.github/ @FalkorDB/falkordb-core -> .github
  CODEOWNERS /CODEOWNERS @FalkorDB/falkordb-core -> nothing
FalkorDB/GraphRAG-SDK: 164 PRs, 1 sweeps (1 by size, 0 by branch)
  skipped 1379 bot comments
  skipped 534 bot reviews
  skipped 53 bot-authored PRs (no AUTHORED)
  skipped 5 comments on PRs or issues outside the window
FalkorDB/falkordb-browser: 781 PRs, 57 sweeps (0 by size, 57 by branch)
  skipped 6275 bot comments
  skipped 1789 bot reviews
  skipped 368 bot-authored PRs (no AUTHORED)
  skipped 2 comments on PRs or issues outside the window
edges dropped (endpoint not in the data): FIXES 18
nodes:
  Chunk                  500
  Comment               5688
  Decision               210
  Doc                     18
  File                  3348
  Issue                 2010
  Label                  175
  Module                  71
  PR                    1951
  Person                 294
  Repo                     3
  Team                     4
edges:
  ABOUT                  207
  AUTHORED              3252
  DESCRIBES               18
  FIXES                  866
  HAS_CHUNK              500
  HAS_LABEL             3781
  IN                    3419
  MADE_IN                210
  MEMBER_OF               38
  ON                    5688
  OWNS                     2
  REVIEWED              1222
  REVIEWED_FOR            85
  REVIEW_REQUESTED        71
  TOUCHES              13519
  WROTE                 5688
sweeps: 91
loaded tenant_falkordb in 130.2s
```

## Cypher

Every query the product runs is a file in [`cypher/`](../cypher/), loaded by name and always run with parameters. Each line says what the query does and why it needs a graph.

Triage tools (`api/tools/`, served to the agent over MCP):

- `affected_modules.cypher`: finds the past issues nearest a new one by vector search, then walks Issue ← FIXES ← PR → TOUCHES → File → IN → Module. That three-hop join from text similarity to code is something no single table holds.
- `rank_reviewers.cypher`: in one query, scores the people who AUTHORED or REVIEWED PRs touching a module, divides by their open REVIEW_REQUESTED load, and zeroes anyone an active avoid Rule from agent memory APPLIES_TO. All three facts sit next to the same Person node.
- `bus_factor.cypher`: Module ← IN ← File ← TOUCHES ← PR ← AUTHORED ← Person, grouped by author. Ownership concentration is a count of paths.
- `find_decisions.cypher`: vector search over Decisions, then MADE_IN to the PR, issue or doc, ABOUT to the module, and OWNS back to its Team. It returns why a decision was made, where, and who owns the code.
- `find_chunks.cypher`: vector search over doc Chunks, joined over HAS_CHUNK to their Doc. The `find_decisions` tool runs it alongside the decisions.
- `explain_path.cypher`: `algo.SPpaths` shortest path, up to 4 hops, between two keys over evidence edges only. The proof behind a claim is a path in the graph.
- `recall_run.cypher`: agent memory. One traversal from an AgentRun to every key it USED_EVIDENCE, the Actions it PROPOSED, the Feedback ON them, and the Rules DERIVED_FROM that feedback.
- `similar_issues.cypher`: the nearest Issues by vector index alone, with no traversal. The loader uses it as a sanity check, and it is the starting point for Proof's vector-only baseline, which the graph is measured against.

API reads (`/tenants/{tenant}/...`):

- `subgraph.cypher`: seed nodes, their one-hop neighbours and every edge between them. This is what the canvas draws.
- `stats.cypher`: node counts per label and edge counts per type, for `/stats` and the load summary above.

Agent (`api/agent.py`, `api/memory.py`):

- `issue_context.cypher`: the issue under triage with its author, and over REPLAY_OF the upstream issue it replays with that issue's author, so a replay never finds its own original or suggests its own author.
- `issue_by_number.cypher`: the issues with a number, sandbox issues first. A tenant spans several repos, so a bare number can be ambiguous.
- `record_run.cypher`: one query, so one transaction: the AgentRun, HANDLED to its issue, USED_EVIDENCE to every key it cited, and the Actions it PROPOSED, each TARGETS its Person or Label. Episodic memory is a subgraph wired into the same nodes the evidence came from.
- `run_context.cypher`: what `apply_triage` needs for a run: the issue it HANDLED with its own number and sandbox flag, and the Actions it PROPOSED.
- `list_runs.cypher`: the newest AgentRuns, for the live runs list.
- `feedback_target.cypher`: from an AgentRun to the Action it PROPOSED and the Person that Action TARGETS, so feedback is checked against the run that made the suggestion.
- `record_feedback.cypher`: writes Feedback ON the Action and, for a rejected reviewer, MERGEs the one avoid Rule for that Person and scope, which APPLIES_TO the Person and is DERIVED_FROM each Feedback, in one write. A repeated reject reactivates the same Rule rather than adding another. The `rank_reviewers` query reads that Rule on the next triage, so the feedback changes the ranking inside the graph query, with no LLM call.

Load (`ingest/load.py`), in the order it runs:

- `indexes.cypher`: a `key` index on every label, vector indexes on Issue, Chunk and Decision, and a full-text index on Issue. They make every MERGE by key and every lookup an index seek, not a scan.
- `load_reset.cypher`: deletes REVIEW_REQUESTED before rewriting it. Load is current state, so an edge must go when a review request is withdrawn.
- `load_cleanup.cypher`: removes TOUCHES from PRs marked as sweeps, so a release merge never makes its author an expert on every module.
- `drop_stale_chunks.cypher`: deletes Chunks the current docs no longer produce, so vector search never returns text that is gone.
- `derive_reviewed_for.cypher`: collapses Person → REVIEWED → PR ← AUTHORED ← Person into one weighted Person → Person edge. This is the social graph the algorithms run on.
- `algo_scores.cypher`: FalkorDB's `pageRank`, `betweenness` and `labelPropagation` over REVIEWED_FOR, stored on Person.
- `inferred_teams.cypher`: turns label-propagation communities into Team nodes with MEMBER_OF edges. Keys are numbered by community size, so the same data gives the same keys and a reload reuses the teams rather than recreating them, which lets agent-memory edges into a team survive. A reload that changes the communities can renumber them.
- `set_embeddings.cypher`: writes vectors with `vecf32()` so the vector indexes pick them up.
- `top_people.cypher`: the highest-pagerank people with their betweenness, printed after the algorithms run.
- `inferred_team_members.cypher`: each inferred team with its members, printed after the teams are rebuilt.

Decisions job (`ingest/decisions.py`):

- `decision_sources.cypher`: the most-discussed merged PRs, each with the modules it touches (TOUCHES → IN) and its human comments (WROTE → ON) in order, as LLM input.
- `decision_docs.cypher`: ADR, RFC and design docs with their Chunks in order and the Module each DESCRIBES.
- `module_keys.cypher`: checks the LLM's module paths against real Module nodes, so a decision is never attached to code that doesn't exist.
- `write_decisions.cypher`: MERGEs each Decision with MADE_IN to its source, ABOUT to its modules, and its embedding.

Proof and benchmarking:

- `backtest_sample.cypher`: samples closed, non-incoming Issues evenly across history after a warmup, with merged, non-sweep fixes reviewed by someone other than the issue author. FIXES → TOUCHES → IN, REVIEWED and HAS_LABEL supply the non-test modules, reviewers and labels used as truth.
- `baseline_codeowners_reviewers.cypher`: follows Module ← OWNS ← Team ← MEMBER_OF ← Person for up to three owners, falling back to the repo's most frequent reviewers of PRs merged before the cutoff. Ownership and review history meet at Person nodes, with excluded logins left out.
- `baseline_similar_labels.cypher`: takes the three nearest earlier labelled Issues by vector search, then follows HAS_LABEL to rank two labels by usage, breaking ties by nearest issue. The edges turn text similarity into label predictions.
- `baseline_similar_modules.cypher`: takes the nearest earlier Issue with a fix merged before the cutoff, then follows FIXES → TOUCHES → IN to rank up to three non-test modules by files touched. The paths connect a similar report to the code its fixes changed.
- `baseline_similar_reviewers.cypher`: takes the ten nearest earlier Issues with fixes merged before the cutoff, then follows FIXES and AUTHORED or REVIEWED to rank three people by PR count, leaving out excluded logins. It returns the supporting issue and PR keys alongside each person.
- `baseline_top_labels.cypher`: counts HAS_LABEL edges from the repo's earlier, non-incoming Issues to return its two most-used labels. The newest supporting issue key lets Proof check the historical cutoff.
- `baseline_top_modules.cypher`: walks Repo ← IN ← Module ← IN ← File ← TOUCHES ← PR to rank three non-test modules by PRs merged before the cutoff. The newest supporting PR key lets Proof check that the counts use only past changes.
- `proof_evidence_times.cypher`: resolves returned evidence keys to Node timestamps and reports any dated at or after the cutoff, using merged_at before created_at. The shared Node label lets one leak check cover evidence of different types; undated nodes pass.
- `latest_run.cypher`: selects the newest timestamped AgentRun key for the recall benchmark, returning no row when none exists. It supplies a real graph-memory starting node for timing `recall_run`.

Actions and webhooks:

- `record_actions.cypher`: records each `apply_triage` result on the Action node a run PROPOSED, so feedback and recall see what actually happened. Only the live apply path calls it, through the `GraphActionStore` that `api/main.py` passes in live mode.
- `upsert_incoming_issue.cypher`: MERGEs one incoming sandbox Issue from a validated webhook job, its human author (AUTHORED) and an optional `REPLAY_OF` edge to an already-loaded upstream issue. The replay target must already exist in the tenant graph, so the query can attach provenance without ever fabricating an upstream Issue.
- `webhook_latest_run.cypher`: the latest HANDLED AgentRun for one exact incoming issue, with its evidence, proposed Actions and their Feedback/Rules, in the same row shape as `recall_run`. Feedback, actions and evidence sit behind one traversal, so a collaborator reject resolves the right action on the right run.
- `webhook_node_links.cypher`: resolves only the given keys' stored URLs in the current tenant. A key with no stored URL returns no row, so a `why` reply links real stored sources and never synthesizes one from the key or from comment text.
- `webhook_person_key.cypher`: resolves a GitHub login to the graph's canonical `person:<login>` key, matching case-insensitively because logins are case-insensitive while the key preserves the stored casing. An unknown login returns no row, so a `why` reply never invents a Person key.
- `notification_mark_run.cypher`: stamps webhook-origin lineage on an existing AgentRun that HANDLED an incoming sandbox issue. A parentless issue event roots the lineage at its own key and every descendant inherits the exact proven parent's root; the write is MATCH-only and happens once, so it never fabricates a node or silently reparents a run, and eligibility survives an application restart because it lives on the node rather than in process memory.
- `notification_latest_run.cypher`: the latest lineage-marked AgentRun for one exact incoming issue, with its evidence, Actions and Feedback in the same row shape as the recall query, ordered by the recorded notification time then run key. Restricting the match to the issue's HANDLED edge and an origin root is what stops an unrelated later manual run from ever being chosen as a follow-up baseline.
- `notification_run_meta.cypher`: the issue title and the issue's actual REPLAY_OF key for one issue run, plus the run's recorded lineage root. The node's own REPLAY_OF edge is the only trusted source, so a replay label is never inferred from arbitrary issue body or comment text.
- `notification_ui_parent.cypher`: a proven eligible parent for a UI feedback follow-up. It must HANDLE the exact incoming issue, carry an origin root, and have PROPOSED the Action that the exact persisted `given_by='ui'` Feedback is ON; requiring that parent → PROPOSED → Action ← ON ← Feedback path is what makes a client-supplied feedback reference non-authoritative.
