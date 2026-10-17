'use client';
import { useEffect, useRef, useState } from 'react';
import { MessageSquare } from 'lucide-react';
import type { AskResult, Claim } from '@/lib/types';
import { ApiError } from '@/lib/api-client';
import { claimId } from '@/lib/evidence-selection';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { CitationChip } from './citation-chip';

/**
 * Cited Q&A at the bottom of the triage card (spec section 11). Answer claims
 * render exactly like triage claims: only when every citation is backed by the
 * answer's own trace evidence, each citation as a chip that selects the claim.
 * The submit stays secondary: Apply is the one primary action on the card.
 */
export function AskBox({ onAsk, onResult, onSelectClaim, selectedEvidence, note = null, resetKey = null }: {
  onAsk: (question: string) => Promise<AskResult>;
  /** The answer's trace, so the workspace can show it beside the run's trace. */
  onResult?: (result: AskResult | null) => void;
  onSelectClaim: (claimId: string, claim: Claim) => void;
  selectedEvidence: string[];
  note?: string | null;
  /** A new tenant clears the answer; a new run keeps it (ask is tenant-wide). */
  resetKey?: string | null;
}) {
  const [question, setQuestion] = useState('');
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [asked, setAsked] = useState('');
  const [error, setError] = useState('');
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    setPending(false); setResult(null); setAsked(''); setError('');
    return () => { generation.current += 1; };
  }, [resetKey]);

  async function submit() {
    const text = question.trim();
    if (!text || pending) return;
    const current = ++generation.current;
    setPending(true); setError(''); setResult(null); onResult?.(null);
    try {
      const answer = await onAsk(text);
      if (current !== generation.current) return;
      setResult(answer); setAsked(text); onResult?.(answer);
    } catch (failure) {
      if (current !== generation.current) return;
      setError(failure instanceof ApiError && failure.status === 503
        ? 'Ask is unavailable: the graph or the model did not answer. No answer is shown.'
        : 'Ask failed; no answer is shown.');
    } finally {
      if (current === generation.current) setPending(false);
    }
  }

  const evidence = new Set(result?.trace.flatMap(t => t.evidence) ?? []);
  const claims = result?.answer ?? [];
  const backed = (c: Claim) => c.citations.length > 0 && c.citations.every(k => evidence.has(k));
  const shown = claims.filter(backed);
  const keys = [...new Set(shown.flatMap(c => c.citations))];
  const emptyTool = result?.trace.some(t => t.empty) ?? false;

  return <section className="ask-area" aria-label="Ask" data-ask>
    <form className="ask-form" onSubmit={e => { e.preventDefault(); void submit(); }}>
      <label htmlFor="ask-question" className="type-label muted-text">Ask</label>
      <div className="ask-row">
        <Input id="ask-question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Why was this module split?" autoComplete="off" required />
        <Button type="submit" disabled={pending || !question.trim()} aria-busy={pending}><MessageSquare />{pending ? 'Asking…' : 'Ask'}</Button>
      </div>
    </form>
    {note && <p className="type-caption muted-text">{note}</p>}
    <div role="status" className="ask-status" data-ask-status>
      {pending && <p className="type-caption muted-text">Searching past decisions…</p>}
      {error && <p className="type-caption error-text">{error}</p>}
      {result && <>
        <p className="type-caption muted-text" data-ask-question>Q: {asked}</p>
        {!shown.length
          ? <p className="muted-text" data-ask-empty>{emptyTool ? 'No evidence: no decision or doc matched this question.' : 'No evidence-backed answer for this question.'}</p>
          : <ul className="ask-answer">{shown.map((c, index) => <li key={`${c.text}:${c.citations.join('|')}`}>
            <span>{c.text}</span>
            <span className="citations">{c.citations.map(key => <CitationChip key={key} citation={key} number={keys.indexOf(key) + 1} selected={selectedEvidence.includes(key)} onSelect={onSelectClaim} claimId={claimId('ask', index)} claim={c} />)}</span>
          </li>)}</ul>}
        {shown.length > 0 && shown.length !== claims.length && <p className="error-text type-caption">Uncited answer claims were hidden.</p>}
      </>}
    </div>
  </section>;
}
