'use client';
import { useRef } from 'react';
import type { ProofRates, Stats } from '@/lib/types';
import { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle } from './ui/sheet';
import { Button } from './ui/button';

const methods = [
  ['graph', 'Graph'], ['codeowners', 'CODEOWNERS'], ['similar_issues', 'Similar issues'],
] as const;
const groups: [keyof ProofRates, string][] = [
  ['reviewer_hit_at_3', 'Reviewer hit@3'], ['module_hit_at_1', 'Module hit@1'],
  ['module_hit_at_3', 'Module hit@3'], ['label_precision_at_2', 'Label precision@2'],
];
const percent = (rate: number) => `${Number((rate * 100).toFixed(1))}%`;

export function ProofPanel({ open, onOpenChange, stats, loading, stale, error, onRetry, source = 'live' }: {
  open: boolean; onOpenChange: (open: boolean) => void; stats: Stats | null;
  loading: boolean; stale: boolean; error: string | null; onRetry: () => void;
  source?: 'fixture' | 'live';
}) {
  const triggerRef = useRef<HTMLElement | null>(null);
  const proof = stats?.proof;
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent className="proof-sheet" aria-describedby={undefined}
      onOpenAutoFocus={() => { triggerRef.current = document.activeElement as HTMLElement; }}
      onCloseAutoFocus={event => { event.preventDefault(); triggerRef.current?.focus(); }}>
      <SheetHeader><SheetTitle>Proof</SheetTitle></SheetHeader>
      <SheetClose asChild><Button variant="secondary" className="proof-close">Close Proof</Button></SheetClose>
      {source === 'fixture' && <p className="type-caption muted-text">Fixture Proof · illustrative data, not live measurements</p>}
      <div className="proof-status" aria-live="polite">
        {loading && <p className="type-caption muted-text">{stats ? 'Refreshing measurements…' : 'Loading measurements…'}</p>}
        {!stats && !loading && <p className="muted-text">Stats unavailable</p>}
        {error && <div role="alert"><p>{stale ? 'Stale measurements. ' : ''}{error}</p><Button variant="secondary" disabled={loading} onClick={onRetry}>Retry</Button></div>}
      </div>
      {stats && <>
      {proof && proof.n > 0 ? <>
        <p className="type-caption muted-text">{source === 'fixture' ? 'Illustrative comparisons' : 'Measured comparisons'} · shared 0–100% scale</p>
        <ul className="proof-legend type-caption" aria-label="Methods">{methods.map(([key, label], index) => <li key={key}><span aria-hidden="true" style={{ background: `var(--chart-${index + 1})` }} />{label}</li>)}</ul>
        {groups.map(([key, label]) => <section className="proof-group" data-proof-group key={key} aria-label={label}>
          <h3 className="type-body-strong">{label}</h3>
          <dl>{methods.map(([method, name], index) => <div className="proof-rate" key={method}>
            <dt className="type-caption">{name}</dt>
            <dd><span className="proof-bar" aria-hidden="true"><span style={{ width: percent(proof.methods[method][key]), background: `var(--chart-${index + 1})` }} /></span><span className="type-mono-sm" data-proof-value>{percent(proof.methods[method][key])}</span></dd>
          </div>)}</dl>
        </section>)}
        <p className="type-caption muted-text">n={proof.n}</p>
      </> : <div className="proof-empty muted-text">
        <p>Proof not measured</p>
        {proof && <><p className="type-caption">No evaluable sample. Rates need at least one evaluated issue.</p><p className="type-caption">n={proof.n}</p></>}
      </div>}
      <section className="proof-latencies" aria-label="Tool latencies">
        <h3 className="type-title">Tool latencies</h3>
        {stats?.latencies.length ? <table>
          <caption className="type-caption muted-text">Tool latency percentiles in milliseconds</caption>
          <thead><tr><th scope="col">Tool</th><th scope="col">p50 (ms)</th><th scope="col">p95 (ms)</th></tr></thead>
          <tbody>{stats.latencies.map(latency => <tr key={latency.tool}><th scope="row" className="type-mono-sm">{latency.tool}</th><td className="type-mono-sm">{latency.p50_ms} ms</td><td className="type-mono-sm">{latency.p95_ms} ms</td></tr>)}</tbody>
        </table> : <p className="type-caption muted-text">Tool latencies not measured</p>}
      </section>
      </>}
    </SheetContent>
  </Sheet>;
}
