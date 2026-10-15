import { test, expect, type Page } from '@playwright/test';
import { buildDisplayGraph, kindStyle, RADIUS_PX, NODE_LIMIT, segmentId } from '../lib/graph-display';
import { createSelectionGuard, pathEdgeIds, resolveClaimEvidence, supportedAbsenceKeys } from '../lib/evidence-selection';
import type { Claim, GraphEdge, GraphKind, GraphNode, PathData, Subgraph, ToolResult } from '../lib/types';
import triage from '../../api/fixtures/triage_result.json';

/**
 * B5 Task 1 — token-driven force renderer.
 *
 * Pure helpers are exercised with imported assertions (shape/token/size table,
 * input cloning, the 150-node collapse cap). Actual drawing is proved from the
 * canvas pixel buffer in a browser, because DOM state alone does not prove that
 * the renderer painted anything.
 */

const HARNESS = '/harness/graph';

const EXPECTED: Record<GraphKind, { shape: string; colorToken: string; radiusToken: string }> = {
  Person: { shape: 'circle', colorToken: 'node-person', radiusToken: 'md' },
  Team: { shape: 'hexagon', colorToken: 'node-team', radiusToken: 'lg' },
  Repo: { shape: 'rounded-square', colorToken: 'node-code', radiusToken: 'lg' },
  Module: { shape: 'rounded-square', colorToken: 'node-code', radiusToken: 'lg' },
  File: { shape: 'rounded-square', colorToken: 'node-code', radiusToken: 'sm' },
  Issue: { shape: 'ring', colorToken: 'node-issue', radiusToken: 'md' },
  PR: { shape: 'notched-circle', colorToken: 'node-pr', radiusToken: 'md' },
  Comment: { shape: 'dot', colorToken: 'node-pr', radiusToken: 'sm' },
  Label: { shape: 'square', colorToken: 'node-code', radiusToken: 'sm' },
  Doc: { shape: 'diamond', colorToken: 'node-knowledge', radiusToken: 'md' },
  Chunk: { shape: 'diamond', colorToken: 'node-knowledge', radiusToken: 'sm' },
  Decision: { shape: 'diamond', colorToken: 'node-knowledge', radiusToken: 'md' },
  AgentRun: { shape: 'dashed-ring', colorToken: 'node-memory', radiusToken: 'md' },
  Action: { shape: 'dashed-ring', colorToken: 'node-memory', radiusToken: 'md' },
  Feedback: { shape: 'dashed-ring', colorToken: 'node-memory', radiusToken: 'md' },
  Rule: { shape: 'dashed-ring', colorToken: 'node-memory', radiusToken: 'md' },
};

const TOKEN_HEX: Record<string, { dark: string; light: string }> = {
  'node-person': { dark: '#8fb4e8', light: '#2f5e9e' },
  'node-team': { dark: '#62c3b6', light: '#16756a' },
  'node-code': { dark: '#a3abb5', light: '#5b6470' },
  'node-issue': { dark: '#7cc98e', light: '#2a713f' },
  'node-pr': { dark: '#b9a3e3', light: '#6a4fb0' },
  'node-knowledge': { dark: '#e59bbd', light: '#a3396a' },
  'node-memory': { dark: '#d9d4c7', light: '#6b6557' },
};

async function ready(page: Page) {
  await expect(page.locator('[data-graph-ready="true"]')).toBeVisible();
  await expect(page.locator('[data-graph-settled="true"]')).toBeVisible({ timeout: 15_000 });
}

async function cornerPixel(page: Page): Promise<[number, number, number]> {
  return page.evaluate(() => {
    const canvas = document.querySelector('[data-force-canvas] canvas') as HTMLCanvasElement;
    const ctx = canvas.getContext('2d')!;
    const d = ctx.getImageData(2, 2, 1, 1).data;
    return [d[0], d[1], d[2]] as [number, number, number];
  });
}

async function hasColor(page: Page, hex: string, tolerance = 24): Promise<boolean> {
  return page.evaluate(
    ([value, tol]) => {
      const canvas = document.querySelector('[data-force-canvas] canvas') as HTMLCanvasElement;
      const ctx = canvas.getContext('2d')!;
      const { width, height } = canvas;
      const data = ctx.getImageData(0, 0, width, height).data;
      const target = value.slice(1);
      const r = parseInt(target.slice(0, 2), 16);
      const g = parseInt(target.slice(2, 4), 16);
      const b = parseInt(target.slice(4, 6), 16);
      let hits = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] < 128) continue;
        if (Math.abs(data[i] - r) <= tol && Math.abs(data[i + 1] - g) <= tol && Math.abs(data[i + 2] - b) <= tol) {
          hits += 1;
          if (hits >= 4) return true;
        }
      }
      return false;
    },
    [hex, tolerance] as const,
  );
}

