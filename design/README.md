# Keystone design system

Exported from the Keystone design system on claude.ai. Tokens: `tokens.json` (colours per theme, type, spacing, radius, shadow, graph geometry, opacity). Canvas rules: `graph-canvas.md`.

Keystone is a triage agent for an open-source org. It reads the org's history from a FalkorDB graph, decides who should handle a new issue, and shows the path that proves each claim. The interface exists to make one thing visible: **the evidence behind every action.** Everything quiet serves that; the amber path is the loudest thing on screen.

## Voice

Keystone writes like a careful maintainer leaving a triage note: short, specific, and checkable.

- Lead with the decision, then the reason, then the source. "Assign @octocat. Touched `src/serializers` in 9 merged PRs since March [1]."
- Every factual claim carries a citation chip. A claim without a source is not shown.
- State negative results plainly. "No one has changed `src/bolt` in 6 months. Escalating to the core team."
- Sentence case everywhere: buttons, titles, labels in the legend. Section labels (`label` style) are the one uppercase exception.
- Numbers keep their units: "412 ms", "9 PRs", "top-3 hit rate of 50 issues". Logins keep their `@`, issues keep their `#`.
- The agent does not say "I think" or "I believe". It says what it did: "Suggested", "Applied", "Skipped (dry run)".
- No emoji, no exclamation marks, no "magic" or "AI-powered".

## Color

The base follows GitHub's Primer greys (version 2 of the tokens): an inset `background`, raised `card` panels, `muted` box headers and code blocks, and `border` hairlines. People who triage open-source issues live in GitHub all day, so the workspace reads like it. Amber is warm against the greys and is reserved:

- `primary` (amber fill) marks the one primary action per view: **Run triage** or **Apply to sandbox**. Its label is `primary-foreground`.
- `evidence` marks evidence: the highlighted graph path, citation chips, the sentence a chip points at (on `evidence-soft`). In light theme, amber text is always `evidence`, never `primary`.
- Nothing else is amber. Not links, not hover states, not charts other than the graph's own result.

Status colours always sit beside a word or icon: `good` for applied, open issues and passing; `destructive` (alias `risk`) for risk flags, failed tool calls and Reject; `attention` for warnings that are not failures (fallback runs, the bus-factor meter); `done` for closed issues and merged PRs; `link` for links out to GitHub (issues, PRs, profiles). Labels keep their own GitHub colour, as a tinted pill. Text tokens are checked at 4.5:1 or more on `background`, `card`, `muted` and `popover` in both themes; `input`, `ring` and `edge-strong` are 3:1 or more.

**Entity colours** come from the graph model. Each kind also has a fixed shape, so colour is never the only cue:

| Kind | Token | Shape | Nodes |
| --- | --- | --- | --- |
| Person | `node-person` | circle | Person |
| Team | `node-team` | hexagon | Team |
| Code | `node-code` | rounded square | Repo, Module, File |
| Ticket | `node-issue` | ring | Issue |
| Change | `node-pr` | notched circle | PR |
| Conversation | `node-pr` at `opacity-ghost` | small dot | Comment |
| Tag | `node-code` | small square | Label |
| Knowledge | `node-knowledge` | diamond | Doc, Chunk, Decision |
| Memory | `node-memory` | dashed ring | AgentRun, Action, Feedback, Rule |

The same token colours an entity's badge and its mono label in the triage card and the trace, so a reader can match `@octocat` in the text to the blue circle on the canvas.

## Type

Two families, both from Google Fonts. **Instrument Sans** (`sans`) for the interface; **JetBrains Mono** (`mono`) for anything a person might copy: Cypher, logins, paths, issue numbers, tool names, durations in the trace.

- `body` (14px) is the default. This is a dense tool; nothing readable goes below `caption` (12px).
- `title` names panels and cards. `display` appears only on the empty state and the landing view.
- Section and panel headings are 14px semibold, in sentence case, with an icon and a count, as on GitHub. `label` (uppercase, letter-spaced) stays in the tokens for the node kind in the graph hover card and is otherwise unused.
- `code` sets Cypher in the trace; `mono-sm` sets node labels on the canvas and inline identifiers.
- `metric` is reserved for the one headline number (the backtest hit rate), with tabular figures.

