'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Subgraph } from '@/lib/types';
import type { FixtureWorkspaceData, LiveWorkspaceData, WorkspaceData } from '@/lib/workspace-data';
import { getSubgraph } from '@/lib/api-client';
import { Header } from './header';
import { TriageCard, LiveTriageCard } from './triage-card';
import { CanvasPlaceholder } from './canvas-placeholder';
import { TracePanel } from './trace-panel';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from './ui/sheet';
import { Terminal } from 'lucide-react';

export function Workspace(data: WorkspaceData) {
  return data.source === 'live' ? <LiveWorkspace data={data} /> : <FixtureWorkspace data={data} />;
}

function FixtureWorkspace({ data }: { data: FixtureWorkspaceData }) {
  const [dryRun, setDryRun] = useState(true);
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>([]);
  const [traceOpen, setTraceOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <Header tenant={data.tenant} tenants={data.tenants} />
      <main className="workspace">
        <TriageCard triage={data.triage} dryRun={dryRun} onDryRunChange={setDryRun} selectedEvidence={selectedEvidence} onSelectEvidence={key => { setSelectedEvidence([key]); if (window.matchMedia('(min-width: 640px) and (max-width: 1023px)').matches) setTraceOpen(true); }} applyPreview={data.applyPreview} applyRefused={data.applyRefused} />
        <CanvasPlaceholder subgraph={data.subgraph} selectedEvidence={selectedEvidence} />
        <section className="trace-inline">
          <TracePanel trace={data.triage.trace} selectedEvidence={selectedEvidence} />
        </section>
        <div className="trace-drawer">
          <Button ref={triggerRef} variant="secondary" className="trace-drawer-trigger" onClick={() => setTraceOpen(true)} aria-label="Open tool trace"><Terminal />Tool trace</Button>
          <Sheet open={traceOpen} onOpenChange={open => { setTraceOpen(open); if (!open) requestAnimationFrame(() => triggerRef.current?.focus()); }}>
            <SheetContent className="trace-sheet">
              <SheetHeader><SheetTitle>Tool trace</SheetTitle></SheetHeader>
              <TracePanel trace={data.triage.trace} selectedEvidence={selectedEvidence} />
            </SheetContent>
          </Sheet>
        </div>
      </main>
    </>
  );
}

/**
 * Live workspace: the `?issue=` anchor drives a client-side `/subgraph` read.
 * A monotonic request id plus an `AbortController` guarantee a superseded
 * response can never replace a newer one (no fetching library).
 */
function LiveWorkspace({ data }: { data: LiveWorkspaceData }) {
  const [issueInput, setIssueInput] = useState(data.issueKey ?? '');
  const [issueKey, setIssueKey] = useState<string | null>(data.issueKey);
  const [subgraph, setSubgraph] = useState<Subgraph>(data.subgraph);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(data.error);
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>([]);
  const [traceOpen, setTraceOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const loadIssue = useCallback(() => {
    const key = issueInput.trim();
    if (!key) return;
    const request = requestRef.current + 1;
    requestRef.current = request;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    getSubgraph(data.tenant, [key], controller.signal)
      .then(next => {
        if (request !== requestRef.current) return;
        setSubgraph(next);
        setIssueKey(key);
        setSelectedEvidence([]);
        const url = new URL(window.location.href);
        url.searchParams.set('issue', key);
        window.history.replaceState(null, '', url.toString());
      })
      .catch(() => {
        if (request !== requestRef.current || controller.signal.aborted) return;
        setError('Live data unavailable');
      })
      .finally(() => {
        if (request === requestRef.current) setLoading(false);
      });
  }, [data.tenant, issueInput]);

  return (
    <>
      <Header tenant={data.tenant} tenants={data.tenants} source="live" />
      <main className="workspace">
        {error && <p className="live-error-banner type-caption error-text" role="alert" data-live-error>{error}</p>}
        <LiveTriageCard issueKey={issueKey} issueInput={issueInput} onIssueInputChange={setIssueInput} onLoadIssue={loadIssue} loading={loading} />
        <CanvasPlaceholder subgraph={subgraph} selectedEvidence={selectedEvidence} stats={data.stats} issueKey={issueKey} source="live" />
        <section className="trace-inline">
          <TracePanel trace={data.triage?.trace ?? []} selectedEvidence={selectedEvidence} source="live" />
        </section>
        <div className="trace-drawer">
          <Button ref={triggerRef} variant="secondary" className="trace-drawer-trigger" onClick={() => setTraceOpen(true)} aria-label="Open tool trace"><Terminal />Tool trace</Button>
          <Sheet open={traceOpen} onOpenChange={open => { setTraceOpen(open); if (!open) requestAnimationFrame(() => triggerRef.current?.focus()); }}>
            <SheetContent className="trace-sheet">
              <SheetHeader><SheetTitle>Tool trace</SheetTitle></SheetHeader>
              <TracePanel trace={data.triage?.trace ?? []} selectedEvidence={selectedEvidence} source="live" />
            </SheetContent>
          </Sheet>
        </div>
      </main>
    </>
  );
}