function hex(rgb: [number, number, number]): string {
  return '#' + rgb.map(c => c.toString(16).padStart(2, '0')).join('');
}

test('canvas renders without SSR hydration errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', error => errors.push(error.message));

  await page.goto(`${HARNESS}?case=fixture`);
  await ready(page);
  await expect(page.locator('[data-force-canvas] canvas')).toBeVisible();
  await expect(page.locator('[data-graph-node-list] button')).toHaveCount(8);

  const hydration = errors.filter(text => /hydrat|did not match|server.*client|client.*server/i.test(text));
  expect(hydration, `hydration errors: ${hydration.join(' | ')}`).toEqual([]);
});

test('all graph kinds have specified shapes and sizes', async ({ page }) => {
  for (const kind of Object.keys(EXPECTED) as GraphKind[]) {
    const expected = EXPECTED[kind];
    const style = kindStyle(kind, false);
    expect(style, `${kind} shape`).toEqual(expected);
  }
  // The issue under triage is the only kind whose size depends on context.
  expect(kindStyle('Issue', true).radiusToken).toBe('lg');
  expect(kindStyle('Issue', true).shape).toBe('ring');
  expect(RADIUS_PX).toEqual({ sm: 5, md: 8, lg: 12 });

  await page.goto(`${HARNESS}?case=all`);
  await ready(page);
  // Every entity colour family is actually painted on the canvas.
  for (const [token, value] of Object.entries(TOKEN_HEX)) {
    expect(await hasColor(page, value.dark, 32), `${token} painted (${value.dark})`).toBe(true);
  }
  // Shapes are structurally distinct, not just coloured: the legend names each kind.
  await expect(page.locator('[data-graph-legend] [data-kind]')).toHaveCount(Object.keys(EXPECTED).length);
  await expect(page.locator('[data-graph-legend] [data-kind="Team"] [data-shape="hexagon"]')).toHaveCount(1);
  await expect(page.locator('[data-graph-legend] [data-kind="Doc"] [data-shape="diamond"]')).toHaveCount(1);
  await expect(page.locator('[data-graph-legend] [data-kind="Rule"] [data-shape="dashed-ring"]')).toHaveCount(1);
});

test('force graph leaves input models unchanged', async () => {
  const subgraph: Subgraph = {
    nodes: [
      { key: 'person:a', kind: 'Person', label: '@a', properties: { login: 'a' } },
      { key: 'module:m', kind: 'Module', label: 'm', properties: { path: 'm' } },
    ],
    edges: [{ source: 'person:a', target: 'module:m', type: 'TOUCHED' }],
  };
  const snapshot = JSON.parse(JSON.stringify(subgraph));

  const graph = buildDisplayGraph(subgraph);
  expect(graph.nodeId).toBe('key');
  expect(graph.nodes).toHaveLength(2);
  expect(graph.links).toHaveLength(1);
  expect(graph.nodes[0]).not.toBe(subgraph.nodes[0]);
  expect(graph.nodes[0].properties).not.toBe(subgraph.nodes[0].properties);
  expect(graph.links[0].source).toBe('person:a');
  expect(graph.links[0].target).toBe('module:m');

  // Simulate the library mutating its own copies (positions and object endpoints).
  (graph.nodes[0] as unknown as { x: number; vx: number }).x = 10;
  (graph.nodes[0] as unknown as { vx: number }).vx = 1;
  (graph.links[0] as unknown as { source: unknown }).source = graph.nodes[0];
  graph.nodes.push({ key: 'extra', kind: 'Rule', label: 'x', properties: {} });

  expect(JSON.parse(JSON.stringify(subgraph))).toEqual(snapshot);
});

