/**
 * Deterministic, read-only fake graph API for the API-mode Playwright suite.
 *
 * Server-side Next requests cannot be intercepted with `page.route`, so the
 * API-mode config points the Next dev server at this Node HTTP server on a
 * separate local port. Tests mutate `state` (outage/ok, request log) in the
 * Playwright worker process; the server never writes anything.
 */
import { createServer, type Server, type ServerResponse } from 'node:http';

/** Must match `NEXT_PUBLIC_API_BASE_URL` in `playwright.api.config.ts`. */
export const FAKE_API_PORT = 4321;

export type FakeScenario = 'ok' | 'outage';

export interface FakeApiState {
  scenario: FakeScenario;
  /** `forbidden` mirrors the API refusing a live write (403). */
  applyScenario: 'ok' | 'fail' | 'forbidden';
  lastKeys: string[] | null;
  requests: string[];
  applyRequests: Array<{ tenant: string; run: string; body: unknown }>;
}

export interface FakeApi {
  server: Server;
  state: FakeApiState;
}

interface StatsBody {
  node_counts: Record<string, number>;
  edge_counts: Record<string, number>;
  last_ingest_at: number | null;
  proof: null;
  latencies: unknown[];
}

const ACME_STATS: StatsBody = {
  node_counts: { Issue: 7, Repo: 2, Person: 3 },
  edge_counts: { AUTHORED: 4, FIXES: 1 },
  last_ingest_at: 1760000000,
  proof: null,
  latencies: [],
};

const TENANTS = [
  { slug: 'acme', name: 'Acme' },
  { slug: 'falkordb', name: 'FalkorDB' },
];

/**
 * The one persisted run the fake recall route knows (`apply.spec.ts` uses the
 * same UUID as its apply target). Any other UUID under any tenant is 404, so the
 * deep link's unknown/wrong-tenant path stays honest.
 */
export const KNOWN_RUN = '11111111-2222-4333-8444-555555555555';
/** The issue the known run belongs to; also the run deep link's graph anchor. */
export const KNOWN_RUN_ISSUE = 'issue:Acme/Widgets#42';
/** Every evidence key cited by the known run's triage and trace. */
export const KNOWN_RUN_EVIDENCE = [
  'repo:Acme/Widgets',
  'doc:Acme/Widgets/README.md',
  'module:Acme/Widgets/src/widget',
  KNOWN_RUN_ISSUE,
  'label:Acme/Widgets/graph',
  'person:acme-maintainer-a',
  'person:acme-maintainer-b',
];

