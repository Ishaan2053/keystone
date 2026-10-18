import { notFound } from 'next/navigation';
import { loadTenants } from '@/lib/data-source';
import type { TenantSummary } from '@/lib/types';

export default async function TenantLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  let tenants: TenantSummary[] | null = null;
  try {
    tenants = await loadTenants();
  } catch {
    tenants = null;
  }
  if (tenants && !tenants.some((candidate) => candidate.slug === tenant)) notFound();
  return children;
}