test('graph cap collapses files and chunks before the 150-node limit', () => {
  const modules: GraphNode[] = Array.from({ length: 3 }, (_, i) => ({ key: `module:m${i}`, kind: 'Module', label: `m${i}`, properties: {} }));
  const files: GraphNode[] = Array.from({ length: 200 }, (_, i) => ({ key: `file:f${i}`, kind: 'File', label: `f${i}`, properties: {} }));
  const docs: GraphNode[] = Array.from({ length: 2 }, (_, i) => ({ key: `doc:d${i}`, kind: 'Doc', label: `d${i}`, properties: {} }));
  const chunks: GraphNode[] = Array.from({ length: 200 }, (_, i) => ({ key: `chunk:c${i}`, kind: 'Chunk', label: `c${i}`, properties: {} }));
  const fileEdges: GraphEdge[] = files.map((file, i) => ({ source: file.key, target: modules[i % modules.length].key, type: 'IN' }));
  const chunkEdges: GraphEdge[] = chunks.map((chunk, i) => ({ source: docs[i % docs.length].key, target: chunk.key, type: 'HAS_CHUNK' }));

  const collapsed = buildDisplayGraph({ nodes: [...modules, ...files, ...docs, ...chunks], edges: [...fileEdges, ...chunkEdges] });
  expect(collapsed.nodes.map(node => node.key).sort()).toEqual([...modules, ...docs].map(node => node.key).sort());
  expect(collapsed.collapsedFiles).toBe(200);
  expect(collapsed.collapsedChunks).toBe(200);
  expect(collapsed.truncated).toBe(false);
  expect(collapsed.links).toHaveLength(0);

  // Without containment data the cap is retained rather than inventing edges.
  const flat: GraphNode[] = Array.from({ length: 300 }, (_, i) => ({ key: `module:m${i}`, kind: 'Module', label: `m${i}`, properties: {} }));
  const capped = buildDisplayGraph({ nodes: flat, edges: [] });
  expect(capped.nodes).toHaveLength(NODE_LIMIT);
  expect(capped.truncated).toBe(true);
  expect(capped.totalNodes).toBe(300);
});

test('canvas resizes and theme changes update token colours', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.goto(`${HARNESS}?case=fixture`);
  await ready(page);

  const dark = hex(await cornerPixel(page));
  expect(dark).toBe('#0b0d0f');
  expect(await hasColor(page, TOKEN_HEX['node-person'].dark, 32)).toBe(true);

  const wide = (await page.locator('[data-graph-stage]').boundingBox())!.width;
  await page.setViewportSize({ width: 760, height: 720 });
  await expect
    .poll(async () => Math.round((await page.locator('[data-graph-stage]').boundingBox())!.width), { timeout: 10_000 })
    .toBeLessThan(Math.round(wide));
  const stage = (await page.locator('[data-graph-stage]').boundingBox())!;
  const canvas = (await page.locator('[data-force-canvas] canvas').boundingBox())!;
  expect(Math.abs(canvas.width - stage.width)).toBeLessThanOrEqual(2);

  await page.evaluate(() => document.documentElement.classList.add('light'));
  await expect.poll(async () => hex(await cornerPixel(page)), { timeout: 10_000 }).toBe('#fbfbfb');
  expect(await hasColor(page, TOKEN_HEX['node-person'].light, 32)).toBe(true);
  expect(await hasColor(page, TOKEN_HEX['node-person'].dark, 8)).toBe(false);

  await page.evaluate(() => document.documentElement.classList.remove('light'));
  await expect.poll(async () => hex(await cornerPixel(page)), { timeout: 10_000 }).toBe('#0b0d0f');
});

test('empty graph is an explicit empty state', async ({ page }) => {
  await page.goto(`${HARNESS}?case=empty`);
  const empty = page.locator('[data-graph-empty]');
  await expect(empty).toBeVisible();
  await expect(empty).toContainText('No graph nodes');
  await expect(page.locator('[data-force-canvas] canvas')).toHaveCount(0);
  await expect(page.locator('[data-graph-legend]')).toHaveCount(0);
});

/**
 * B5 Task 2 — claim ↔ path ↔ node selection.
 *
 * Selection membership is asserted through the pure helpers (exact segment ids,
 * trace-supported absence) and through the DOM sets the wrapper publishes, then
 * confirmed against canvas pixels, because DOM state alone does not prove that
 * the renderer painted the evidence state.
 */

async function evidenceAttr(page: Page, name: string): Promise<string[]> {
  const raw = await page.locator('.graph-canvas').getAttribute(name);
  return raw ? (JSON.parse(raw) as string[]) : [];
}

