'use client';
import { useState } from 'react';
import { AlertTriangle, Play, ArrowUpRight, Search } from 'lucide-react';
import type { ApplyResult, Claim, RecallRunData, TriageResult } from '@/lib/types';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Switch } from './ui/switch';
import { CitationChip } from './citation-chip';
import { claimId } from '@/lib/evidence-selection';
import { ApiError } from '@/lib/api-client';
const sections = { labels: 'Suggested labels', reviewers: 'Suggested reviewers', risks: 'Risk flags', decisions: 'Past decisions' } as const;

/**
 * The four claim sections, shared by the fixture demo card and the live run
 * card. A claim is shown only when every citation is backed by the supplied
 * evidence, so neither card can present an unsupported suggestion.
 */
function ClaimSections({ triage, evidence, selectedEvidence, onSelectClaim, uncitedNote }: {
  triage: TriageResult;
  evidence: Set<string>;
  selectedEvidence: string[];
  onSelectClaim: (claimId: string, claim: Claim) => void;
  uncitedNote: string | null;
}) {
  const keys = [...new Set(Object.keys(sections).flatMap(s => triage[s as keyof typeof sections].flatMap(c => c.citations)))];
  return <div className="claim-sections">{Object.entries(sections).map(([section, title]) => {
    const claims = triage[section as keyof typeof sections];
    const valid = claims.filter(c => c.citations.length && c.citations.every(k => evidence.has(k)));
    return <section data-claim-section={section} className={`claim-section ${section}`} key={section}><h2 className="type-title">{section === 'risks' && <AlertTriangle />}{title}</h2>
      {!valid.length && <p className="muted-text">No suggestions</p>}
      {uncitedNote && valid.length !== claims.length && <p className="error-text type-caption">{uncitedNote}</p>}
      <ul>{claims.map((c, index) => c.citations.length && c.citations.every(k => evidence.has(k)) ? <li key={`${c.text}:${c.citations.join('|')}`}><span className={section === 'reviewers' ? 'reviewer type-mono-sm' : section === 'labels' ? 'entity-label type-mono-sm' : ''}>{section === 'reviewers' && !c.text.startsWith('@') ? '@' : ''}{c.text}</span><span className="citations">{c.citations.map(key => <CitationChip key={key} citation={key} number={keys.indexOf(key) + 1} selected={selectedEvidence.includes(key)} onSelect={onSelectClaim} claimId={claimId(section, index)} claim={c} />)}</span></li> : null)}</ul>
    </section>;
  })}</div>;
}

interface FixtureProps {
  triage: TriageResult;
  dryRun: boolean;
  onDryRunChange: (value: boolean) => void;
  onSelectClaim: (claimId: string, claim: Claim) => void;
  selectedEvidence: string[];
  applyPreview: ApplyResult;
  applyRefused: ApplyResult;
}

/** Fixture demo card — the B3 shell, unchanged, labelled as synthetic. */
export function TriageCard({ triage, dryRun, onDryRunChange, onSelectClaim, selectedEvidence, applyPreview, applyRefused }: FixtureProps) {
  const [issue, setIssue] = useState('Synthetic fixture demonstration');
  const [notice, setNotice] = useState('');
  const [actions, setActions] = useState<ApplyResult | null>(null);
  const evidence = new Set(triage.trace.flatMap(t => t.evidence));
  function apply() { setActions(dryRun ? applyPreview : applyRefused); setNotice('Fixture preview: no GitHub changes'); }
  return <section className="panel triage-panel" aria-label="Triage card">
    <div className="panel-heading"><h1 className="type-title">Issue triage</h1><span className="badge type-caption">Fixture preview</span></div>
    <form className="issue-form" onSubmit={e => { e.preventDefault(); setActions(null); setNotice('Fixture preview redisplayed. Issue input does not change the supplied fixture.'); }}>
      <label htmlFor="issue" className="type-caption">Issue title or number</label><Input id="issue" value={issue} onChange={e => setIssue(e.target.value)} required />
      <Button type="submit"><Play />Run triage preview</Button>
    </form>
    <p className="fixture-note type-caption">Illustrative fixture · synthetic issue and reviewers.</p>
    <ClaimSections triage={triage} evidence={evidence} selectedEvidence={selectedEvidence} onSelectClaim={onSelectClaim} uncitedNote="Uncited fixture claims hidden." />
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
  tenant?: string;
  runKey?: string | null;
  /** Persisted recall for the selected run; null keeps the honest "no run" state. */
  run?: RecallRunData | null;
  onSelectClaim?: (claimId: string, claim: Claim) => void;
  selectedEvidence?: string[];
  onApply?: (dryRun: boolean) => Promise<ApplyResult>;
}

