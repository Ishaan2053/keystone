import 'server-only';
import { notFound } from 'next/navigation';
import { ApiError, getStats, getSubgraph, getTenants } from './api-client';
import { dataSource } from './data-source-config';
import * as fixtureData from './fixture-data';
import type { Stats, Subgraph, TenantSummary } from './types';
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
 *   loadWorkspaceData(tenant, issueKey): Promise<WorkspaceData | null>
 */
export { dataSource } from './data-source-config';
export type { DataSource } from './data-source-config';

const LIVE_ERROR = 'Live data unavailable';

export async function loadTenants(): Promise<TenantSummary[]> {
  if (dataSource() === 'fixtures') return fixtureData.loadTenants();
  // `apiGet` reads with `cache: 'no-store'`, which opts the route into dynamic
  // rendering, so a live tenant list is never captured by a build or data cache.
  return getTenants();
}

export async function loadWorkspaceData(
  tenant: string,
  issueKey: string | null,
): Promise<WorkspaceData | null> {
  if (dataSource() === 'fixtures') {
    const fixture = await fixtureData.loadWorkspaceData(tenant);
    return fixture ? { ...fixture, source: 'fixture', issueKey: null } : null;
  }

  let tenants: TenantSummary[];
  try {
    tenants = await getTenants();
  } catch {
    return liveError(tenant, issueKey, []);
  }

  // Unknown tenants are a 404, never a fixture fallback or an empty live page.
  if (!tenants.some((candidate) => candidate.slug === tenant)) notFound();

  let stats: Stats;
  let subgraph: Subgraph = { nodes: [], edges: [] };
  try {
    stats = await getStats(tenant);
    if (issueKey) subgraph = await getSubgraph(tenant, [issueKey]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return liveError(tenant, issueKey, tenants);
  }

  return {
    source: 'live',
    tenant,
    tenants,
    issueKey,
    triage: null,
    subgraph,
    stats,
    applyPreview: null,
    applyRefused: null,
    error: null,
  };
}

function liveError(tenant: string, issueKey: string | null, tenants: TenantSummary[]): LiveWorkspaceData {
  return {
    source: 'live',
    tenant,
    tenants,
    issueKey,
    triage: null,
    subgraph: { nodes: [], edges: [] },
    stats: null,
    applyPreview: null,
    applyRefused: null,
    error: LIVE_ERROR,
  };
}
