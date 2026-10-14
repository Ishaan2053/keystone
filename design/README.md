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

The ground is granite (`background`, `card`, `muted`), biased slightly cool. Amber is warm against it and is reserved:

- `primary` (amber fill) marks the one primary action per view: **Run triage** or **Apply to sandbox**. Its label is `primary-foreground`.
- `evidence` marks evidence: the highlighted graph path, citation chips, the sentence a chip points at (on `evidence-soft`). In light theme, amber text is always `evidence`, never `primary`.
- Nothing else is amber. Not links, not hover states, not charts other than the graph's own result.

Status colours always sit beside a word or icon: `good` for applied and passing, `destructive` (alias `risk`) for bus-factor flags, failed tool calls and Reject. Text tokens are checked at 4.5:1 or more on `background`, `card`, `muted` and `popover` in both themes; `input`, `ring` and `edge-strong` are 3:1 or more.

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
- `label` is the uppercase section label over a panel or legend group, letter-spaced 0.06em.
- `code` sets Cypher in the trace; `mono-sm` sets node labels on the canvas and inline identifiers.
- `metric` is reserved for the one headline number (the backtest hit rate), with tabular figures.

## Layout

One screen, three panes, at desktop width:

1. **Left, 360px: the triage card.** Issue input at the top, then the decision (labels, reviewers, risk flags), each line ending in citation chips. The primary action sits at the bottom of this card.
2. **Centre, flexible: the graph canvas** on `canvas`, edge to edge, with the legend docked bottom-left. See `graph-canvas.md`.
3. **Right, 400px: the trace.** One row per tool call: tool name in `mono-sm`, arguments, rows returned, duration in `muted-foreground`. A row expands to show its Cypher in a `muted` block with a Copy button.

Panels are `card` with a `border` hairline and `radius-lg`, separated by `space-4`. Rows inside pad `space-2` by `space-3`. Below 1024px the canvas moves under the triage card and the trace becomes a drawer. Below 640px the panes stack in reading order: card, canvas (fixed 320px tall), trace.

## Interaction

- Clicking a citation chip highlights its subgraph on the canvas and scrolls its tool call into view in the trace. Clicking a node does the reverse: its chips and trace rows light up.
- Feedback is two buttons on each suggestion: **Accept** (secondary) and **Reject** (destructive outline). Reject opens one text field: "Why? Keystone will remember this as a rule." The new Rule appears in the trace as its own row.
- Dry run is on by default and shown as a switch beside **Apply to sandbox**. When it is on, the button reads **Apply to sandbox (dry run)**. Running a triage never writes to GitHub, so **Run triage** has no dry-run state.
- Focus uses `focus-ring` on every control. Motion is limited to the evidence path drawing in over 240ms; with `prefers-reduced-motion` it appears at once.

## Iconography

Lucide icons (shadcn's default), 16px at 1.5 stroke, in `muted-foreground` unless they carry status. No emoji, no illustrations. Keystone has no logo yet: the name is set in Instrument Sans 600 wherever a mark would go.

## Using with shadcn

Color token names match shadcn's CSS variables (`background`, `card`, `primary`, `muted-foreground`, `ring`, `chart-1` to `chart-5`). Put the dark values on `:root` and the light values on `.light`, since dark is the default theme. Keystone-only tokens (`canvas`, `evidence`, `evidence-soft`, `good`, `risk`, `node-*`, `edge*`) sit beside them under the same names. Note that shadcn's `accent` is a hover surface; Keystone's brand colour is `primary`. `--radius` is `radius-md`.
