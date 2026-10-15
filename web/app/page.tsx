import { redirect } from 'next/navigation';
import { loadTenants } from '@/lib/data-source';
import type { TenantSummary } from '@/lib/types';

export default async function Page() {
  let tenants: TenantSummary[];
  try {
    tenants = await loadTenants();
  } catch {
    return <main>No tenants available.</main>;
  }
  if (!tenants.length) return <main>No tenants available.</main>;
  redirect(`/t/${encodeURIComponent(tenants[0].slug)}`);
}
