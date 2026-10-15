import { redirect } from 'next/navigation';
import { loadTenants } from '@/lib/fixture-data';
export default async function Page() {
  const tenants = await loadTenants();
  if (!tenants.length) return <main>No fixture tenants configured.</main>;
  redirect(`/t/${encodeURIComponent(tenants[0].slug)}`);
}
