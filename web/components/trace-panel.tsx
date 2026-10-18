'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, ChevronRight, CircleDot, Copy, FolderTree, Lightbulb, Route, Terminal, Users, Check } from 'lucide-react';
import type { ToolResult } from '@/lib/types';
import type { WorkspaceSource } from '@/lib/workspace-data';
import { toolSummary } from '@/lib/run-insights';
import { keyKind, keyLabel } from '@/lib/github';
import { Button } from './ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible';
import { CodeBlock, JsonBlock } from './code-block';

const TOOL_ICON: Record<string, ReactNode> = {
  issue_context: <CircleDot className="icon-issue" />,
  affected_modules: <FolderTree className="icon-muted" />,
  rank_reviewers: <Users className="icon-person" />,
  bus_factor: <AlertTriangle className="icon-attention" />,
  find_decisions: <Lightbulb className="icon-knowledge" />,
  explain_path: <Route className="icon-muted" />,
};

async function copyCypher(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

function Query({ text, number }: { text: string; number: number }) {
  const [status, setStatus] = useState('');
  const [pending, setPending] = useState(false);
  async function copy() { setPending(true); setStatus(''); setStatus(await copyCypher(text) ? 'Copied' : 'Could not copy Cypher'); setPending(false); }
  return <div className="query">
    <div className="query-heading">
      <span className="type-caption muted-text">{text.endsWith('.cypher') ? 'Cypher reference (fixture)' : 'Cypher'}</span>
      <Button variant="ghost" aria-label={`Copy Cypher ${number}`} disabled={pending} onClick={copy}>{status === 'Copied' ? <Check className="icon-good" /> : <Copy />}Copy</Button>
    </div>
    <CodeBlock code={text} lang="cypher" className="query-code" />
    <p role="status" className={`copy-status type-caption ${status.startsWith('Could') ? 'error-text' : 'muted-text'}`}>{status}</p>
  </div>;
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
  return <Collapsible ref={row} className="trace-row" data-trace-row data-supporting={supporting} open={open} onOpenChange={setOpen}>
    <CollapsibleTrigger className="trace-trigger" aria-label={`${open ? 'Collapse' : 'Expand'} ${tool.tool}`}>
      <ChevronRight className="trace-chevron" />
      {TOOL_ICON[tool.tool] ?? <Terminal className="icon-muted" />}
      <span className="trace-main">
        <span className="trace-name type-mono-sm">{tool.tool}</span>
        <span className={`trace-summary type-caption ${tool.empty ? 'muted-text' : ''}`}>{toolSummary(tool)}</span>
      </span>
      <span className="trace-ms type-mono-sm muted-text">{tool.ms} ms</span>
    </CollapsibleTrigger>
    <CollapsibleContent className="trace-content">
      <h3 className="trace-heading type-caption">Parameters</h3>
      <JsonBlock value={tool.params} className="trace-params" />
      {!tool.empty && tool.data !== null && tool.data !== undefined && <details className="trace-result">
        <summary className="trace-heading type-caption">Result</summary>
        <JsonBlock value={tool.data} className="trace-data" />
      </details>}
      {tool.cypher.length ? tool.cypher.map((query, i) => <Query key={`${i}:${query}`} text={query} number={i + 1} />) : <p className="type-caption muted-text">No Cypher supplied</p>}
      <h3 className="trace-heading type-caption">Evidence keys{tool.evidence.length > 0 && <span className="muted-text"> · {tool.evidence.length}</span>}</h3>
      {tool.evidence.length
        ? <ul className="trace-evidence">{tool.evidence.map(k => <li key={k} className={`key-chip key-${(keyKind(k) ?? 'other').toLowerCase()}${supporting && reveal?.includes(k) ? ' evidence-text' : ''}`} title={k}><span className="type-mono-sm">{keyLabel(k)}</span></li>)}</ul>
        : <p className="type-caption muted-text">No evidence</p>}
    </CollapsibleContent>
  </Collapsible>;
}

export function TracePanel({ trace, askTrace = [], selectedEvidence, source = 'fixture' }: { trace: ToolResult[]; askTrace?: ToolResult[]; selectedEvidence: string[]; source?: WorkspaceSource }) {
  const live = source === 'live';
  const first = trace.findIndex(tool => selectedEvidence.some(key => tool.evidence.includes(key)));
  // The latest Ask answer's own tool calls, so its citation chips light a row too.
  const askFirst = first >= 0 ? -1 : askTrace.findIndex(tool => selectedEvidence.some(key => tool.evidence.includes(key)));
  const total = trace.reduce((sum, t) => sum + t.ms, 0);
  const intro = trace.length
    ? `${trace.length} ${live ? 'persisted' : 'fixture'} graph ${trace.length === 1 ? 'query' : 'queries'} · ${total} ms`
    : live ? 'Each graph query a run makes appears here, with its Cypher.' : 'No evidence';
  return <section className="panel trace-panel" aria-label="Tool trace" data-tour="trace">
    <div className="panel-heading"><h2 className="panel-title"><Terminal className="icon-muted" />Tool trace</h2><span className="type-caption muted-text trace-intro">{intro}</span></div>
    <div className="trace-scroll">
      {trace.length
        ? trace.map((tool, i) => <TraceRow key={`${i}:${tool.tool}`} tool={tool} supporting={selectedEvidence.some(k => tool.evidence.includes(k))} reveal={first === i ? selectedEvidence : null} />)
        : live && <p className="trace-empty muted-text type-caption">No triage run selected</p>}
      {askTrace.length > 0 && <div className="trace-ask" data-ask-trace>
        <h3 className="trace-ask-heading type-caption muted-text">Ask · {askTrace.length} {askTrace.length === 1 ? 'graph query' : 'graph queries'}, not persisted</h3>
        {askTrace.map((tool, i) => <TraceRow key={`ask:${i}:${tool.tool}`} tool={tool} supporting={selectedEvidence.some(k => tool.evidence.includes(k))} reveal={askFirst === i ? selectedEvidence : null} />)}
      </div>}
    </div>
  </section>;
}
