# B5 — Graph canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each claim's graph evidence inspectable through citation selection and reverse node-to-chip/trace highlighting.

**Architecture:** Replace `CanvasPlaceholder` with a client-only `react-force-graph-2d` renderer, keeping immutable shared-contract data separate from the library's mutable simulation objects. `Workspace` owns selection; B4's path/subgraph API supplies real paths. Pure display helpers control shapes, styles and exact path-edge membership; they never infer a product path in JavaScript.

**Tech Stack:** Next.js App Router, React, TypeScript, `react-force-graph-2d`, existing CSS design tokens and Playwright.

**Spec:** `design/README.md`, `design/graph-canvas.md`, `design/tokens.json`; `docs/spec.md` §§6, 9, 11; `docs/tasks.md` B5; B4 plan's client interfaces; review decisions in `2026-10-15-b4-b6-review.md`.

## Global Constraints

- At most **150 nodes**; collapse Files into their Module and Chunks into their Doc past that limit.
- Node radii: `node-size-sm` **5px**, `node-size-md` **8px**, `node-size-lg` **12px**.
- At rest edges: `edge`, **1px**, no arrowheads; direction is in hover details.
- Evidence edges: `evidence`, **2.5px**; nodes gain a **2px** evidence ring; outside opacity **0.22**.
- Hover neighbours retain full strength, outside opacity **0.5**, incident edges use `edge-strong`.
- Evidence draws outward over **240ms**, immediately with reduced motion.
- Labels: `mono-sm`; large/hovered nodes only; at most about **30 labels**. Phone canvas is **320px** tall.
- Ground is `canvas`, with no grid/background pattern; entity shapes/colours exactly follow `design/README.md`.
- No shared contract or A-owned query/tool changes; no fabricated evidence paths.

## Review Focus

1. Multiple citations and parallel relationships highlight only actual returned path segments, not every edge between selected nodes.
2. Rapid claim changes, tenant changes and run changes cannot apply a stale path response.
3. Unreachable evidence and empty tool results remain visible as absence, not invented connections.
4. Browser-only simulation mutation, resize or theme changes do not corrupt wire data or crash SSR.
5. Keyboard, touch and reduced-motion users can inspect the same evidence without relying on canvas hover.

---

## File map and prerequisites

- Create `web/components/graph/graph-canvas.tsx`: client wrapper, measurement, loading/error, accessible node controls and legend.
- Create `web/components/graph/force-canvas.tsx`: browser-only force renderer and canvas callbacks.
- Create `web/lib/graph-display.ts`: display node/edge copies, exact path matching, per-kind shape/token/size configuration.
- Create `web/lib/evidence-selection.ts`: async claim-to-path orchestration and pure selection types.
- Modify `web/components/workspace.tsx`, `web/components/triage-card.tsx`, `web/components/citation-chip.tsx`, `web/components/trace-panel.tsx`, `web/app/globals.css`.
- Modify `web/package.json` / lockfile for the prescribed stack dependency, `web/tests/shell.spec.ts`; create `web/tests/graph-canvas.spec.ts`.
- B4 supplies `getPath`, `getSubgraph`, selected `issueKey`, live/fixture identity, and graph route behavior. Test mocks may supply paths; live mode must use A's actual evidence-only query.
- Do not delete the old placeholder until the replacement is integrated and its shell tests updated. No redesign of the surrounding panes.

### Task 1: Token-driven force renderer (~35 min)

**Interfaces:** `buildDisplayGraph(subgraph: Subgraph): DisplayGraph` clones inputs and uses `nodeId="key"`; `kindStyle(kind: GraphKind, isIssue: boolean): { shape: NodeShape; colorToken: string; radiusToken: string }` lives in `web/lib/graph-display.ts`. `GraphCanvas({subgraph, issueKey, selection, onNodeSelect})` takes `Subgraph`, `string | null`, `EvidenceSelection | null`, and `(key: string) => void`. `EvidenceSelection` is defined in Task 2; begin with null-only rendering until then.

