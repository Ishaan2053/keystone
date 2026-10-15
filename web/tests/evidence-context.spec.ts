import { test, expect } from '@playwright/test';
import { createSelectionGuard, resolveClaimContext } from '../lib/evidence-selection';
import type { GraphNode, PathData, Subgraph, ToolResult } from '../lib/types';

const issue = 'issue:demo#1';
const pr = 'pr:demo#2';
const person = 'person:reviewer';
const path: PathData = { keys: [issue, pr, person], rels: ['FIXES', 'AUTHORED'] };
const node = (key: string, kind: GraphNode['kind']): GraphNode => ({ key, kind, label: key, properties: {} });
const complete: Subgraph = {
  nodes: [node(issue, 'Issue'), node(pr, 'PR'), node(person, 'Person')],
  // FIXES is traversed against the stored direction.
  edges: [{ source: pr, target: issue, type: 'FIXES' }, { source: pr, target: person, type: 'AUTHORED' }],
};
const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_BASE_URL;
let requests: URL[];

test.beforeEach(() => {
  requests = [];
  process.env.NEXT_PUBLIC_API_BASE_URL = 'https://evidence.invalid';
});
test.afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = originalBase;
});

function api(returnedPath = path, extra: Subgraph = complete, failContext = false) {
  globalThis.fetch = async input => {
    const url = new URL(String(input));
    requests.push(url);
    if (url.pathname.endsWith('/path')) {
      const data = url.searchParams.get('to') === person ? returnedPath : { keys: [], rels: [] };
      return Response.json({ tool: 'path', data, cypher: [], params: {}, evidence: data.keys, ms: 0, empty: !data.keys.length });
    }
    if (url.pathname.endsWith('/subgraph')) return Response.json(extra, { status: failContext ? 503 : 200 });
    throw new Error(`Unexpected request: ${url}`);
  };
}

function resolve(base: Subgraph, signal = new AbortController().signal, citations = [person]) {
  return resolveClaimContext('demo', issue, { text: 'reviewer evidence', citations }, 'reviewers:0', () => base, [], signal);
}

for (const [name, base] of [
  ['intermediate node', { nodes: [complete.nodes[0], complete.nodes[2]], edges: [] }],
  ['typed relationship segment', { nodes: complete.nodes, edges: [complete.edges[0], { source: pr, target: person, type: 'REVIEWED' }] }],
] satisfies [string, Subgraph][]) {
  test(`fetches missing ${name} even when citation endpoints exist`, async () => {
    api();
    const result = await resolve(base);
    const contexts = requests.filter(url => url.pathname.endsWith('/subgraph'));
    expect(contexts).toHaveLength(1);
    expect(JSON.parse(contexts[0].searchParams.get('keys')!)).toEqual([issue, pr, person]);
    expect(result.graph.nodes.map(n => n.key)).toEqual([issue, pr, person]);
    expect(result.graph.edges).toEqual(expect.arrayContaining(complete.edges));
    expect(result.next.paths).toEqual([path]);
    expect(result.next.missingKeys).toEqual([]);
  });
}

test('complete context including reverse stored edges avoids a context fetch', async () => {
  api();
  const result = await resolve(complete);
  expect(requests.map(url => url.pathname)).toEqual(['/tenants/demo/path']);
  expect(result.graph).toEqual(complete);
  expect(result.next.paths).toEqual([path]);
});

test('unreachable paths stay empty even when context contains a connection', async () => {
  api({ keys: [], rels: [] });
  const result = await resolve({ nodes: [complete.nodes[0]], edges: [] });
  expect(result.next.paths).toEqual([]);
  expect(result.next.nodeKeys).toEqual([]);
  expect(result.graph.nodes.map(n => n.key)).toContain(person);
});

test('empty paths with present citations do not trigger context fetching', async () => {
  api({ keys: [], rels: [] });
  const result = await resolve(complete);
  expect(requests).toHaveLength(1);
  expect(result.next.paths).toEqual([]);
});

test('context seeds include citations outside the returned path without inventing paths', async () => {
  api();
  const result = await resolve({ nodes: [], edges: [] }, undefined, [person, 'module:missing']);
  const context = requests.find(url => url.pathname.endsWith('/subgraph'))!;
  expect(JSON.parse(context.searchParams.get('keys')!)).toEqual([issue, pr, person, 'module:missing']);
  expect(result.next.missingKeys).toEqual(['module:missing']);
  expect(result.next.paths).toEqual([path]);
});

test('failed context fetch preserves API paths and reports missing intermediates', async () => {
  api(path, complete, true);
  const base = { nodes: [complete.nodes[0], complete.nodes[2]], edges: [] };
  const result = await resolve(base);
  expect(result.graph).toEqual(base);
  expect(result.next.paths).toEqual([path]);
  expect(result.next.missingKeys).toEqual([pr]);
});

