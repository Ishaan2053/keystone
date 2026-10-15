'use client';
import { useState } from 'react';
import { AlertTriangle, Play, ArrowUpRight, Search } from 'lucide-react';
import type { ApplyResult, TriageResult } from '@/lib/types';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Switch } from './ui/switch';
import { CitationChip } from './citation-chip';
const sections = { labels: 'Suggested labels', reviewers: 'Suggested reviewers', risks: 'Risk flags', decisions: 'Past decisions' } as const;

interface FixtureProps {
  triage: TriageResult;
  dryRun: boolean;
  onDryRunChange: (value: boolean) => void;
  onSelectEvidence: (key: string) => void;
  selectedEvidence: string[];
  applyPreview: ApplyResult;
  applyRefused: ApplyResult;
}

/** Fixture demo card — the B3 shell, unchanged, labelled as synthetic. */
export function TriageCard({ triage, dryRun, onDryRunChange, onSelectEvidence, selectedEvidence, applyPreview, applyRefused }: FixtureProps) {
  const [issue, setIssue] = useState('Synthetic fixture demonstration');
  const [notice, setNotice] = useState('');
  const [actions, setActions] = useState<ApplyResult | null>(null);
  const keys = [...new Set(Object.keys(sections).flatMap(s => triage[s as keyof typeof sections].flatMap(c => c.citations)))];
  const evidence = new Set(triage.trace.flatMap(t => t.evidence));
  function apply() { setActions(dryRun ? applyPreview : applyRefused); setNotice('Fixture preview: no GitHub changes'); }
  return <section className="panel triage-panel" aria-label="Triage card">
    <div className="panel-heading"><h1 className="type-title">Issue triage</h1><span className="badge type-caption">Fixture preview</span></div>
    <form className="issue-form" onSubmit={e => { e.preventDefault(); setActions(null); setNotice('Fixture preview redisplayed. Issue input does not change the supplied fixture.'); }}>
      <label htmlFor="issue" className="type-caption">Issue title or number</label><Input id="issue" value={issue} onChange={e => setIssue(e.target.value)} required />
      <Button type="submit"><Play />Run triage preview</Button>
    </form>
    <p className="fixture-note type-caption">Illustrative fixture · synthetic issue and reviewers.</p>
    <div className="claim-sections">{Object.entries(sections).map(([section, title]) => {
      const claims = triage[section as keyof typeof sections];
      const valid = claims.filter(c => c.citations.length && c.citations.every(k => evidence.has(k)));
      return <section data-claim-section={section} className={`claim-section ${section}`} key={section}><h2 className="type-title">{section === 'risks' && <AlertTriangle />}{title}</h2>
        {!valid.length && <p className="muted-text">No suggestions</p>}
        {valid.length !== claims.length && <p className="error-text type-caption">Uncited fixture claims hidden.</p>}
        <ul>{valid.map(c => <li key={`${c.text}:${c.citations.join('|')}`}><span className={section === 'reviewers' ? 'reviewer type-mono-sm' : section === 'labels' ? 'entity-label type-mono-sm' : ''}>{section === 'reviewers' && !c.text.startsWith('@') ? '@' : ''}{c.text}</span><span className="citations">{c.citations.map(key => <CitationChip key={key} citation={key} number={keys.indexOf(key) + 1} selected={selectedEvidence.includes(key)} onSelect={onSelectEvidence} />)}</span></li>)}</ul>
      </section>;
    })}</div>
    <div className="apply-area"><div className="apply-controls"><Button variant="default" onClick={apply}>Apply to sandbox{dryRun ? ' (dry run)' : ''}<ArrowUpRight /></Button><label className="dry-control"><Switch checked={dryRun} onCheckedChange={onDryRunChange} aria-label="Dry run" /><span className="type-caption">Dry run</span></label></div><p className="type-caption muted-text">Fixture mode never writes to GitHub.</p>
      <div role="status" className="preview-status">{notice && <p>{notice}</p>}{actions && <ul>{actions.actions.map(a => <li key={a.action_key}><strong>{a.status === 'dry_run' ? 'Skipped (dry run)' : 'Refused'}</strong> · {a.kind} <span className="type-mono-sm">{a.value}</span><p className="type-caption muted-text">{a.note}</p></li>)}</ul>}</div>
    </div>
  </section>;
}

interface LiveProps {
  issueKey: string | null;
  issueInput: string;
  onIssueInputChange: (value: string) => void;
  onLoadIssue: () => void;
  loading: boolean;
}

/**
 * Live card: real graph counts are supplied elsewhere; no triage run exists yet,
 * so this stays an honest "no run selected" state with an issue-key form and no
 * synthetic claims, reviewers or apply results.
 */
export function LiveTriageCard({ issueKey, issueInput, onIssueInputChange, onLoadIssue, loading }: LiveProps) {
  return <section className="panel triage-panel" aria-label="Triage card">
    <div className="panel-heading"><h1 className="type-title">Issue triage</h1><span className="badge type-caption">Live · no run</span></div>
    <form className="issue-form" onSubmit={e => { e.preventDefault(); onLoadIssue(); }}>
      <label htmlFor="issue-key" className="type-caption">Issue key</label>
      <Input id="issue-key" value={issueInput} onChange={e => onIssueInputChange(e.target.value)} placeholder="issue:owner/repo#1" autoComplete="off" required />
      <Button type="submit" aria-busy={loading}><Search />Load issue graph</Button>
    </form>
    <p className="fixture-note type-caption">Live graph · no triage run selected.</p>
    <div className="claim-sections">
      <p className="muted-text" data-triage-empty>{issueKey ? `No triage run selected for ${issueKey}.` : 'No triage run selected.'}</p>
    </div>
    <div className="apply-area">
      <p className="type-caption muted-text">Apply is unavailable until a real triage run exists. API mode never writes to GitHub.</p>
    </div>
  </section>;
}