test('claim chip selects all its claim citations', async ({ page }) => {
  await page.goto('/t/falkordb');
  const claim = triage.labels[0];
  const row = page.locator('[data-claim-section="labels"] li').filter({ hasText: claim.text });
  const chips = row.getByRole('button', { name: /^Show evidence:/ });
  await expect(chips).toHaveCount(claim.citations.length);

  await chips.first().click();
  for (const key of claim.citations) {
    await expect(row.getByRole('button', { name: `Show evidence: ${key}`, exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.canvas-selection')).toContainText(key);
  }
  // A sibling claim that shares no citation is left unselected.
  const sibling = page.locator('[data-claim-section="labels"] li').filter({ hasText: triage.labels[1].text });
  await expect(sibling.getByRole('button', { name: /^Show evidence:/ })).toHaveAttribute('aria-pressed', 'false');
});

test('parallel nonpath edge stays dim', async ({ page }) => {
  const from = 'issue:x';
  const to = 'module:y';
  const edges: GraphEdge[] = [
    { source: from, target: to, type: 'TOUCHES' },
    { source: to, target: from, type: 'ABOUT' },
  ];
  const paths: PathData[] = [{ keys: [from, to], rels: ['TOUCHES'] }];
  const ids = pathEdgeIds(paths, edges);
  // Reverse traversal resolves to the stored edge, and only the matching
  // relationship type is highlighted.
  expect([...ids]).toEqual([segmentId(edges[0])]);
  expect(ids.has(segmentId(edges[1]))).toBe(false);

  await page.goto(`${HARNESS}?case=parallel`);
  await ready(page);
  expect(await evidenceAttr(page, 'data-evidence-edges')).toEqual([segmentId(edges[0])]);
  expect(await evidenceAttr(page, 'data-evidence-nodes')).toEqual([from, to].sort());
  expect(await hasColor(page, '#f2b54a', 24), 'evidence edge painted').toBe(true);
});

test('node selection lights every matching chip and trace row', async ({ page }) => {
  await page.goto('/t/falkordb');
  const key = 'person:falkordb-maintainer-a';
  await page.locator('.graph-node-controls summary').click();
  await page.locator('[data-graph-node-list] button').filter({ hasText: '@falkordb-maintainer-a' }).click();

  await expect(page.getByRole('button', { name: `Show evidence: ${key}`, exact: true }).first()).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-trace-row]').first()).toHaveAttribute('data-supporting', 'true');
  await expect(page.locator('.canvas-selection')).toContainText(key);
  expect(await evidenceAttr(page, 'data-evidence-nodes')).toEqual([key]);
  expect(await evidenceAttr(page, 'data-evidence-edges')).toEqual([]);
});

test('late response cannot replace newer selection', async () => {
  const guard = createSelectionGuard();
  let release!: (value: string) => void;
  const slow = new Promise<string>(resolve => { release = resolve; });
  const first = guard.run(async () => { await slow; return 'slow'; });
  const second = guard.run(async () => 'fast');
  await expect(second).resolves.toBe('fast');
  release('slow');
  await expect(first).resolves.toBeNull();
});

test('unreachable citation does not create a path', async () => {
  const originalFetch = globalThis.fetch;
  const originalBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  process.env.NEXT_PUBLIC_API_BASE_URL = 'http://127.0.0.1:9';
  globalThis.fetch = (async (input: unknown) => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : (input as Request).url;
    const url = new URL(raw);
    const from = url.searchParams.get('from') ?? '';
    return new Response(
      JSON.stringify({
        tool: 'explain_path',
        data: { keys: [from], rels: [] },
        cypher: ['explain_path.cypher'],
        params: { from, to: url.searchParams.get('to') },
        evidence: [from],
        ms: 1,
        empty: true,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }) as typeof fetch;
  try {
    const claim: Claim = { text: 'unreachable', citations: ['module:missing'] };
    const selection = await resolveClaimEvidence('t', 'issue:x', claim, new AbortController().signal);
    expect(selection.state).toBe('ready');
    expect(selection.paths).toEqual([]);
    expect(selection.nodeKeys).toEqual([]);
    // Both endpoints may exist in the graph, but an empty result never invents an edge.
    const edges: GraphEdge[] = [{ source: 'issue:x', target: 'module:missing', type: 'ABOUT' }];
    expect(pathEdgeIds(selection.paths, edges).size).toBe(0);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
    else process.env.NEXT_PUBLIC_API_BASE_URL = originalBase;
  }
});

test('empty tool result shows module absence', async ({ page }) => {
  const module = 'module:y';
  const emptyTool: ToolResult = {
    tool: 'bus_factor',
    data: [],
    cypher: ['bus_factor.cypher'],
    params: { tenant: 't', module },
    evidence: [module],
    ms: 4,
    empty: true,
  };
  const present = new Set(['module:y', 'module:z']);
  expect(supportedAbsenceKeys([emptyTool], [module], present)).toEqual([module]);
  // Arbitrary prose (a non-empty result) is never classified as an absence.
  expect(supportedAbsenceKeys([{ ...emptyTool, empty: false }], [module], present)).toEqual([]);
  // A tool that does not reference the citation cannot classify it.
  expect(supportedAbsenceKeys([emptyTool], ['module:z'], present)).toEqual([]);

  await page.goto(`${HARNESS}?case=negative`);
  await ready(page);
  await expect(page.locator('.canvas-selection')).toContainText('No path found');
  expect(await evidenceAttr(page, 'data-evidence-risk')).toEqual([module]);
  expect(await hasColor(page, '#f2836b', 24), 'risk ring painted').toBe(true);
});

test('missing graph node shows an explicit notice', async ({ page }) => {
  await page.goto(`${HARNESS}?case=missing`);
  await ready(page);
  await expect(page.locator('[data-missing-evidence]')).toContainText('module:z');
  expect(await evidenceAttr(page, 'data-evidence-nodes')).toEqual(['issue:x', 'module:y']);
  expect(await evidenceAttr(page, 'data-evidence-edges')).toEqual(['ABOUT:issue:x->module:y']);
});

test('selection clears with Escape and the clear button', async ({ page }) => {
  await page.goto('/t/falkordb');
  const key = triage.labels[0].citations[0];
  const chip = page.getByRole('button', { name: `Show evidence: ${key}`, exact: true }).first();
  await chip.click();
  await expect(page.locator('.canvas-selection')).toContainText(key);

  // Escape clears without trapping focus: the chip keeps focus.
  await page.keyboard.press('Escape');
  await expect(page.locator('.canvas-selection')).toContainText('Select a citation');
  await expect(chip).toHaveAttribute('aria-pressed', 'false');
  await expect(chip).toBeFocused();

  await chip.click();
  await page.getByRole('button', { name: 'Clear selection' }).click();
  await expect(page.locator('.canvas-selection')).toContainText('Select a citation');
});

test('legend lists visible kinds only', async ({ page }) => {
  await page.goto(`${HARNESS}?case=fixture`);
  await ready(page);
  const legendKinds = await page.locator('[data-graph-legend] [data-kind]').evaluateAll(els => els.map(e => e.getAttribute('data-kind')));
  // Fixture graph has exactly the kinds actually on screen: no phantom kinds, no Evidence without a selection.
  expect(legendKinds.sort()).toEqual(['Decision', 'Doc', 'Issue', 'Label', 'Module', 'Person', 'Repo'].sort());
  expect(legendKinds).not.toContain('Evidence');
  // Selected path adds the amber Evidence row.
  await page.goto(`${HARNESS}?case=hover`);
  await ready(page);
  await expect(page.locator('[data-graph-legend] [data-kind="Evidence"]')).toHaveCount(1);
});

test('keyboard node list selects evidence', async ({ page }) => {
  await page.goto('/t/falkordb');
  await page.locator('.graph-node-controls summary').click();
  const btn = page.locator('[data-graph-node-list] button').first();
  await btn.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.canvas-selection')).toContainText(await btn.textContent() ?? '');
});

test('phone tap exposes node details', async ({ page }) => {
  for (const width of [390, 800, 1440]) {
    await page.setViewportSize({ width, height: 720 });
    await page.goto(`${HARNESS}?case=hover`);
    await ready(page);
    await page.locator('.graph-node-controls summary').click();
    await page.locator('[data-graph-node-list] button').last().click();
    await expect(page.locator('[data-graph-hover-card]')).toBeVisible();
    const cardBox = (await page.locator('[data-graph-hover-card]').boundingBox())!;
    expect(cardBox.x + cardBox.width).toBeLessThanOrEqual(width + 2);
    await expect(page.locator('[data-node-label]')).toContainText('w');
  }
});

test('hover only emphasizes direct neighbours without overriding selected paths', async ({ page }) => {
  await page.goto(`${HARNESS}?case=hover`);
  await ready(page);
  await page.waitForFunction(() => !!(window as any).__graphNodeAlpha);
  const alpha = () => page.evaluate(() => (window as any).__graphNodeAlpha || {});

  // on-path node stays vivid (not dimmed to 0.5) while off-path dimmed node goes to 0.5
  const onPathAlpha = (await alpha())['issue:x'];
  expect(onPathAlpha).toBe(1);

  // Trigger hover of an off-path node via the controlled harness initial state already hovers module:z.
  const offPathAlpha = (await alpha())['module:w'];
  // The non-neighbour, non-path node should be dimmed by hover.
  expect(offPathAlpha).toBeLessThanOrEqual(0.55);
});

test('reduced motion has no path reveal delay', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${HARNESS}?case=parallel`);
  await ready(page);
  await expect.poll(async () => page.locator('.graph-canvas').getAttribute('data-path-reveal')).toBe('1.00');
});