- [ ] **Step 1: Write failing tests** in `web/tests/graph-canvas.spec.ts`: `canvas renders without SSR hydration errors`, `all graph kinds have specified shapes and sizes`, `force graph leaves input models unchanged`, `canvas resizes and theme changes update token colours`, `empty graph is an explicit empty state`. Exercise pure helpers with imported assertions in the Playwright test runner and actual drawing via browser screenshots; DOM state alone does not prove canvas pixels.
- [ ] **Step 2: Run** `npm run test:e2e -- tests/graph-canvas.spec.ts` from `web/`; expect missing-canvas failures.
- [ ] **Step 3: Install** `react-force-graph-2d` and commit the resolved version in package/lockfile; it is already in the approved stack. Read installed Next guidance on client-only dynamic imports. Load the renderer with SSR disabled from a client component, not directly from a server page.
- [ ] **Step 4: Implement** canvas with measured container dimensions (`ResizeObserver`, cleaned up on unmount), bounded initial zoom-to-fit, library cleanup and cloned mutable nodes/links. CSS token values are read as resolved colours; refresh on theme changes. Do not use CSS `var(...)` strings directly as canvas stroke/fill colours. Preserve stable keys in click callbacks even when library link endpoints become node objects.
- [ ] **Step 5: Implement** Person circle; Team hexagon; Repo/Module/File rounded square; Issue ring; PR notched circle; Comment ghost small dot; Label small square; Doc/Chunk/Decision diamond; AgentRun/Action/Feedback/Rule dashed ring. Team/Repo/Module/current Issue use large size; File/Chunk/Comment/Label small; all other kinds medium. Current issue alone has a foreground 2px outline at rest. Clip labels to ~30; draw only large/hovered labels. At rest omit arrows and relationship captions.
- [ ] **Step 6: Handle the cap honestly**: B4 already caps the wire response. When loading more context, ask A to collapse overflow File/Chunk neighbours to retained Module/Doc keys in the query result before the 150-node cap; do not infer ownership by parsing paths. If no containment data is provided, retain the cap and visible limit notice rather than invent edges. Do not expose aggregate nodes as citeable evidence. Existing returned `IN`/`HAS_CHUNK` edges may drive display-only collapse when those endpoints are actually present.
- [ ] **Step 7: Verify and commit**: `npm run typecheck` and `npm run test:e2e -- tests/graph-canvas.spec.ts` → pass, including screenshot inspection in dark/light. Commit `B5: render token-driven graph canvas`.

### Task 2: Claim ↔ path ↔ node selection (~45 min)

**Interfaces:** `EvidenceSelection = { claimId: string | null; citationKeys: string[]; paths: PathData[]; nodeKeys: string[]; missingKeys: string[]; state: 'loading' | 'ready' | 'error' }` in `web/lib/evidence-selection.ts`. Claim IDs are local `${section}:${index}`, reset with tenant/run. `resolveClaimEvidence(tenant: string, issueKey: string, claim: Claim, signal: AbortSignal): Promise<EvidenceSelection>` calls B4's `getPath` once per distinct citation and unions returned paths. `pathEdgeIds(paths: PathData[], edges: GraphEdge[]): Set<string>` identifies path segments by adjacent stable keys **and relationship type**, accepting reverse traversal while retaining stored edge direction.

