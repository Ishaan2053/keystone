'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  AskResult,
  Claim,
  FeedbackRequest,
  GraphNode,
  PathData,
  RecallRunData,
  Subgraph,
  ToolResult,
  TriageRequest,
} from '@/lib/types';
import { useSearchParams } from 'next/navigation';
import type { FixtureWorkspaceData, LiveWorkspaceData, WorkspaceData } from '@/lib/workspace-data';
import {
  ApiError,
  getStats,
  getSubgraph,
  getRun,
  runKeyUuid,
  applyRun,
  ask,
  isFullIssueKey,
  isIssueKey,
  postFeedback,
  triage,
  triageRequestFor,
} from '@/lib/api-client';
import { ProofPanel } from './proof-panel';
import { LiveRuns } from './live-runs';
import {
  citedKeys,
  createSelectionGuard,
  distinctKeys,
  nodeSelection,
  pathNodeKeys,
  resolveClaimContext,
  resolveUnanchoredContext,
  type EvidenceSelection,
} from '@/lib/evidence-selection';
import { Header } from './header';
import { TriageCard, LiveTriageCard } from './triage-card';
import { GraphCanvas } from './graph/graph-canvas';
import { TracePanel } from './trace-panel';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from './ui/sheet';
import { BookOpen, Terminal } from 'lucide-react';
import { LiveSidebar, FixtureSidebar } from './runs-sidebar';
import { openWelcome } from './welcome-dialog';

/** A tablet-width selection opens the trace drawer after highlighting. */
function onTablet(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(min-width: 640px) and (max-width: 1023px)').matches
  );
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
  return {
    claimId: id,
    citationKeys,
    paths,
    nodeKeys: pathNodeKeys(paths),
    missingKeys: [],
    state: 'ready',
    riskKeys: [],
    noPathKeys: [],
    failedKeys: [],
  };
}

/**
 * The request that re-triages a recorded run: its full issue key, else (a
 * raw-title run) the title and body it was triaged from, sent in this session
 * or recorded on the run (`request`). Null when neither is available, as for a
 * raw run recorded before runs kept their request.
 */
function retriageRequest(
  run: RecallRunData,
  sent: TriageRequest | undefined,
): TriageRequest | null {
  const key = run.run.issue_key.trim();
  if (isFullIssueKey(key)) return { issue_key: key };
  if (isIssueKey(key)) return null;
  if (sent?.title?.trim()) return { title: sent.title, body: sent.body ?? '' };
  const recorded = run.request;
  if (recorded?.title?.trim()) return { title: recorded.title, body: recorded.body ?? '' };
  return null;
}

/** Label colours from the graph's Label nodes (GitHub's own colours), by label key. */
function labelColorsFrom(nodes: GraphNode[]): Record<string, string> {
  const colors: Record<string, string> = {};
  for (const node of nodes) {
    const color = node.properties.color;
    if (node.kind === 'Label' && typeof color === 'string') colors[node.key] = color;
  }
  return colors;
}

const labelColorCache = new Map<string, string | null>();

/**
 * Reads the colour of each cited label once per tenant through the same
 * /subgraph route the canvas uses; a failed read just leaves the pill neutral.
 */
function useLabelColors(tenant: string, run: RecallRunData | null): Record<string, string> {
  const keys = useMemo(
    () => [
      ...new Set(
        (run?.triage.labels ?? []).flatMap((c) =>
          c.citations.filter((k) => k.startsWith('label:')),
        ),
      ),
    ],
    [run],
  );
  const [colors, setColors] = useState<Record<string, string>>({});
  useEffect(() => {
    const cached = () =>
      Object.fromEntries(
        keys.flatMap((k) => {
          const c = labelColorCache.get(`${tenant}|${k}`);
          return c ? [[k, c]] : [];
        }),
      );
    const missing = keys.filter((k) => !labelColorCache.has(`${tenant}|${k}`));
    setColors(cached());
    if (!missing.length) return;
    const controller = new AbortController();
    getSubgraph(tenant, missing, controller.signal)
      .then((graph) => {
        const found = labelColorsFrom(graph.nodes);
        for (const key of missing) labelColorCache.set(`${tenant}|${key}`, found[key] ?? null);
        setColors(cached());
      })
      .catch(() => {
        /* Neutral pills stay. */
      });
    return () => controller.abort();
  }, [tenant, keys]);
  return colors;
}

