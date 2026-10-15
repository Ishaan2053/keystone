import { loadTenants, loadWorkspaceData } from '@/lib/fixture-data';
import { Workspace } from '@/components/workspace';
export const dynamicParams = false;
export async function generateStaticParams() { return (await loadTenants()).map(t => ({ tenant: t.slug })); }
export default async function TenantPage({ params }: { params: Promise<{ tenant: string }> }) {
  const { tenant } = await params;
  const data = await loadWorkspaceData(tenant);
  if (!data) return <main><h1>Fixture dataset unavailable</h1><p>This tenant has no fixture dataset.</p></main>;
  return <Workspace {...data} />;
}
