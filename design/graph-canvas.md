# Graph canvas

The canvas answers one question at a time: "why did Keystone say that?" At rest it shows the neighbourhood of the issue under triage. When a claim is selected, everything except that claim's evidence recedes.

## At rest

- Ground is `canvas`. No grid, no background pattern.
- Nodes use their entity token and shape (see Color). Sizes: `node-size-lg` for Team, Repo, Module and the issue under triage; `node-size-md` for Person, Issue, PR, Doc, Decision and memory nodes; `node-size-sm` for File, Chunk, Comment and Label.
- The issue under triage is drawn at `node-size-lg` with a 2px `foreground` outline. It is the only outlined node at rest.
- Edges are `edge` at `edge-width`, without arrowheads. Direction lives in the hover card, not on the line.
- Labels in `mono-sm`, `muted-foreground`, shown only for `node-size-lg` nodes and the hovered node. Never more than about 30 labels on screen.
- Show at most 150 nodes. Past that, collapse Files into their Module and Chunks into their Doc.

## Evidence path (a claim is selected)

- Nodes and edges on the path keep full colour; nodes gain a 2px `evidence` ring and edges become `evidence` at `edge-width-evidence`.
- Everything else drops to `opacity-dim`.
- Edge labels (the relationship type, in `mono-sm`) appear only on path edges, on a `card` pill with `radius-sm`.
- The path draws from the issue outward over 240ms. With reduced motion, it appears at once.
- A negative result ("no recent author for this module") is shown as the module node with a dashed `risk` ring and a "No path found" caption under it, so absence is visible.

## Hover

- Hovering a node raises a hover card (`popover`, `shadow-popover`, `radius-lg`): the node's kind as a `label`, its name in `body-strong`, two or three properties in `mono-sm`, and its GitHub link.
- Its direct neighbours stay at full strength, their edges become `edge-strong`, and the rest of the canvas falls to `opacity-ghost`.

## Legend

Docked bottom-left on a `card` panel: one row per entity kind, with its shape and its name in `caption`. When a path is selected, a last row reads "Evidence" with an amber line sample. The legend lists only kinds that are on screen.
