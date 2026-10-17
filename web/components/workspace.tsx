'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Claim, PathData, Subgraph } from '@/lib/types';
import { useSearchParams } from 'next/navigation';
import type { FixtureWorkspaceData, LiveWorkspaceData, WorkspaceData } from '@/lib/workspace-data';
import { getStats, getSubgraph, getRun, runKeyUuid, applyRun } from '@/lib/api-client';
import { ProofPanel } from './proof-panel';
import { LiveRuns } from './live-runs';
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
  return data.source === 'live' ? <LiveWorkspace key={data.tenant} data={data} /> : <FixtureWorkspace data={data} />;
}

function FixtureWorkspace({ data }: { data: FixtureWorkspaceData }) {
  const [proofOpen, setProofOpen] = useState(false);
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
      <Header tenant={data.tenant} tenants={data.tenants} stats={data.stats} onOpenProof={() => setProofOpen(true)} />
      <ProofPanel open={proofOpen} onOpenChange={setProofOpen} stats={data.stats} loading={false} stale={false} error={null} onRetry={() => {}} source="fixture" />
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
  const [proofOpen, setProofOpen] = useState(false);
  const [stats, setStats] = useState(data.stats);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(data.stats ? null : 'Unable to load stats. Try again.');
  const refreshRef = useRef<(() => void) | null>(null);

  // Panel-owned reads never abort or participate in selected-run/graph requests.
  // Cleanup invalidates this entire open/tenant generation, including late replies.
  useEffect(() => {
    if (!proofOpen) return;
    let active = true;
    let inFlight = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      if (!active || inFlight) return;
      inFlight = true;
      controller = new AbortController();
      setStatsLoading(true);
      try {
        const next = await getStats(data.tenant, controller.signal);
        if (active && !controller.signal.aborted) { setStats(next); setStatsError(null); }
      } catch {
        if (active && !controller.signal.aborted) setStatsError('Unable to refresh stats. Try again.');
      } finally {
        inFlight = false;
        if (active) setStatsLoading(false);
      }
    };
    refreshRef.current = () => { void refresh(); };
    void refresh();
    const interval = setInterval(() => { void refresh(); }, 5000);
    return () => { active = false; clearInterval(interval); controller?.abort(); refreshRef.current = null; };
  }, [proofOpen, data.tenant]);
  const searchParams = useSearchParams();
  const search = searchParams.toString();
  const navigationRef = useRef(search);
  const incomingRef = useRef(data);
  const [issueInput, setIssueInput] = useState(data.issueKey ?? '');
  const [view, setView] = useState({ issueKey: data.issueKey, runKey: data.runKey ?? null, run: data.run ?? null, subgraph: data.subgraph });
  const { issueKey, runKey, run, subgraph } = view;
  const [autoSelect, setAutoSelect] = useState(!searchParams.has('run') && !searchParams.has('issue') && !data.issueKey && !data.runKey && !data.error);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(data.error);
  const [selection, setSelection] = useState<EvidenceSelection | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const subgraphRef = useRef(subgraph);
  const selectionGuard = useMemo(() => createSelectionGuard(), []);
  const trace = run?.triage.trace ?? [];
  const setSubgraph = useCallback((next: Subgraph) => {
    subgraphRef.current = next;
    setView(current => ({ ...current, subgraph: next }));
  }, []);

  useEffect(() => { subgraphRef.current = subgraph; }, [subgraph]);
  useEffect(() => () => { requestRef.current += 1; abortRef.current?.abort(); selectionGuard.cancel(); }, [selectionGuard]);

  const selectedEvidence = selection?.citationKeys ?? [];

  // A pending claim keeps resolving against the current view while a read is in
  // flight: only a committed view cancels it, so a failed read cannot strand it.
  const beginRead = useCallback(() => {
    const request = ++requestRef.current;
    abortRef.current?.abort();
    setAutoSelect(false);
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    return { request, controller };
  }, []);

  const updateUrl = useCallback((kind: 'run' | 'issue', value: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set(kind, value);
    url.searchParams.delete(kind === 'run' ? 'issue' : 'run');
    navigationRef.current = url.searchParams.toString();
    if (url.href !== window.location.href) window.history.pushState(null, '', url.toString());
  }, []);

  const selectRun = useCallback(async (key: string, writeUrl = true): Promise<boolean> => {
    const { request, controller } = beginRead();
    try {
      const uuid = runKeyUuid(key);
      if (!uuid) throw new Error('Invalid run id');
      const result = await getRun(data.tenant, key, controller.signal);
      const recalled = result.data;
      if (result.empty || !recalled || recalled.run.run_key !== `run:${uuid}` || recalled.triage?.run_key !== `run:${uuid}` || !recalled.run.issue_key) throw new Error('Run not found or identity mismatch');
      const graph = await getSubgraph(data.tenant, [recalled.run.issue_key], controller.signal);
      if (request !== requestRef.current || controller.signal.aborted) return false;
      selectionGuard.cancel();
      subgraphRef.current = graph;
      setView({ issueKey: recalled.run.issue_key, runKey: `run:${uuid}`, run: recalled, subgraph: graph });
      setIssueInput(recalled.run.issue_key);
      setSelection(null);
      if (writeUrl) updateUrl('run', uuid);
      return true;
    } catch {
      if (request === requestRef.current && !controller.signal.aborted) setError('Unable to select run. Previous selection preserved; try another run.');
      return false;
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }, [beginRead, data.tenant, selectionGuard, updateUrl]);

  const readIssue = useCallback(async (key: string, writeUrl = true) => {
    if (!key) return;
    const { request, controller } = beginRead();
    try {
      const graph = await getSubgraph(data.tenant, [key], controller.signal);
      if (request !== requestRef.current || controller.signal.aborted) return;
      selectionGuard.cancel();
      subgraphRef.current = graph;
      setView({ issueKey: key, runKey: null, run: null, subgraph: graph });
      setIssueInput(key); setSelection(null);
      if (writeUrl) updateUrl('issue', key);
    } catch {
      if (request === requestRef.current && !controller.signal.aborted) setError('Live data unavailable');
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }, [beginRead, data.tenant, selectionGuard, updateUrl]);
  const loadIssue = useCallback(() => { void readIssue(issueInput.trim()); }, [readIssue, issueInput]);

  // Native history changes do not re-fetch the Server Component. Incoming server
  // navigation and back/forward therefore both participate in the same guard.
  useEffect(() => {
    if (incomingRef.current !== data) {
      incomingRef.current = data;
      beginRead();
      selectionGuard.cancel();
      subgraphRef.current = data.subgraph;
      setView({ issueKey: data.issueKey, runKey: data.runKey ?? null, run: data.run ?? null, subgraph: data.subgraph });
      setIssueInput(data.issueKey ?? ''); setSelection(null); setError(data.error); setLoading(false);
      const allow = !searchParams.has('run') && !searchParams.has('issue') && !data.error;
      setAutoSelect(allow);
      navigationRef.current = search;
      return;
    }
    if (navigationRef.current === search) return;
    navigationRef.current = search;
    if (searchParams.has('run')) { void selectRun(searchParams.get('run') ?? '', false); }
    else if (searchParams.has('issue')) { void readIssue(searchParams.get('issue') ?? '', false); }
    else {
      beginRead();
      selectionGuard.cancel();
      const graph = { nodes: [], edges: [] };
      subgraphRef.current = graph;
      setView({ issueKey: null, runKey: null, run: null, subgraph: graph });
      setIssueInput(''); setSelection(null); setLoading(false);
      setAutoSelect(true);
    }
  }, [data, search, searchParams, beginRead, selectRun, readIssue, selectionGuard]);

  const selectListedRun = useCallback((key: string) => selectRun(key), [selectRun]);

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
      // Every view commit cancels the guard first, so a resolved result here
      // still belongs to the displayed view.
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
      <Header tenant={data.tenant} tenants={data.tenants} source="live" stats={stats} statsLoading={proofOpen && statsLoading} onOpenProof={() => setProofOpen(true)} />
      <ProofPanel open={proofOpen} onOpenChange={setProofOpen} stats={stats} loading={statsLoading} stale={Boolean(stats && statsError)} error={statsError} onRetry={() => refreshRef.current?.()} />
      <main className="workspace">
        {error && <p className="live-error-banner type-caption error-text" role="alert" data-live-error>{error}</p>}
        <LiveTriageCard recentRuns={<LiveRuns tenant={data.tenant} selectedRunKey={runKey} autoSelect={autoSelect} onSelect={selectListedRun} />} issueKey={issueKey} issueInput={issueInput} onIssueInputChange={setIssueInput} onLoadIssue={loadIssue} loading={loading} tenant={data.tenant} runKey={runKey} run={run} onSelectClaim={selectClaim} selectedEvidence={selectedEvidence} onApply={runKey ? (dryRun: boolean) => applyRun(data.tenant, runKey, dryRun) : undefined} />
        <GraphCanvas subgraph={subgraph} issueKey={issueKey} selection={selection} onNodeSelect={selectNode} onClearSelection={clearSelection} stats={stats} source="live" />
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