function send(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function buildSubgraph(keys: string[]): { nodes: unknown[]; edges: unknown[] } {
  const nodes: unknown[] = [];
  const edges: unknown[] = [];
  const seen = new Set<string>();
  for (const key of keys) {
    if (seen.has(key)) continue;
    seen.add(key);
    nodes.push({ key, kind: 'Issue', label: key, properties: {} });
    const text = key.toLowerCase();
    const neighbours = text.includes('fast') ? 4 : text.includes('slow') ? 1 : 2;
    for (let index = 0; index < neighbours; index += 1) {
      const neighbour = `${key}::module:${index}`;
      if (seen.has(neighbour)) continue;
      seen.add(neighbour);
      nodes.push({ key: neighbour, kind: 'Module', label: `module-${index}`, properties: {} });
      edges.push({ source: key, target: neighbour, type: 'ABOUT' });
    }
  }
  return { nodes, edges };
}

/** A real-shaped `ToolResult<RecallRunData>` body for the known run. */
function buildRecallRun(tenant: string): unknown {
  const runKey = `run:${KNOWN_RUN}`;
  return {
    tool: 'recall_run',
    data: {
      run: {
        run_key: runKey,
        at: 1760400000,
        issue_key: KNOWN_RUN_ISSUE,
        summary: 'Persisted run: triaged Acme/Widgets#42 from graph evidence.',
        fallback: false,
        model: 'fake-model-v0',
        llm_calls: 3,
        ms: 4321,
      },
      evidence: [...KNOWN_RUN_EVIDENCE],
      actions: [
        {
          action_key: 'action:acme-assign-1',
          kind: 'assign',
          value: 'acme-maintainer-a',
          module: 'module:Acme/Widgets/src/widget',
          status: 'applied',
          note: 'Assigned through the sandbox webhook.',
          feedback: [],
        },
        {
          action_key: 'action:acme-label-1',
          kind: 'label',
          value: 'graph',
          module: null,
          status: 'proposed',
          note: null,
          feedback: [],
        },
      ],
      triage: {
        run_key: runKey,
        labels: [
          { text: 'graph', citations: ['label:Acme/Widgets/graph', 'doc:Acme/Widgets/README.md'] },
          { text: 'bug', citations: [KNOWN_RUN_ISSUE] },
        ],
        reviewers: [
          { text: '@acme-maintainer-a', citations: ['person:acme-maintainer-a', 'module:Acme/Widgets/src/widget'] },
          { text: '@acme-maintainer-b', citations: ['person:acme-maintainer-b', 'module:Acme/Widgets/src/widget'] },
        ],
        risks: [
          { text: 'Persisted recall risk: validate reviewer load before applying.', citations: ['module:Acme/Widgets/src/widget'] },
        ],
        decisions: [
          { text: 'Persisted recall decision: reuse graph evidence for this issue.', citations: ['doc:Acme/Widgets/README.md'] },
        ],
        comment_markdown: 'Persisted run summary for Acme/Widgets#42.',
        trace: [
          {
            tool: 'affected_modules',
            data: { modules: [{ module: 'module:Acme/Widgets/src/widget', path: 'src/widget', evidence: 1 }] },
            cypher: ['affected_modules.cypher'],
            params: { tenant, issue: KNOWN_RUN_ISSUE },
            evidence: [...KNOWN_RUN_EVIDENCE],
            ms: 21,
            empty: false,
          },
        ],
      },
    },
    cypher: ['recall_run.cypher'],
    params: { tenant, run: runKey },
    evidence: [...KNOWN_RUN_EVIDENCE],
    ms: 7,
    empty: false,
  };
}

export function createFakeApi(): FakeApi {
  const state: FakeApiState = { scenario: 'ok', applyScenario: 'ok', lastKeys: null, requests: [], applyRequests: [] };
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', `http://127.0.0.1:${FAKE_API_PORT}`);
    state.requests.push(`${req.method} ${url.pathname}`);
    if (req.method === 'OPTIONS') {
      res.writeHead(204, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type,Accept',
      });
      res.end();
      return;
    }
    if (req.method === 'POST') {
      const match = /^\/tenants\/([^/]+)\/runs\/([^/]+)\/apply$/.exec(url.pathname);
      if (match) {
        const tenant = decodeURIComponent(match[1]);
        const run = decodeURIComponent(match[2]);
        let body = '';
        req.setEncoding('utf8');
        req.on('data', (chunk) => { body += chunk; });
        req.on('end', () => {
          let parsed: unknown;
          try { parsed = body ? JSON.parse(body) : {}; } catch { parsed = {}; }
          state.applyRequests.push({ tenant, run, body: parsed });
          if (state.applyScenario === 'fail') {
            send(res, 500, { detail: 'Apply failed' });
            return;
          }
          if (state.applyScenario === 'forbidden') {
            send(res, 403, { detail: 'live writes happen only through the webhook' });
            return;
          }
          setTimeout(() => {
            send(res, 200, {
              run_key: `run:${run}`,
              dry_run: (parsed as any)?.dry_run !== false,
              actions: [
                { action_key: 'action:1', kind: 'label', value: 'graph', status: 'dry_run', note: 'Skipped (dry run)' },
              ],
            });
          }, 200);
        });
        return;
      }
      send(res, 405, { detail: 'read-only test API' });
      return;
    }
    if (req.method !== 'GET') {
      send(res, 405, { detail: 'read-only test API' });
      return;
    }
    if (state.scenario === 'outage') {
      send(res, 503, { detail: 'Live graph reader unavailable' });
      return;
    }
    if (url.pathname === '/tenants') {
      send(res, 200, { tenants: TENANTS });
      return;
    }
    // Recall one persisted run by canonical UUID path (`/tenants/{t}/runs/{uuid}`).
    const runMatch = /^\/tenants\/([^/]+)\/runs\/([^/]+)$/.exec(url.pathname);
    if (runMatch) {
      const tenant = decodeURIComponent(runMatch[1]);
      const uuid = decodeURIComponent(runMatch[2]);
      if (!TENANTS.some((candidate) => candidate.slug === tenant)) {
        send(res, 404, { detail: 'Unknown tenant' });
        return;
      }
      if (tenant !== 'acme' || uuid !== KNOWN_RUN) {
        send(res, 404, { detail: 'Unknown run' });
        return;
      }
      send(res, 200, buildRecallRun(tenant));
      return;
    }
    const match = /^\/tenants\/([^/]+)\/(stats|subgraph|path)$/.exec(url.pathname);
    if (!match) {
      send(res, 404, { detail: 'Not found' });
      return;
    }
    const tenant = decodeURIComponent(match[1]);
    const route = match[2];
    if (!TENANTS.some((candidate) => candidate.slug === tenant)) {
      send(res, 404, { detail: 'Unknown tenant' });
      return;
    }
    if (route === 'stats') {
      send(res, 200, ACME_STATS);
      return;
    }
    if (route === 'path') {
      const from = url.searchParams.get('from') ?? '';
      const to = url.searchParams.get('to') ?? '';
      const linked = Boolean(from) && Boolean(to) && from !== to;
      send(res, 200, {
        tool: 'explain_path',
        data: { keys: linked ? [from, to] : [from].filter(Boolean), rels: linked ? ['ABOUT'] : [] },
        cypher: ['explain_path.cypher'],
        params: { tenant, from, to },
        evidence: [from, to].filter(Boolean),
        ms: 1,
        empty: !linked,
      });
      return;
    }
    const raw = url.searchParams.get('keys') ?? '';
    let keys: string[];
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed) || parsed.some((key) => typeof key !== 'string')) throw new Error('bad keys');
      keys = parsed as string[];
    } catch {
      send(res, 422, { detail: 'keys must be a JSON array of strings' });
      return;
    }
    state.lastKeys = keys;
    const body = buildSubgraph(keys);
    if (keys.some((key) => key.toLowerCase().includes('slow'))) setTimeout(() => send(res, 200, body), 1200);
    else send(res, 200, body);
  });
  return { server, state };
}

export function startFakeApi(api: FakeApi, port = FAKE_API_PORT): Promise<void> {
  return new Promise((resolve, reject) => {
    api.server.once('error', reject);
    api.server.listen(port, '127.0.0.1', () => resolve());
  });
}

export function stopFakeApi(api: FakeApi): Promise<void> {
  return new Promise((resolve) => api.server.close(() => resolve()));
}
