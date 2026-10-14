import 'server-only';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { notFound } from 'next/navigation';
import type { TenantsResponse } from './types';
import type { WorkspaceData } from './workspace-data';

const fixtureRoot = path.resolve(process.cwd(), '../api/fixtures');
async function readFixture<T>(name: string): Promise<T> {
  try { return JSON.parse(await readFile(path.join(fixtureRoot, `${name}.json`), 'utf8')) as T; }
  catch { throw new Error('Fixture loading failed. Please retry.'); }
}
export async function loadTenants() {
  return (await readFixture<TenantsResponse>('tenants')).tenants;
}
export async function loadWorkspaceData(tenant: string): Promise<WorkspaceData | null> {
  const tenants = await loadTenants();
  if (!tenants.some(t => t.slug === tenant)) notFound();
  if (tenant !== 'falkordb') return null;
  const [triage, subgraph, stats, applyPreview, applyRefused] = await Promise.all([
    readFixture<WorkspaceData['triage']>('triage_result'),
    readFixture<WorkspaceData['subgraph']>('subgraph'),
    readFixture<WorkspaceData['stats']>('stats'),
    readFixture<WorkspaceData['applyPreview']>('apply_dry_run'),
    readFixture<WorkspaceData['applyRefused']>('apply_refused'),
  ]);
  return { tenant, tenants, triage, subgraph, stats, applyPreview, applyRefused };
}
