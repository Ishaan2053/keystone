'use client';
import { useRouter } from 'next/navigation';
import type { Stats, TenantSummary } from '@/lib/types';
import type { WorkspaceSource } from '@/lib/workspace-data';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { CircleHelp } from 'lucide-react';
import { ThemeToggle } from './theme-toggle';
import { Button } from './ui/button';
import { WelcomeDialog, openWelcome } from './welcome-dialog';
export function Header({ tenant, tenants, source = 'fixture', stats, statsLoading = false, onOpenProof }: { tenant: string; tenants: TenantSummary[]; source?: WorkspaceSource; stats?: Stats | null; statsLoading?: boolean; onOpenProof?: () => void }) {
  const router = useRouter();
  const measured = source === 'live' && stats?.proof && stats.proof.n > 0;
  return <header className="app-header"><a className="wordmark" href="/"><KeystoneMark />Keystone</a><span className="header-divider" /><Select value={tenant} onValueChange={value => router.push(`/t/${encodeURIComponent(value)}`)}><SelectTrigger aria-label="Tenant"><SelectValue /></SelectTrigger><SelectContent>{tenants.map(t => <SelectItem value={t.slug} key={t.slug}>{t.name}</SelectItem>)}</SelectContent></Select><span className={`badge type-caption source-badge source-${source}`} data-source={source}><span className="source-dot" aria-hidden="true" />{source === 'live' ? 'Live data' : 'Fixture data'}</span><div className="header-end"><button type="button" className="proof proof-trigger type-caption" onClick={onOpenProof} aria-haspopup="dialog" data-tour="proof">{measured ? <><span className="type-metric">{Number((stats.proof!.methods.graph.reviewer_hit_at_3 * 100).toFixed(1))}%</span><span>Proof · reviewer hit@3</span></> : source === 'live' && !stats ? (statsLoading ? 'Proof loading…' : 'Proof unavailable') : source === 'fixture' && stats?.proof ? 'Fixture Proof' : stats?.unavailable.includes('proof') ? 'Proof unavailable' : 'Proof not measured'}</button><Button variant="ghost" className="help-button" onClick={openWelcome} aria-label="How Keystone works" data-tour="help"><CircleHelp /><span className="help-label">Help</span></Button><ThemeToggle /></div><WelcomeDialog /></header>;
}

/** The wordmark's glyph: a keystone, the wedge that locks an arch. */
function KeystoneMark() {
  return <svg viewBox="0 0 24 24" className="wordmark-mark" aria-hidden="true"><path d="M7 3h10l-2.5 18h-5z" fill="currentColor" /><path d="M2 21c0-6 3-10 6.5-12M22 21c0-6-3-10-6.5-12" fill="none" stroke="var(--muted-foreground)" strokeWidth="1.75" strokeLinecap="round" /></svg>;
}
