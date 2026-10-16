'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Claim, PathData, Subgraph } from '@/lib/types';
import type { FixtureWorkspaceData, LiveWorkspaceData, WorkspaceData } from '@/lib/workspace-data';
import { getSubgraph, applyRun } from '@/lib/api-client';
import type { ApplyResult } from '@/lib/types';
import {
  createSelectionGuard,
  distinctKeys,
  nodeSelection,
  pathNodeKeys,
  resolveClaimContext,
  supportedAbsenceKeys,
  type EvidenceSelection,
} from '@/lib/evidence-selection';
import { Header } from './header';
import { TriageCard, LiveTriageCard } from './triage-card';
import { GraphCanvas } from './graph/graph-canvas';
import { TracePanel } from './trace-panel';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from './ui/sheet';
import { Terminal } from 'lucide-react';

/** A tablet-width selection opens the trace drawer after highlighting. */
function onTablet(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(min-width: 640px) and (max-width: 1023px)').matches;
}

/**
 * Fixture mode never computes a path in the browser. It uses the explicit path
 * fixture (`api/fixtures/path.json`) as a labelled demonstration: a citation is
 * shown on the fixture path only when the fixture actually connects it.
 */
function fixtureClaimSelection(id: string, claim: Claim, demo: PathData): EvidenceSelection {
  const citationKeys = distinctKeys(claim.citations);
  const paths: PathData[] = [];
  const last = demo.keys[demo.keys.length - 1];
  for (const key of citationKeys) {
    if (key === demo.keys[0]) paths.push({ keys: [key], rels: [] });
    else if (key === last) paths.push({ keys: [...demo.keys], rels: [...demo.rels] });
  }
  // The fixture path is a demonstration, not a lookup per citation: a citation
  // it does not connect is not evidence of absence, so no-path stays empty.
  return { claimId: id, citationKeys, paths, nodeKeys: pathNodeKeys(paths), missingKeys: [], state: 'ready', riskKeys: [], noPathKeys: [], failedKeys: [] };
}

export function Workspace(data: WorkspaceData) {
  return data.source === 'live' ? <LiveWorkspace data={data} /> : <FixtureWorkspace data={data} />;
}

