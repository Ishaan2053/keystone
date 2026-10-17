/**
 * Deterministic, read-only fake graph API for the API-mode Playwright suite.
 *
 * Server-side Next requests cannot be intercepted with `page.route`, so the
 * API-mode config points the Next dev server at this Node HTTP server on a
 * separate local port. Tests mutate `state` (outage/ok, request log) in the
 * Playwright worker process; the server never writes anything.
 */
import { createServer, type Server, type ServerResponse } from 'node:http';
import type { RecallRunData, RunSummary, Stats, ToolResult } from '../lib/types';

/** Must match `NEXT_PUBLIC_API_BASE_URL` in `playwright.api.config.ts`. */
export const FAKE_API_PORT = 4321;

export type FakeScenario = 'ok' | 'outage';

export interface FakeApiState {
  stats: Record<string, Stats>;
  statsOutage: boolean;
  runs: Record<string, RunSummary[]>;
  recalls: Record<string, Record<string, ToolResult<RecallRunData>>>;
  delays: Record<string, number>;
  listOutage: boolean;
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

const ACME_STATS: Stats = {
  node_counts: { Issue: 7, Repo: 2, Person: 3 },
  edge_counts: { AUTHORED: 4, FIXES: 1 },
  last_ingest_at: 1760000000,
  proof: null,
  latencies: [],
  unavailable: [],
};

const TENANTS = [
  { slug: 'acme', name: 'Acme' },
  { slug: 'falkordb', name: 'FalkorDB' },
  { slug: 'pallets', name: 'Pallets' },
];

/** The one Flask issue in the Pallets graph (B9 second tenant). */
export const PALLET_ISSUE = 'issue:pallets/flask#500';
/** Pallets stats, deliberately distinct from Acme's; `Issue: 3` is asserted by the switch test. */
const PALLET_STATS: Stats = {
  node_counts: { Issue: 3, Repo: 1, Person: 2 },
  edge_counts: { AUTHORED: 2, REVIEWED: 1 },
  last_ingest_at: 1760000100,
  proof: null,
  latencies: [],
  unavailable: [],
};

/**
 * The one persisted run the fake recall route knows (`apply.spec.ts` uses the
 * same UUID as its apply target). Any other UUID under any tenant is 404, so the
 * deep link's unknown/wrong-tenant path stays honest.
 */
export const KNOWN_RUN = '11111111-2222-4333-8444-555555555555';
/**
 * A run the recall route answers with HTTP 200 and `ToolResult(data=None,
 * empty=True)`, as the `recall_run` tool does when a run is missing or its
 * stored triage is invalid. The deep link must show run-not-found, not crash.
 */
export const EMPTY_RUN = '66666666-7777-4888-8999-000000000000';
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
export function buildRecallRun(tenant: string): ToolResult<RecallRunData> {
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
  const state: FakeApiState = { stats: {}, statsOutage: false, runs: {}, recalls: {}, delays: {}, listOutage: false, scenario: 'ok', applyScenario: 'ok', lastKeys: null, requests: [], applyRequests: [] };
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
    const listMatch = /^\/tenants\/([^/]+)\/runs$/.exec(url.pathname);
    if (listMatch) {
      const tenant = decodeURIComponent(listMatch[1]);
      const body = { runs: structuredClone(state.runs[tenant] ?? []) };
      const outage = state.listOutage;
      setTimeout(() => send(res, outage ? 503 : 200, outage ? { detail: 'Run list unavailable' } : body), state.delays[url.pathname] ?? 0);
      return;
    }
    // Recall one persisted run by canonical UUID path (`/tenants/{t}/runs/{uuid}`).
    const runMatch = /^\/tenants\/([^/]+)\/runs\/([^/]+)$/.exec(url.pathname);
    if (runMatch) {
      const tenant = decodeURIComponent(runMatch[1]);
      const uuid = decodeURIComponent(runMatch[2]);
      const recalled = state.recalls[tenant]?.[uuid];
      if (recalled) {
        const body = structuredClone(recalled);
        setTimeout(() => send(res, 200, body), state.delays[url.pathname] ?? 0);
        return;
      }
      if (!TENANTS.some((candidate) => candidate.slug === tenant)) {
        send(res, 404, { detail: 'Unknown tenant' });
        return;
      }
      if (tenant === 'acme' && uuid === EMPTY_RUN) {
        send(res, 200, {
          tool: 'recall_run',
          data: null,
          cypher: ['recall_run.cypher'],
          params: { tenant, run: `run:${uuid}` },
          evidence: [],
          ms: 1,
          empty: true,
        });
        return;
      }
      // B9: Pallets exposes no persisted runs — only Acme's known run recalls.
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
      const body = structuredClone(state.stats[tenant] ?? (tenant === 'pallets' ? PALLET_STATS : ACME_STATS));
      const outage = state.statsOutage;
      setTimeout(() => send(res, outage ? 503 : 200, outage ? { detail: 'Stats unavailable' } : body), state.delays[url.pathname] ?? 0);
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
