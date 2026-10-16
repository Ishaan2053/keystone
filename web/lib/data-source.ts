import 'server-only';
import { notFound } from 'next/navigation';
import { ApiError, getRun, getStats, getSubgraph, getTenants, runKeyUuid } from './api-client';
import { dataSource } from './data-source-config';
import * as fixtureData from './fixture-data';
import type { RecallRunData, Stats, Subgraph, TenantSummary, ToolResult } from './types';
import type { LiveWorkspaceData, WorkspaceData } from './workspace-data';

/**
 * Server-side data selection for the workspace (B4 Task 2).
 *
 * The fixture branch delegates to `fixture-data.ts` unchanged. The live branch
 * reads `/tenants`, `/stats` and `/subgraph` over HTTP, always uncached, and
 * never substitutes fixture data: a live failure is an explicit error state.
 *
 * Both branches keep the same public signatures so pages stay mode-agnostic:
 *   loadTenants(): Promise<TenantSummary[]>
 *   loadWorkspaceData(tenant, issueKey, runKey?): Promise<WorkspaceData | null>
 *
 * A live `runKey` (B7 Task 3) takes precedence over `issueKey` only when it is a
 * well-formed UUID that the recall route resolves to the same identity; an
 * unknown or malformed run is an explicit error, never an issue or fixture view.
 */
export { dataSource } from './data-source-config';
export type { DataSource } from './data-source-config';

const LIVE_ERROR = 'Live data unavailable';
const RUN_INVALID = 'Run link is not a valid run id.';
const RUN_NOT_FOUND = 'Run not found for this tenant.';
const RUN_MISMATCH = 'Run link does not match the returned run.';

export async function loadTenants(): Promise<TenantSummary[]> {
  if (dataSource() === 'fixtures') return fixtureData.loadTenants();
  // `apiGet` reads with `cache: 'no-store'`, which opts the route into dynamic
  // rendering, so a live tenant list is never captured by a build or data cache.
  return getTenants();
}

export async function loadWorkspaceData(
  tenant: string,
  issueKey: string | null,
  runKey: string | null = null,
): Promise<WorkspaceData | null> {
  if (dataSource() === 'fixtures') {
    const fixture = await fixtureData.loadWorkspaceData(tenant);
    return fixture ? { ...fixture, source: 'fixture', issueKey: null } : null;
  }

  let tenants: TenantSummary[];
  try {
    tenants = await getTenants();
  } catch {
    return liveError(tenant, issueKey, [], LIVE_ERROR);
  }

  // Unknown tenants are a 404, never a fixture fallback or an empty live page.
  if (!tenants.some((candidate) => candidate.slug === tenant)) notFound();

  if (runKey !== null) return loadRunWorkspace(tenant, issueKey, runKey, tenants);

  let stats: Stats;
  let subgraph: Subgraph = { nodes: [], edges: [] };
  try {
    stats = await getStats(tenant);
    if (issueKey) subgraph = await getSubgraph(tenant, [issueKey]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return liveError(tenant, issueKey, tenants, LIVE_ERROR);
  }

  return {
    source: 'live',
    tenant,
    tenants,
    issueKey,
    runKey: null,
    run: null,
    triage: null,
    subgraph,
    stats,
    applyPreview: null,
    applyRefused: null,
    error: null,
  };
}

/** Resolve one persisted run and its issue neighbourhood; never fall back to issue or fixtures. */
async function loadRunWorkspace(
  tenant: string,
  issueKey: string | null,
  runParam: string,
  tenants: TenantSummary[],
): Promise<LiveWorkspaceData> {
  const uuid = runKeyUuid(runParam);
  if (!uuid) return liveError(tenant, issueKey, tenants, RUN_INVALID);

  let recall: ToolResult<RecallRunData>;
  try {
    recall = await getRun(tenant, uuid);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return liveError(tenant, issueKey, tenants, RUN_NOT_FOUND);
    return liveError(tenant, issueKey, tenants, LIVE_ERROR);
  }

  // `recall_run` answers a missing run, or one whose stored triage is invalid,
  // with HTTP 200 and `empty: true, data: null`: that is the same not-found state.
  const run = recall?.empty === true ? null : recall?.data;
  if (!run?.run || !run.triage) return liveError(tenant, issueKey, tenants, RUN_NOT_FOUND);
  // The route must answer for exactly the requested run: no mismatched identity.
  if (runKeyUuid(run.run.run_key) !== uuid || runKeyUuid(run.triage.run_key) !== uuid) {
    return liveError(tenant, issueKey, tenants, RUN_MISMATCH);
  }

  const runIssue = run.run.issue_key.trim() || null;
  let stats: Stats;
  let subgraph: Subgraph = { nodes: [], edges: [] };
  try {
    stats = await getStats(tenant);
    if (runIssue) subgraph = await getSubgraph(tenant, [runIssue]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return liveError(tenant, issueKey, tenants, LIVE_ERROR);
  }

  return {
    source: 'live',
    tenant,
    tenants,
    // The run owns the graph anchor; the `?issue=` value is not selected.
    issueKey: runIssue,
    runKey: `run:${uuid}`,
    run,
    triage: run.triage,
    subgraph,
    stats,
    applyPreview: null,
    applyRefused: null,
    error: null,
  };
}

function liveError(tenant: string, issueKey: string | null, tenants: TenantSummary[], message: string): LiveWorkspaceData {
  return {
    source: 'live',
    tenant,
    tenants,
    issueKey,
    runKey: null,
    run: null,
    triage: null,
    subgraph: { nodes: [], edges: [] },
    stats: null,
    applyPreview: null,
    applyRefused: null,
    error: message,
  };
}