function FixtureWorkspace({ data }: { data: FixtureWorkspaceData }) {
  const [dryRun, setDryRun] = useState(true);
  const [selection, setSelection] = useState<EvidenceSelection | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const selectedEvidence = selection?.citationKeys ?? [];
  const openTraceOnTablet = useCallback(() => { if (onTablet()) setTraceOpen(true); }, []);
  const selectClaim = useCallback((id: string, claim: Claim) => {
    setSelection(fixtureClaimSelection(id, claim, data.path.data));
    openTraceOnTablet();
  }, [data.path, openTraceOnTablet]);
  const selectNode = useCallback((key: string) => { setSelection(nodeSelection(key)); openTraceOnTablet(); }, [openTraceOnTablet]);
  const clearSelection = useCallback(() => setSelection(null), []);

  return (
    <>
      <Header tenant={data.tenant} tenants={data.tenants} />
      <main className="workspace">
        <TriageCard triage={data.triage} dryRun={dryRun} onDryRunChange={setDryRun} selectedEvidence={selectedEvidence} onSelectClaim={selectClaim} applyPreview={data.applyPreview} applyRefused={data.applyRefused} />
        <GraphCanvas subgraph={data.subgraph} issueKey={null} selection={selection} onNodeSelect={selectNode} onClearSelection={clearSelection} source="fixture" />
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
 * Live workspace: the `?issue=` anchor drives a client-side `/subgraph` read,
 * while a `?run=` deep link is resolved on the server and rendered as a
 * persisted triage. A monotonic request id plus an `AbortController` guarantee a
 * superseded response can never replace a newer one (no fetching library).
 * Selection uses the same guard: a stale or aborted path result never mutates
 * graph or highlight state.
 */
function LiveWorkspace({ data }: { data: LiveWorkspaceData }) {
  const [issueInput, setIssueInput] = useState(data.issueKey ?? '');
  const [issueKey, setIssueKey] = useState<string | null>(data.issueKey);
  // A manual issue load supersedes the server-resolved `?run=` deep link, so the
  // persisted run card, its trace and its Apply binding are cleared client-side:
  // run data must never be shown against a different issue.
  const [runSuperseded, setRunSuperseded] = useState(false);
  const runKey = runSuperseded ? null : (data.runKey ?? null);
  const run = runSuperseded ? null : (data.run ?? null);
  const [subgraph, setSubgraph] = useState<Subgraph>(data.subgraph);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(data.error);
  const [selection, setSelection] = useState<EvidenceSelection | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const subgraphRef = useRef(subgraph);
  const selectionGuard = useMemo(() => createSelectionGuard(), []);
  // The run's trace is part of the superseded run view, so it clears with it.
  const trace = runSuperseded ? [] : (data.triage?.trace ?? []);

  useEffect(() => { subgraphRef.current = subgraph; }, [subgraph]);
  useEffect(() => () => { abortRef.current?.abort(); selectionGuard.cancel(); }, [selectionGuard]);

  const selectedEvidence = selection?.citationKeys ?? [];

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
        // A new issue supersedes any deep-linked run and resets selection; no
        // stale run or path may survive it.
        setRunSuperseded(true);
        selectionGuard.cancel();
        setSelection(null);
        const url = new URL(window.location.href);
        url.searchParams.set('issue', key);
        // A manual issue choice supersedes the run deep link, so a reload is consistent.
        url.searchParams.delete('run');
        window.history.replaceState(null, '', url.toString());
      })
      .catch(() => {
        if (request !== requestRef.current || controller.signal.aborted) return;
        setError('Live data unavailable');
      })
      .finally(() => {
        if (request === requestRef.current) setLoading(false);
      });
  }, [data.tenant, issueInput, selectionGuard]);

  const selectClaim = useCallback((id: string, claim: Claim) => {
    const citationKeys = distinctKeys(claim.citations);
    if (!issueKey) {
      // No issue anchor: the claim is known but no path can be resolved.
      setSelection({ claimId: id, citationKeys, paths: [], nodeKeys: [], missingKeys: [], state: 'ready', riskKeys: [], noPathKeys: [], failedKeys: [] });
      return;
    }
    setSelection({ claimId: id, citationKeys, paths: [], nodeKeys: [], missingKeys: [], state: 'loading', riskKeys: [], noPathKeys: [], failedKeys: [] });
    selectionGuard
      .run(signal => resolveClaimContext(data.tenant, issueKey, claim, id, () => subgraphRef.current, trace, signal))
      .then(result => {
        if (!result) return;
        setSubgraph(result.graph);
        setSelection(result.next);
      })
      .catch(() => {
        setSelection(current => (current && current.claimId === id ? { ...current, state: 'error' } : current));
      });
  }, [data.tenant, issueKey, selectionGuard, trace]);

  const selectNode = useCallback((key: string) => {
    selectionGuard.cancel();
    setSelection(nodeSelection(key));
    if (onTablet()) setTraceOpen(true);
  }, [selectionGuard]);
  const clearSelection = useCallback(() => { selectionGuard.cancel(); setSelection(null); }, [selectionGuard]);

  return (
    <>
      <Header tenant={data.tenant} tenants={data.tenants} source="live" />
      <main className="workspace">
        {error && <p className="live-error-banner type-caption error-text" role="alert" data-live-error>{error}</p>}
        <LiveTriageCard issueKey={issueKey} issueInput={issueInput} onIssueInputChange={setIssueInput} onLoadIssue={loadIssue} loading={loading} tenant={data.tenant} runKey={runKey} run={run} onSelectClaim={selectClaim} selectedEvidence={selectedEvidence} onApply={runKey ? (dryRun: boolean) => applyRun(data.tenant, runKey, dryRun) : undefined} />
        <GraphCanvas subgraph={subgraph} issueKey={issueKey} selection={selection} onNodeSelect={selectNode} onClearSelection={clearSelection} stats={data.stats} source="live" />
        <section className="trace-inline">
          <TracePanel trace={trace} selectedEvidence={selectedEvidence} source="live" />
        </section>
        <div className="trace-drawer">
          <Button ref={triggerRef} variant="secondary" className="trace-drawer-trigger" onClick={() => setTraceOpen(true)} aria-label="Open tool trace"><Terminal />Tool trace</Button>
          <Sheet open={traceOpen} onOpenChange={open => { setTraceOpen(open); if (!open) requestAnimationFrame(() => triggerRef.current?.focus()); }}>
            <SheetContent className="trace-sheet">
              <SheetHeader><SheetTitle>Tool trace</SheetTitle></SheetHeader>
              <TracePanel trace={trace} selectedEvidence={selectedEvidence} source="live" />
            </SheetContent>
          </Sheet>
        </div>
      </main>
    </>
  );
}
