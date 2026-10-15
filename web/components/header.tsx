'use client';
import { useRouter } from 'next/navigation';
import type { TenantSummary } from '@/lib/types';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { ThemeToggle } from './theme-toggle';
export function Header({ tenant, tenants }: { tenant: string; tenants: TenantSummary[] }) {
  const router = useRouter();
  return <header className="app-header"><a className="wordmark" href="/">Keystone</a><span className="header-divider" /><Select value={tenant} onValueChange={value => router.push(`/t/${encodeURIComponent(value)}`)}><SelectTrigger aria-label="Tenant"><SelectValue /></SelectTrigger><SelectContent>{tenants.map(t => <SelectItem value={t.slug} key={t.slug}>{t.name}</SelectItem>)}</SelectContent></Select><div className="header-end"><span className="proof type-caption">Proof not measured</span><ThemeToggle /></div></header>;
}