test('incomplete context never fabricates a returned relationship segment', async () => {
  const incomplete = { nodes: complete.nodes, edges: [complete.edges[0]] };
  api(path, incomplete);
  const result = await resolve(incomplete);
  expect(requests.filter(url => url.pathname.endsWith('/subgraph'))).toHaveLength(1);
  expect(result.graph.edges).toEqual([complete.edges[0]]);
  expect(result.next.paths).toEqual([path]);
});

test('an aborted path response cannot initiate context loading or return a selection', async () => {
  api();
  const controller = new AbortController();
  const immediateFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const response = await immediateFetch(input, init);
    controller.abort();
    return response;
  };
  await expect(resolve({ nodes: [], edges: [] }, controller.signal)).rejects.toThrow();
  expect(requests.map(url => url.pathname)).toEqual(['/tenants/demo/path']);
});

test('context merging caps nodes, closes edges and reports evidence dropped by the cap', async () => {
  const citations = Array.from({ length: 151 }, (_, i) => `person:${i}`);
  api({ keys: [], rels: [] }, {
    nodes: [complete.nodes[0], ...citations.map(key => node(key, 'Person'))],
    edges: citations.map(target => ({ source: issue, target, type: 'MENTIONS' })),
  });
  const result = await resolve({ nodes: [], edges: [] }, undefined, citations);
  expect(result.graph.nodes).toHaveLength(150);
  const keys = new Set(result.graph.nodes.map(n => n.key));
  expect(result.graph.edges.every(edge => keys.has(edge.source) && keys.has(edge.target))).toBe(true);
  expect(result.next.missingKeys).toEqual(['person:149', 'person:150']);
  expect(result.next.paths).toEqual([]);
});

for (const action of ['supersede', 'cancel'] as const) {
  test(`${action} prevents a late context response from applying`, async () => {
    api();
    const immediateFetch = globalThis.fetch;
    let release!: () => void;
    const delayed = new Promise<void>(done => { release = done; });
    let started!: () => void;
    const contextStarted = new Promise<void>(done => { started = done; });
    let contextSignal: AbortSignal | undefined;
    globalThis.fetch = async (input, init) => {
      if (String(input).includes('/subgraph?')) {
        contextSignal = init?.signal ?? undefined;
        started();
        await delayed; // Deliberately ignores abort, like a late transport response.
      }
      return immediateFetch(input, init);
    };
    const guard = createSelectionGuard();
    const pending = guard.run(signal => resolve({ nodes: [], edges: [] }, signal));
    await contextStarted;
    if (action === 'cancel') guard.cancel();
    else expect(await guard.run(signal => resolve(complete, signal))).not.toBeNull();
    expect(contextSignal?.aborted).toBe(true);
    release();
    expect(await pending).toBeNull();
  });
}

/**
 * Fix 10: one `/path` request fails (503) while another resolves. The resolved
 * path survives, the failed citation is tracked as failed (not "no path"), and a
 * trace entry that would otherwise ring it as absent does not.
 */
test('partial path failure keeps resolved paths and marks the failed key unavailable', async () => {
  const down = 'module:down';
  const withDown: Subgraph = { nodes: [...complete.nodes, node(down, 'Module')], edges: complete.edges };
  globalThis.fetch = async input => {
    const url = new URL(String(input));
    requests.push(url);
    if (url.pathname.endsWith('/path')) {
      if (url.searchParams.get('to') === down) return Response.json({ detail: 'unavailable' }, { status: 503 });
      return Response.json({ tool: 'path', data: path, cypher: [], params: {}, evidence: path.keys, ms: 0, empty: false });
    }
    throw new Error(`Unexpected request: ${url}`);
  };
  const emptyTool: ToolResult = { tool: 'bus_factor', data: [], cypher: [], params: { module: down }, evidence: [down], ms: 0, empty: true };
  const result = await resolveClaimContext('demo', issue, { text: 'mixed', citations: [person, down] }, 'risks:0', () => withDown, [emptyTool], new AbortController().signal);
  expect(result.next.state).toBe('ready');
  expect(result.next.paths).toEqual([path]);
  expect(result.next.nodeKeys).toEqual(path.keys);
  expect(result.next.failedKeys).toEqual([down]);
  expect(result.next.noPathKeys).toEqual([]);
  // A failure is not a negative result: no dashed risk ring for it.
  expect(result.next.riskKeys).toEqual([]);
});

test('every path request failing is an error state, not "no path"', async () => {
  globalThis.fetch = async input => {
    requests.push(new URL(String(input)));
    return Response.json({ detail: 'unavailable' }, { status: 503 });
  };
  const result = await resolve(complete);
  expect(result.next.state).toBe('error');
  expect(result.next.paths).toEqual([]);
  expect(result.next.failedKeys).toEqual([person]);
  expect(result.next.noPathKeys).toEqual([]);
});

test('an empty path result is recorded as no path, not as a failure', async () => {
  api({ keys: [], rels: [] });
  const result = await resolve(complete);
  expect(result.next.state).toBe('ready');
  expect(result.next.noPathKeys).toEqual([person]);
  expect(result.next.failedKeys).toEqual([]);
});
