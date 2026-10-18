import 'server-only';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { notFound } from 'next/navigation';
import type { TenantsResponse } from './types';
import type { FixtureDataset } from './workspace-data';

const fixtureRoot = path.resolve(process.cwd(), '../api/fixtures');
async function readFixture<T>(name: string): Promise<T> {
  try {
    return JSON.parse(await readFile(path.join(fixtureRoot, `${name}.json`), 'utf8')) as T;
  } catch {
    throw new Error('Fixture loading failed. Please retry.');
  }
}
export async function loadTenants() {
  return (await readFixture<TenantsResponse>('tenants')).tenants;
}
export async function loadWorkspaceData(tenant: string): Promise<FixtureDataset | null> {
  const tenants = await loadTenants();
  if (!tenants.some((t) => t.slug === tenant)) notFound();
  if (tenant !== 'falkordb') return null;
  const [triage, subgraph, stats, path, applyPreview, applyRefused, ask] = await Promise.all([
    readFixture<FixtureDataset['triage']>('triage_result'),
    readFixture<FixtureDataset['subgraph']>('subgraph'),
    readFixture<FixtureDataset['stats']>('stats'),
    readFixture<FixtureDataset['path']>('path'),
    readFixture<FixtureDataset['applyPreview']>('apply_dry_run'),
    readFixture<FixtureDataset['applyRefused']>('apply_refused'),
    readFixture<FixtureDataset['ask']>('ask'),
  ]);
  return { tenant, tenants, triage, subgraph, stats, path, applyPreview, applyRefused, ask };
}
