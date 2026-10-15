import { dataSource, loadWorkspaceData } from '@/lib/data-source';
import { Header } from '@/components/header';
import { Workspace } from '@/components/workspace';

/**
 * Live reads are per-request, so the tenant route is always rendered on demand.
 * Fixture data still resolves through the same page without static enumeration.
 */
export const dynamic = 'force-dynamic';

export default async function TenantPage({ params, searchParams }: {
  params: Promise<{ tenant: string }>;
  searchParams: Promise<{ issue?: string | string[] }>;
}) {
  const { tenant } = await params;
  // The `?issue=` anchor is a live-mode input; fixture data is not keyed by issue.
  const issueKey = dataSource() === 'api' ? firstParam((await searchParams).issue) : null;
  const data = await loadWorkspaceData(tenant, issueKey);
  if (!data) return <main><h1>Dataset unavailable</h1><p>This tenant has no dataset for the selected data source.</p></main>;
  if (data.source === 'live' && data.error) {
    return (
      <>
        <Header tenant={data.tenant} tenants={data.tenants} source="live" />
        <main className="workspace">
          <section className="panel" data-live-error role="alert" aria-label="Live data error">
            <h1 className="type-title">Live data unavailable</h1>
            <p className="muted-text">The live graph API could not be reached, so no graph data is shown. Fixture data is never substituted in API mode.</p>
          </section>
        </main>
      </>
    );
  }
  return <Workspace {...data} />;
}

function firstParam(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) return value[0] ?? null;
  return value && value.length ? value : null;
}