function triageLabel(body: TriageRequest): string {
  return body.issue_key ?? (body.issue_number ? `#${body.issue_number}` : 'this title');
}

/** A triage failure in the workspace's notice voice; the API's 409 detail names the matching issues. */
function triageError(failure: unknown): string {
  if (failure instanceof ApiError) {
    if (failure.status === 404) return "Triage failed: no such issue in this tenant's graph.";
    if (failure.status === 409) {
      const candidates = [...new Set(failure.detail?.match(/issue:[^\s,]+/g) ?? [])];
      return candidates.length
        ? `Triage failed: that number matches more than one repo. Enter the full key instead: ${candidates.join(', ')}.`
        : 'Triage failed: that number matches more than one repo. Enter the full key instead, e.g. issue:owner/repo#n.';
    }
    if (failure.status === 422) return 'Triage failed: enter an issue number or a nonblank title.';
    if (failure.status === 0 && failure.message.includes('timed out'))
      return 'Triage timed out. It may still be recorded; check Recent runs.';
  }
  return 'Triage failed: the live agent or graph did not answer. Try again.';
}

export function Workspace(data: WorkspaceData) {
  return data.source === 'live' ? (
    <LiveWorkspace key={data.tenant} data={data} />
  ) : (
    <FixtureWorkspace data={data} />
  );
}

