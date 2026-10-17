'use client';
import { useRouter } from 'next/navigation';
import type { Stats, TenantSummary } from '@/lib/types';
import type { WorkspaceSource } from '@/lib/workspace-data';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { ThemeToggle } from './theme-toggle';
export function Header({ tenant, tenants, source = 'fixture', stats, statsLoading = false, onOpenProof }: { tenant: string; tenants: TenantSummary[]; source?: WorkspaceSource; stats?: Stats | null; statsLoading?: boolean; onOpenProof?: () => void }) {
  const router = useRouter();
  const measured = source === 'live' && stats?.proof && stats.proof.n > 0;
  return <header className="app-header"><a className="wordmark" href="/">Keystone</a><span className="header-divider" /><Select value={tenant} onValueChange={value => router.push(`/t/${encodeURIComponent(value)}`)}><SelectTrigger aria-label="Tenant"><SelectValue /></SelectTrigger><SelectContent>{tenants.map(t => <SelectItem value={t.slug} key={t.slug}>{t.name}</SelectItem>)}</SelectContent></Select><span className="badge type-caption" data-source={source}>{source === 'live' ? 'Live data' : 'Fixture data'}</span><div className="header-end"><button type="button" className="proof proof-trigger type-caption" onClick={onOpenProof} aria-haspopup="dialog">{measured ? <><span className="type-metric">{Number((stats.proof!.methods.graph.reviewer_hit_at_3 * 100).toFixed(1))}%</span><span>Proof · reviewer hit@3</span></> : source === 'live' && !stats ? (statsLoading ? 'Proof loading…' : 'Proof unavailable') : source === 'fixture' && stats?.proof ? 'Fixture Proof' : stats?.unavailable.includes('proof') ? 'Proof unavailable' : 'Proof not measured'}</button><ThemeToggle /></div></header>;
}
