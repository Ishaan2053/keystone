'use client';
import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Copy, Terminal } from 'lucide-react';
import type { ToolResult } from '@/lib/types';
import type { WorkspaceSource } from '@/lib/workspace-data';
import { Button } from './ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible';
async function copyCypher(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}
function Query({ text, number }: { text: string; number: number }) {
  const [status, setStatus] = useState('');
  const [pending, setPending] = useState(false);
  async function copy() { setPending(true); setStatus(''); setStatus(await copyCypher(text) ? 'Copied' : 'Could not copy Cypher'); setPending(false); }
  return <div className="query"><div className="query-heading"><span className="type-caption muted-text">{text.endsWith('.cypher') ? 'Cypher reference (fixture)' : 'Cypher'}</span><Button aria-label={`Copy Cypher ${number}`} disabled={pending} onClick={copy}><Copy />Copy</Button></div><pre className="query-code type-code"><code>{text}</code></pre><p role="status" className={`copy-status type-caption ${status.startsWith('Could') ? 'error-text' : 'muted-text'}`}>{status}</p></div>;
}
function TraceRow({ tool, supporting, reveal }: { tool: ToolResult; supporting: boolean; reveal: string[] | null }) {
  const [open, setOpen] = useState(false);
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!reveal) return;
    setOpen(true);
    const frame = requestAnimationFrame(() => row.current?.scrollIntoView({ block: 'nearest', behavior: 'instant' }));
    return () => cancelAnimationFrame(frame);
  }, [reveal]);
  return <Collapsible ref={row} className="trace-row" data-trace-row data-supporting={supporting} open={open} onOpenChange={setOpen}><CollapsibleTrigger className="trace-trigger" aria-label={`${open ? 'Collapse' : 'Expand'} ${tool.tool}`}><span className="trace-name type-mono-sm">{tool.tool}</span><span className="type-mono-sm muted-text">{tool.ms} ms</span><ChevronDown /></CollapsibleTrigger><p className="trace-summary type-caption muted-text">{tool.empty ? 'No evidence' : Array.isArray(tool.data) ? `${tool.data.length} rows returned` : 'Structured result'}</p><CollapsibleContent className="trace-content"><h3 className="type-label">Parameters</h3><pre className="trace-params type-mono-sm">{JSON.stringify(tool.params, null, 2)}</pre>{tool.cypher.length ? tool.cypher.map((query, i) => <Query key={`${i}:${query}`} text={query} number={i + 1} />) : <p className="type-caption muted-text">No Cypher supplied</p>}<h3 className="type-label">Evidence keys</h3>{tool.evidence.length ? <ul className="trace-evidence type-mono-sm">{tool.evidence.map(k => <li key={k} className={supporting && reveal?.includes(k) ? 'evidence-text' : ''}>{k}</li>)}</ul> : <p className="type-caption muted-text">No evidence</p>}</CollapsibleContent></Collapsible>;
}
export function TracePanel({ trace, askTrace = [], selectedEvidence, source = 'fixture' }: { trace: ToolResult[]; askTrace?: ToolResult[]; selectedEvidence: string[]; source?: WorkspaceSource }) {
  const live = source === 'live';
  const first = trace.findIndex(tool => selectedEvidence.some(key => tool.evidence.includes(key)));
  // The latest Ask answer's own tool calls, so its citation chips light a row too.
  const askFirst = first >= 0 ? -1 : askTrace.findIndex(tool => selectedEvidence.some(key => tool.evidence.includes(key)));
  const intro = live
    ? trace.length
      ? `${trace.length} persisted tool ${trace.length === 1 ? 'call' : 'calls'} · ${trace.reduce((sum, t) => sum + t.ms, 0)} ms`
      : 'No triage run selected · live tool calls appear once a run exists.'
    : `${trace.length} fixture tool ${trace.length === 1 ? 'call' : 'calls'} · ${trace.reduce((sum, t) => sum + t.ms, 0)} ms`;
  return <section className="panel trace-panel" aria-label="Tool trace"><div className="panel-heading"><h2 className="type-title">Tool trace</h2><Terminal /></div><p className="trace-intro type-caption muted-text">{intro}</p>{trace.length ? trace.map((tool, i) => <TraceRow key={`${i}:${tool.tool}`} tool={tool} supporting={selectedEvidence.some(k => tool.evidence.includes(k))} reveal={first === i ? selectedEvidence : null} />) : <p className="trace-intro muted-text">{live ? 'No triage run selected' : 'No evidence'}</p>}{askTrace.length > 0 && <div className="trace-ask" data-ask-trace><h3 className="type-label muted-text trace-intro">Ask · {askTrace.length} {askTrace.length === 1 ? 'tool call' : 'tool calls'}, not persisted</h3>{askTrace.map((tool, i) => <TraceRow key={`ask:${i}:${tool.tool}`} tool={tool} supporting={selectedEvidence.some(k => tool.evidence.includes(k))} reveal={askFirst === i ? selectedEvidence : null} />)}</div>}</section>;
}