## Layout

One screen, three columns, sized to the viewport at desktop width. Each column scrolls on its own and the page never does.

1. **Left, 280px: runs.** The triage form (key, number or title, then **Run triage**) above the recent runs. Each run shows its repo and issue number, its age and a two-line summary, plus a token only when it is a fallback run or new.
2. **Centre, flexible: the issue**, laid out like a GitHub issue page: title and number, open/closed state, repo and author, then Keystone's summary with the model, LLM calls and graph time. Three tabs follow. **Triage** shows the four claim sections as Primer boxes. **Actions** lists what the run would do on GitHub and previews the drafted comment as rendered markdown. **Ask** answers questions from past decisions. The Apply bar is pinned to the bottom.
3. **Right, 32vw (360 to 500px): graph above trace.** The graph canvas with its legend docked bottom-left, then the tool trace. Both stay visible, so a citation lights the graph path and the trace row together. Each trace row shows a tool icon, the tool name, a one-line summary of what it found and its duration. Expanding a row shows its parameters and raw result as highlighted JSON, then its Cypher (highlighted, with Copy).

Panels are `card` with a `border` hairline and `radius-lg`, separated by `space-3`; boxes inside panels use `radius-md`. Below 1024px the columns stack and the trace becomes a drawer. Below 640px the panes stack in reading order: runs, issue, canvas (fixed 320px tall), trace.

## Interaction

- Each claim has one evidence control, an amber outlined **N sources** button. Choosing it resolves every citation of the claim, highlights the subgraph on the canvas, and scrolls the supporting tool call into view in the trace. Clicking a node does the reverse: every claim that cites it, and its trace rows, light up. Graph keys inside claim text (`person:octocat`, `module:o/r/src`) are shown as their short labels in the entity colour, with the full key on hover.
- Reviewer rows show a GitHub avatar (loaded from github.com in the browser, never stored), `@login` linked to the profile, the graph's expertise and open-review counts, and tokens for **Top match**, ownership share and avoid rules.
- Feedback is two buttons on each suggestion: **Accept** (secondary) and **Reject** (destructive outline). Reject opens one text field: "Why? Keystone will remember this as a rule." The new Rule appears in the trace as its own row.
- Dry run is on by default and shown as a switch beside **Apply to sandbox**. When it is on, the button reads **Apply to sandbox (dry run)**. Running a triage never writes to GitHub, so **Run triage** has no dry-run state.
- A first visit opens a welcome dialog: a five-slide manual slideshow of what Keystone does, ending in an offer to start the guided tour (driver.js, one step per pane). **Help** in the header reopens it.
- Focus uses `focus-ring` on every control. Motion is limited to the evidence path drawing in over 240ms and the triage spinner; with `prefers-reduced-motion` both appear at once.

## Iconography

Lucide icons (shadcn's default), 16px at 1.75 stroke. An icon takes its entity colour when it names an entity (`node-person` for reviewers, `node-issue` for issues, `node-pr` for PRs, `node-knowledge` for decisions) or a status colour when it carries status; otherwise it is `muted-foreground`. No emoji. The only illustrations are the welcome dialog's diagrams, drawn with the canvas's own node shapes and colours so they teach the legend. The mark is a keystone wedge over an arch, in `foreground`, beside the name in Instrument Sans 600.

## Using with shadcn

Color token names match shadcn's CSS variables (`background`, `card`, `primary`, `muted-foreground`, `ring`, `chart-1` to `chart-5`). Put the dark values on `:root` and the light values on `.light`, since dark is the default theme. Keystone-only tokens (`canvas`, `evidence`, `evidence-soft`, `good`, `risk`, `node-*`, `edge*`) sit beside them under the same names. Note that shadcn's `accent` is a hover surface; Keystone's brand colour is `primary`. `--radius` is `radius-md`.