function FixtureWorkspace({ data }: { data: FixtureWorkspaceData }) {
  const [proofOpen, setProofOpen] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [selection, setSelection] = useState<EvidenceSelection | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [askTrace, setAskTrace] = useState<ToolResult[]>([]);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Fixture Ask answers with the supplied `ask` fixture; nothing leaves the browser.
  const askFixture = useCallback(async () => structuredClone(data.ask), [data.ask]);
  const onAskResult = useCallback(
    (result: AskResult | null) => setAskTrace(result?.trace ?? []),
    [],
  );

  const selectedEvidence = selection?.citationKeys ?? [];
  const labelColors = useMemo(() => labelColorsFrom(data.subgraph.nodes), [data.subgraph]);
  const openTraceOnTablet = useCallback(() => {
    if (onTablet()) setTraceOpen(true);
  }, []);
  const selectClaim = useCallback(
    (id: string, claim: Claim) => {
      setSelection(fixtureClaimSelection(id, claim, data.path.data));
      openTraceOnTablet();
    },
    [data.path, openTraceOnTablet],
  );
  const selectNode = useCallback(
    (key: string) => {
      setSelection(nodeSelection(key));
      openTraceOnTablet();
    },
    [openTraceOnTablet],
  );
  const clearSelection = useCallback(() => setSelection(null), []);

  return (
    <>
      <Header
        tenant={data.tenant}
        tenants={data.tenants}
        stats={data.stats}
        onOpenProof={() => setProofOpen(true)}
      />
      <ProofPanel
        open={proofOpen}
        onOpenChange={setProofOpen}
        stats={data.stats}
        loading={false}
        stale={false}
        error={null}
        onRetry={() => {}}
        source="fixture"
      />
      <main className="workspace">
        <FixtureSidebar />
        <TriageCard
          triage={data.triage}
          dryRun={dryRun}
          onDryRunChange={setDryRun}
          selectedEvidence={selectedEvidence}
          onSelectClaim={selectClaim}
          applyPreview={data.applyPreview}
          applyRefused={data.applyRefused}
          onAsk={askFixture}
          onAskResult={onAskResult}
          labelColors={labelColors}
        />
        <div className="workspace-right">
          <GraphCanvas
            subgraph={data.subgraph}
            issueKey={null}
            selection={selection}
            onNodeSelect={selectNode}
            onClearSelection={clearSelection}
            source="fixture"
          />
          <section className="trace-inline">
            <TracePanel
              trace={data.triage.trace}
              askTrace={askTrace}
              selectedEvidence={selectedEvidence}
            />
          </section>
        </div>
        <div className="trace-drawer">
          <Button
            ref={triggerRef}
            variant="secondary"
            className="trace-drawer-trigger"
            onClick={() => setTraceOpen(true)}
            aria-label="Open tool trace"
          >
            <Terminal />
            Tool trace
          </Button>
          <Sheet
            open={traceOpen}
            onOpenChange={(open) => {
              setTraceOpen(open);
              if (!open) requestAnimationFrame(() => triggerRef.current?.focus());
            }}
          >
            <SheetContent className="trace-sheet">
              <SheetHeader>
                <SheetTitle>Tool trace</SheetTitle>
              </SheetHeader>
              <TracePanel
                trace={data.triage.trace}
                askTrace={askTrace}
                selectedEvidence={selectedEvidence}
              />
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
  const [statsError, setStatsError] = useState<string | null>(
    data.stats ? null : 'Unable to load stats. Try again.',
  );
  const refreshRef = useRef<(() => void) | null>(null);

  // Panel-owned reads never abort or participate in selected-run/graph requests.
  // Cleanup invalidates this entire open/tenant generation, including late replies.
  useEffect(() => {
    if (!proofOpen) return;
    let active = true;
    let inFlight = false;
    let controller: AbortController | null = null;
    // Only the opening read and Retry show loading; a background poll refreshes
    // silently, and its failure still marks the shown snapshot stale. Retry during
    // a poll shows loading until that poll settles.
    const refresh = async (showLoading: boolean) => {
      if (!active) return;
      if (showLoading) setStatsLoading(true);
      if (inFlight) return;
      inFlight = true;
      controller = new AbortController();
      try {
        const next = await getStats(data.tenant, controller.signal);
        if (active && !controller.signal.aborted) {
          setStats(next);
          setStatsError(null);
        }
      } catch {
        if (active && !controller.signal.aborted)
          setStatsError('Unable to refresh stats. Try again.');
      } finally {
        inFlight = false;
        if (active) setStatsLoading(false);
      }
    };
    refreshRef.current = () => {
      void refresh(true);
    };
    void refresh(true);
    const interval = setInterval(() => {
      void refresh(false);
    }, 5000);
    return () => {
      active = false;
      clearInterval(interval);
      controller?.abort();
      refreshRef.current = null;
    };
  }, [proofOpen, data.tenant]);
  const searchParams = useSearchParams();
  const search = searchParams.toString();
  const navigationRef = useRef(search);
  const incomingRef = useRef(data);
  const [issueInput, setIssueInput] = useState(data.issueKey ?? '');
  const [view, setView] = useState({
    issueKey: data.issueKey,
    runKey: data.runKey ?? null,
    run: data.run ?? null,
    subgraph: data.subgraph,
  });
  const { issueKey, runKey, run, subgraph } = view;
  const [autoSelect, setAutoSelect] = useState(
    !searchParams.has('run') &&
      !searchParams.has('issue') &&
      !data.issueKey &&
      !data.runKey &&
      !data.error,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(data.error);
  const [selection, setSelection] = useState<EvidenceSelection | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [askTrace, setAskTrace] = useState<ToolResult[]>([]);
  const [triaging, setTriaging] = useState(false);
  const [triageStatus, setTriageStatus] = useState('');
  const [runsRefresh, setRunsRefresh] = useState(0);
  const [issueHint, setIssueHint] = useState('');
  // Raw-title requests sent from this session, by run key, so Re-triage can repeat them.
  const sentRef = useRef(new Map<string, TriageRequest>());
  // Feedback lineage recorded in this session: the feedback key the API returned for the
  // exact run it was sent to, plus the view generation it was recorded in. Only an
  // explicit Re-triage of that same run reads it, so a pending, failed, stale or
  // reloaded session can never enrich another run, and nothing here is a "latest
  // feedback" value read from anywhere else.
  const feedbackRef = useRef<{
    generation: number;
    tenant: string;
    runKey: string;
    feedbackKey: string;
  } | null>(null);
  const triageRef = useRef(0);
  const triageAbortRef = useRef<AbortController | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const subgraphRef = useRef(subgraph);
  const selectionGuard = useMemo(() => createSelectionGuard(), []);
  const trace = useMemo(() => run?.triage.trace ?? [], [run]);
  const labelColors = useLabelColors(data.tenant, run);
  // The anchor's own node carries GitHub state and url; keep the last one seen
  // for this anchor, since a claim's merged subgraph may cap it out.
  const issueNodeRef = useRef<GraphNode | null>(null);
  const issueNode = useMemo(() => {
    const found = issueKey ? (subgraph.nodes.find((node) => node.key === issueKey) ?? null) : null;
    if (found) issueNodeRef.current = found;
    return found ?? (issueNodeRef.current?.key === issueKey ? issueNodeRef.current : null);
  }, [issueKey, subgraph]);
  const setSubgraph = useCallback((next: Subgraph) => {
    subgraphRef.current = next;
    setView((current) => ({ ...current, subgraph: next }));
  }, []);

  useEffect(() => {
    subgraphRef.current = subgraph;
  }, [subgraph]);
  useEffect(
    () => () => {
      requestRef.current += 1;
      abortRef.current?.abort();
      selectionGuard.cancel();
      triageRef.current += 1;
      triageAbortRef.current?.abort();
    },
    [selectionGuard],
  );

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

  // A user selection pushes a history entry; an automatic one replaces the bare
  // URL, so Back leaves the workspace instead of re-triggering auto-selection.
  const updateUrl = useCallback((kind: 'run' | 'issue', value: string, replace = false) => {
    const url = new URL(window.location.href);
    url.searchParams.set(kind, value);
    url.searchParams.delete(kind === 'run' ? 'issue' : 'run');
    navigationRef.current = url.searchParams.toString();
    if (url.href === window.location.href) return;
    if (replace) window.history.replaceState(null, '', url.toString());
    else window.history.pushState(null, '', url.toString());
  }, []);

  const selectRun = useCallback(
    async (key: string, writeUrl = true, replace = false): Promise<boolean> => {
      const { request, controller } = beginRead();
      try {
        const uuid = runKeyUuid(key);
        if (!uuid) throw new Error('Invalid run id');
        const result = await getRun(data.tenant, key, controller.signal);
        const recalled = result.data;
        if (
          result.empty ||
          !recalled ||
          recalled.run.run_key !== `run:${uuid}` ||
          recalled.triage?.run_key !== `run:${uuid}` ||
          !recalled.run.issue_key
        )
          throw new Error('Run not found or identity mismatch');
        // A raw-title run (`adhoc`) has no issue anchor: seed the canvas from its citations.
        const anchor = isIssueKey(recalled.run.issue_key) ? recalled.run.issue_key : null;
        const seed = anchor ? [anchor] : citedKeys(recalled.triage);
        const graph = seed.length
          ? await getSubgraph(data.tenant, seed, controller.signal)
          : { nodes: [], edges: [] };
        if (request !== requestRef.current || controller.signal.aborted) return false;
        selectionGuard.cancel();
        subgraphRef.current = graph;
        setView({ issueKey: anchor, runKey: `run:${uuid}`, run: recalled, subgraph: graph });
        if (anchor) setIssueInput(anchor);
        setIssueHint('');
        setSelection(null);
        if (writeUrl) updateUrl('run', uuid, replace);
        return true;
      } catch {
        if (request === requestRef.current && !controller.signal.aborted)
          setError('Unable to select run. Previous selection preserved; try another run.');
        return false;
      } finally {
        if (request === requestRef.current) setLoading(false);
      }
    },
    [beginRead, data.tenant, selectionGuard, updateUrl],
  );

  const readIssue = useCallback(
    async (key: string, writeUrl = true) => {
      if (!key) return;
      const { request, controller } = beginRead();
      try {
        const graph = await getSubgraph(data.tenant, [key], controller.signal);
        if (request !== requestRef.current || controller.signal.aborted) return;
        selectionGuard.cancel();
        subgraphRef.current = graph;
        setView({ issueKey: key, runKey: null, run: null, subgraph: graph });
        setIssueInput(key);
        setSelection(null);
        if (writeUrl) updateUrl('issue', key);
      } catch {
        if (request === requestRef.current && !controller.signal.aborted)
          setError('Live data unavailable');
      } finally {
        if (request === requestRef.current) setLoading(false);
      }
    },
    [beginRead, data.tenant, selectionGuard, updateUrl],
  );
  const loadIssue = useCallback(() => {
    const key = issueInput.trim();
    if (!isIssueKey(key)) {
      setIssueHint(
        'Load issue graph needs a full key, e.g. issue:owner/repo#123. Run triage also takes a number or a title.',
      );
      return;
    }
    setIssueHint('');
    void readIssue(key);
  }, [readIssue, issueInput]);
  const changeIssueInput = useCallback((value: string) => {
    setIssueInput(value);
    setIssueHint('');
  }, []);

  // Start a triage (no GitHub writes), then select the recorded run exactly as a
  // runs-list click or `?run=` link does, and refresh the runs list at once. Only
  // the newest triage may select its run; a superseded one is ignored.
  const runTriage = useCallback(
    async (body: TriageRequest) => {
      const label = triageLabel(body);
      const generation = ++triageRef.current;
      // Every run selection, issue load or navigation advances requestRef; a triage
      // selects its run only if none happened while it ran.
      const viewAtStart = requestRef.current;
      const feedbackAtStart = feedbackRef.current;
      triageAbortRef.current?.abort();
      const controller = new AbortController();
      triageAbortRef.current = controller;
      setTriaging(true);
      setError(null);
      setIssueHint('');
      setTriageStatus(`Running triage on ${label}. A live triage can take up to a minute.`);
      try {
        const result = await triage(data.tenant, body, controller.signal);
        if (generation !== triageRef.current) return;
        setRunsRefresh((current) => current + 1);
        const recorded = runKeyUuid(result.run_key);
        if (!recorded) throw new ApiError(0, 'triage returned no run key');
        if (body.title) sentRef.current.set(`run:${recorded}`, body);
        if (requestRef.current !== viewAtStart) {
          setTriageStatus(`Triage recorded as run:${recorded}. Open it from Recent runs.`);
          return;
        }
        // Consume only this request's context in its original view. A late completion
        // must not erase feedback saved after navigation (or while this call ran).
        if (feedbackRef.current === feedbackAtStart) feedbackRef.current = null;
        setTriageStatus('Triage recorded. Loading the run…');
        const selected = await selectRun(result.run_key);
        if (generation !== triageRef.current) return;
        setTriageStatus(
          selected
            ? ''
            : 'Triage recorded, but the run could not be loaded. Pick it from Recent runs.',
        );
      } catch (failure) {
        if (generation !== triageRef.current || controller.signal.aborted) return;
        setTriageStatus('');
        setError(triageError(failure));
      } finally {
        if (generation === triageRef.current) setTriaging(false);
      }
    },
    [data.tenant, selectRun],
  );

  const startTriage = useCallback(() => {
    const text = issueInput.trim();
    const body = triageRequestFor(text);
    if (!body) {
      setError(
        'Triage needs a full key, a number or a title: enter issue:owner/repo#123, #123, or an issue title.',
      );
      return;
    }
    void runTriage(body);
  }, [issueInput, runTriage]);

  const retriageBase = useMemo(
    () => (run ? retriageRequest(run, sentRef.current.get(run.run.run_key)) : null),
    [run],
  );
  // The lineage is read when Re-triage actually fires, not when the view rendered: the
  // context arrives with the feedback response and must never be cached into a stale body.
  const withFeedbackLineage = useCallback(
    (base: TriageRequest): TriageRequest => {
      const feedback = feedbackRef.current;
      const uuid = runKey ? runKeyUuid(runKey) : null;
      if (!base.issue_key || !uuid || !feedback) return base;
      // Only this exact full-issue run, in this tenant, still in the view generation it was
      // recorded in: a raw-title request or another run is never enriched.
      if (
        feedback.generation !== requestRef.current ||
        feedback.tenant !== data.tenant ||
        feedback.runKey !== runKey
      )
        return base;
      return { ...base, feedback_parent_run: uuid, feedback_key: feedback.feedbackKey };
    },
    [data.tenant, runKey],
  );
  const retriage = useMemo(
    () =>
      retriageBase
        ? () => {
            void runTriage(withFeedbackLineage(retriageBase));
          }
        : null,
    [retriageBase, withFeedbackLineage, runTriage],
  );
  const sendFeedback = useCallback(
    (body: FeedbackRequest) => {
      if (!runKey) return Promise.reject(new ApiError(400, 'no run selected'));
      const recorded = { generation: requestRef.current, tenant: data.tenant, runKey };
      return postFeedback(data.tenant, runKey, body).then((response) => {
        // Only a successful response for the run still on screen attaches lineage; a
        // failed or superseded reply is dropped instead of enriching another run.
        if (requestRef.current === recorded.generation) {
          feedbackRef.current = { ...recorded, feedbackKey: response.feedback_key };
        }
        return response;
      });
    },
    [data.tenant, runKey],
  );
  const askLive = useCallback((question: string) => ask(data.tenant, question), [data.tenant]);
  const onAskResult = useCallback(
    (result: AskResult | null) => setAskTrace(result?.trace ?? []),
    [],
  );

  // Native history changes do not re-fetch the Server Component. Incoming server
  // navigation and back/forward therefore both participate in the same guard.
  useEffect(() => {
    if (incomingRef.current !== data) {
      incomingRef.current = data;
      beginRead();
      selectionGuard.cancel();
      subgraphRef.current = data.subgraph;
      setView({
        issueKey: data.issueKey,
        runKey: data.runKey ?? null,
        run: data.run ?? null,
        subgraph: data.subgraph,
      });
      setIssueInput(data.issueKey ?? '');
      setSelection(null);
      setError(data.error);
      setLoading(false);
      const allow = !searchParams.has('run') && !searchParams.has('issue') && !data.error;
      setAutoSelect(allow);
      navigationRef.current = search;
      return;
    }
    if (navigationRef.current === search) return;
    navigationRef.current = search;
    if (searchParams.has('run')) {
      void selectRun(searchParams.get('run') ?? '', false);
    } else if (searchParams.has('issue')) {
      void readIssue(searchParams.get('issue') ?? '', false);
    } else {
      beginRead();
      selectionGuard.cancel();
      const graph = { nodes: [], edges: [] };
      subgraphRef.current = graph;
      setView({ issueKey: null, runKey: null, run: null, subgraph: graph });
      setIssueInput('');
      setSelection(null);
      setLoading(false);
      setAutoSelect(true);
    }
  }, [data, search, searchParams, beginRead, selectRun, readIssue, selectionGuard]);

  const selectListedRun = useCallback(
    (key: string, automatic = false) => selectRun(key, true, automatic),
    [selectRun],
  );

  const selectClaim = useCallback(
    (id: string, claim: Claim) => {
      const citationKeys = distinctKeys(claim.citations);
      setSelection({
        claimId: id,
        citationKeys,
        paths: [],
        nodeKeys: [],
        missingKeys: [],
        state: 'loading',
        riskKeys: [],
        noPathKeys: [],
        failedKeys: [],
      });
      // No issue anchor (a raw-title run, or no issue selected): no path can be
      // resolved, so the citations are shown as nodes instead.
      selectionGuard
        .run((signal) =>
          issueKey
            ? resolveClaimContext(
                data.tenant,
                issueKey,
                claim,
                id,
                () => subgraphRef.current,
                trace,
                signal,
              )
            : resolveUnanchoredContext(
                data.tenant,
                claim,
                id,
                () => subgraphRef.current,
                trace,
                signal,
              ),
        )
        // Every view commit cancels the guard first, so a resolved result here
        // still belongs to the displayed view.
        .then((result) => {
          if (!result) return;
          setSubgraph(result.graph);
          setSelection(result.next);
        })
        .catch(() => {
          setSelection((current) =>
            current && current.claimId === id ? { ...current, state: 'error' } : current,
          );
        });
    },
    [data.tenant, issueKey, selectionGuard, trace, setSubgraph],
  );

  const selectNode = useCallback(
    (key: string) => {
      selectionGuard.cancel();
      setSelection(nodeSelection(key));
      if (onTablet()) setTraceOpen(true);
    },
    [selectionGuard],
  );
  const clearSelection = useCallback(() => {
    selectionGuard.cancel();
    setSelection(null);
  }, [selectionGuard]);

  return (
    <>
      <Header
        tenant={data.tenant}
        tenants={data.tenants}
        source="live"
        stats={stats}
        statsLoading={proofOpen && statsLoading}
        onOpenProof={() => setProofOpen(true)}
      />
      <ProofPanel
        open={proofOpen}
        onOpenChange={setProofOpen}
        stats={stats}
        loading={statsLoading}
        stale={Boolean(stats && statsError)}
        error={statsError}
        onRetry={() => refreshRef.current?.()}
      />
      <main className={`workspace${error ? ' has-banner' : ''}`}>
        {error && (
          <p className="live-error-banner type-caption error-text" role="alert" data-live-error>
            {error}
          </p>
        )}
        <LiveSidebar
          issueInput={issueInput}
          onIssueInputChange={changeIssueInput}
          onTriage={startTriage}
          onLoadIssue={loadIssue}
          loading={loading}
          triaging={triaging}
          triageStatus={triageStatus}
          issueHint={issueHint}
          recentRuns={
            <LiveRuns
              tenant={data.tenant}
              selectedRunKey={runKey}
              autoSelect={autoSelect}
              onSelect={selectListedRun}
              refreshKey={runsRefresh}
            />
          }
        />
        <LiveTriageCard
          issueKey={issueKey}
          tenant={data.tenant}
          runKey={runKey}
          run={run}
          issueNode={issueNode}
          labelColors={labelColors}
          onSelectClaim={selectClaim}
          selectedEvidence={selectedEvidence}
          onApply={runKey ? (dryRun: boolean) => applyRun(data.tenant, runKey, dryRun) : undefined}
          triaging={triaging}
          onFeedback={sendFeedback}
          onRetriage={retriage}
          onAsk={askLive}
          onAskResult={onAskResult}
          emptyActions={
            <Button type="button" variant="secondary" onClick={openWelcome}>
              <BookOpen />
              How Keystone works
            </Button>
          }
        />
        <div className="workspace-right">
          <GraphCanvas
            subgraph={subgraph}
            issueKey={issueKey}
            selection={selection}
            onNodeSelect={selectNode}
            onClearSelection={clearSelection}
            stats={stats}
            source="live"
          />
          <section className="trace-inline">
            <TracePanel
              trace={trace}
              askTrace={askTrace}
              selectedEvidence={selectedEvidence}
              source="live"
            />
          </section>
        </div>
        <div className="trace-drawer">
          <Button
            ref={triggerRef}
            variant="secondary"
            className="trace-drawer-trigger"
            onClick={() => setTraceOpen(true)}
            aria-label="Open tool trace"
          >
            <Terminal />
            Tool trace
          </Button>
          <Sheet
            open={traceOpen}
            onOpenChange={(open) => {
              setTraceOpen(open);
              if (!open) requestAnimationFrame(() => triggerRef.current?.focus());
            }}
          >
            <SheetContent className="trace-sheet">
              <SheetHeader>
                <SheetTitle>Tool trace</SheetTitle>
              </SheetHeader>
              <TracePanel
                trace={trace}
                askTrace={askTrace}
                selectedEvidence={selectedEvidence}
                source="live"
              />
            </SheetContent>
          </Sheet>
        </div>
      </main>
    </>
  );
}