/**
 * Live card: when a persisted run is selected its real triage, evidence and
 * recorded actions are shown; otherwise it stays an honest "no run selected"
 * state with an issue-key form and no synthetic claims or reviewers.
 */
export function LiveTriageCard({ issueKey, issueInput, onIssueInputChange, onLoadIssue, loading, tenant, runKey, run = null, onSelectClaim, selectedEvidence = [], onApply }: LiveProps) {
  // The API refuses `dry_run: false` (403): live writes happen only through the
  // GitHub webhook, so the switch stays locked on rather than offering a write.
  const dryRun = true;
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [notice, setNotice] = useState('');
  const hasRun = !!onApply || !!runKey;
  const triage = run?.triage ?? null;
  // A claim is evidence-backed only if the run's trace or recall supplied the key.
  const evidence = new Set([...(triage?.trace.flatMap(t => t.evidence) ?? []), ...(run?.evidence ?? [])]);

  async function apply() {
    if (!onApply || pending) return;
    setPending(true);
    setResult(null);
    setNotice('');
    try {
      const response = await onApply(dryRun);
      setResult(response);
      setNotice('Apply complete.');
    } catch (error) {
      setNotice(error instanceof ApiError && error.status === 403
        ? 'Refused: live GitHub writes happen only through the sandbox webhook.'
        : 'Apply failed; no action was reported as applied.');
      setResult(null);
    } finally {
      setPending(false);
    }
  }

  return <section className="panel triage-panel" aria-label="Triage card">
    <div className="panel-heading"><h1 className="type-title">Issue triage</h1><span className="badge type-caption">{triage ? 'Live · run' : 'Live · no run'}</span></div>
    <form className="issue-form" onSubmit={e => { e.preventDefault(); onLoadIssue(); }}>
      <label htmlFor="issue-key" className="type-caption">Issue key</label>
      <Input id="issue-key" value={issueInput} onChange={e => onIssueInputChange(e.target.value)} placeholder="issue:owner/repo#1" autoComplete="off" required />
      <Button type="submit" aria-busy={loading}><Search />Load issue graph</Button>
    </form>
    <p className="fixture-note type-caption">{triage ? 'Live graph · persisted run.' : 'Live graph · no triage run selected.'}</p>
    {run && <div className="run-summary" data-run-summary><p className="type-body-strong type-mono-sm" data-run-key>{run.run.run_key}</p><p className="type-caption muted-text">{run.run.summary}</p></div>}
    {triage && onSelectClaim
      ? <ClaimSections triage={triage} evidence={evidence} selectedEvidence={selectedEvidence} onSelectClaim={onSelectClaim} uncitedNote="Claims without persisted evidence were hidden." />
      : <div className="claim-sections"><p className="muted-text" data-triage-empty>{issueKey ? `No triage run selected for ${issueKey}.` : 'No triage run selected.'}</p></div>}
    {run && run.actions.length > 0 && <section className="run-actions" data-run-actions aria-label="Recorded actions">
      <h2 className="type-title">Recorded actions</h2>
      <ul>{run.actions.map(action => <li key={action.action_key} data-action-status={action.status}>
        <span className="entity-label type-mono-sm">{action.kind}</span> <span className="type-mono-sm">{action.value}</span>
        {action.module && <span className="type-caption muted-text"> · {action.module}</span>}
        <span className="badge type-caption">{action.status}</span>
        {action.note && <p className="type-caption muted-text">{action.note}</p>}
      </li>)}</ul>
    </section>}
    <div className="apply-area">
      {hasRun ? <>
        <div className="apply-controls">
          <Button variant="default" onClick={apply} disabled={pending || !onApply} aria-busy={pending}>
            Apply to sandbox{dryRun ? ' (dry run)' : ''}<ArrowUpRight />
          </Button>
          <label className="dry-control"><Switch checked={dryRun} disabled aria-label="Dry run" aria-describedby="live-dry-run-note" /><span className="type-caption">Dry run</span></label>
        </div>
        <p id="live-dry-run-note" className="type-caption muted-text" data-live-dry-run-note>Live apply is always a dry run here. Real GitHub writes happen only through the sandbox webhook.</p>
      </> : <p className="type-caption muted-text">Apply is unavailable until a real triage run exists. API mode never writes to GitHub.</p>}
      <div role="status" className="preview-status">
        {notice && <p>{notice}</p>}
        {result && <ul>{result.actions.map(a => <li key={a.action_key}><strong>{a.status === 'dry_run' ? 'Skipped (dry run)' : a.status === 'applied' ? 'Applied' : 'Refused'}</strong> · {a.kind} <span className="type-mono-sm">{a.value}</span>{a.note && <p className="type-caption muted-text">{a.note}</p>}</li>)}</ul>}
      </div>
    </div>
  </section>;
}