- [ ] **Step 1: Write failing tests**: `claim chip selects all its claim citations`, `parallel nonpath edge stays dim`, `node selection lights every matching chip and trace row`, `late response cannot replace newer selection`, `unreachable citation does not create a path`, `empty tool result shows module absence`. Use two distinct claims and delayed API replies, plus a graph with two relationship types between the same endpoints. Assert highlighted segment IDs as well as screenshot pixels.
- [ ] **Step 2: Run** `npm run test:e2e -- tests/graph-canvas.spec.ts`; expect selection assertions to fail before implementation.
- [ ] **Step 3: Extend chip callbacks** to carry the owning claim (`onSelectClaim(claimId: string, claim: Claim)`) while keeping accessible button labels and citation numbers. Workspace sets full claim citations for chips/trace, cancels prior requests, and clears selection on tenant/run/issue change. A stale or aborted result must not mutate graph/highlight state. Show path-loading/error state without hiding the trace or inventing fallback edges.
- [ ] **Step 4: Resolve real paths** from the selected issue to each citation via `/path`. Treat `from===to` as one node with zero edges. For missing path nodes, request `/subgraph` with issue + path keys prioritised; merge by stable key within 150, with endpoint closure. Never highlight a disconnected edge merely because both ends appear in the citation set. For fixture mode, use its explicit path fixture as a labelled demonstration only; do not calculate new fixture paths locally.
- [ ] **Step 5: Implement highlighting** with full-colour path nodes, 2px evidence rings, exact evidence edges at 2.5px and outside opacity 0.22. Relationship labels appear only on highlighted edges, on `card` pills with `radius-sm`. A returned empty path shows “No path found”; for a supported negative result show the cited module with dashed risk ring. Do not classify arbitrary prose as a negative result: require a supporting trace entry with `empty: true` that references the module via evidence or the tool's module parameter. A missing graph node produces an explicit missing-evidence notice.
- [ ] **Step 6: Implement reverse selection**: clicking a node sets `[key]` as chip/trace evidence, clearing prior claim paths; all chips that cite that exact key and all supporting trace rows light up. Keep first-row scroll/expand behavior; ensure tablet drawer opens after selection. Clear selection through an accessible button/Escape without trapping focus. Clicking a blank canvas clears selection; dragging a node must not accidentally activate a chip.
- [ ] **Step 7: Verify and commit**: run graph tests plus updated `web/tests/shell.spec.ts`; preserve trace expansion/copy and replace placeholder-specific expectations. Commit `B5: link claim evidence paths and graph selection`.

### Task 3: Inspection, accessibility and acceptance (~30 min)

**Interfaces:** Hover details use only returned `GraphNode.properties` (no user-profile requests), safe HTTP(S) source links and stored edge direction. Accessible node buttons invoke the same `onNodeSelect(key)` callback as canvas clicks.

- [ ] **Step 1: Write failing tests**: `keyboard node list selects evidence`, `phone tap exposes node details`, `hover only emphasizes direct neighbours without overriding selected paths`, `reduced motion has no path reveal delay`, `legend lists visible kinds only`. Test 390px, 800px and 1440px layouts, and long labels/properties.
- [ ] **Step 2: Run** the named graph tests; expect missing inspection behaviors to fail.
- [ ] **Step 3: Add** hover/focus card with kind label, node label, two or three safe properties, GitHub link and relationship direction; clamp to viewport and escape text. Retain selected-path precedence over hover styles; otherwise dim non-neighbours to 0.5 and use `edge-strong` for incident edges. Show legend bottom-left on `card`; add amber “Evidence” line only when a path is selected. Provide a compact expandable semantic node list for keyboard/touch inspection, not a hidden testing-only backdoor.
- [ ] **Step 4: Add** outward path reveal over 240ms, immediately under `prefers-reduced-motion`. Animate only returned path segments; stop animation on unmount or selection change. If schedule slips, explicitly defer animation and hover polish rather than selection, accessibility or trace integration.
- [ ] **Step 5: Verify** `npm run typecheck`, `npm run test:empty`, fixture/API Playwright suites and `npm run build` from `web/`. Inspect screenshots to verify shapes, colour/opacity, path captions and mobile height. Run `graphify update .` after code changes.
- [ ] **Step 6: Show Done when evidence**: with a real triage/run once A10 is available (or a clearly labelled fixture before that), click a multi-citation chip and record only the returned path staying vivid; click a Person node and record its chips and trace lighting up. Note whether evidence was fixture or real; never present fixture footage as real. Commit `B5: add graph inspection and acceptance checks`.

## Scope cuts and handoff

No alternative layouts, graph editing, new filters, full graph browsing or 3D. The existing path fixture includes `HAS_LABEL`, while the real query explicitly excludes that relationship: preserve fixture compatibility, but verify real paths use only A's allowed evidence relationships. A label citation may legitimately have no real issue-to-label path; do not “fix” the query in B's lane or misrepresent that absence.
