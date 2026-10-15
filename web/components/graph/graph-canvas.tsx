'use client';

import dynamic from 'next/dynamic';
import { Component, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { Network, X } from 'lucide-react';
import type { GraphKind, Stats, Subgraph } from '@/lib/types';
import type { WorkspaceSource } from '@/lib/workspace-data';
import { buildDisplayGraph, kindStyle, type DisplayGraph, type NodeShape } from '@/lib/graph-display';
import { pathEdgeIds, type EvidenceSelection } from '@/lib/evidence-selection';
import { Button } from '@/components/ui/button';

/**
 * B5 Task 1 — client wrapper.
 *
 * Owns measurement (`ResizeObserver`), the SSR-disabled dynamic import of the
 * browser-only renderer, loading/error states, the accessible node controls and
 * the legend. It never imports the force library directly, so the server bundle
 * stays clean.
 *
 * The renderer is loaded with `ssr: false` from this client component (see the
 * installed Next.js "Lazy Loading" guide); `ssr: false` is not allowed from a
 * server component.
 */
const ForceCanvas = dynamic(() => import('./force-canvas'), {
  ssr: false,
  loading: () => (
    <p className="type-caption muted-text graph-canvas-status" role="status">
      Loading graph…
    </p>
  ),
});

export interface GraphCanvasProps {
  subgraph: Subgraph;
  issueKey: string | null;
  selection: EvidenceSelection | null;
  onNodeSelect: (key: string) => void;
  /** Clears the selection from the canvas (blank click, Escape or the button). */
  onClearSelection?: () => void;
  /** Live-only summary counts (B4) shown above the stage. */
  stats?: Stats | null;
  source?: WorkspaceSource;
}

class GraphErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <p className="type-caption error-text graph-canvas-status" role="alert" data-graph-error>
          The graph renderer failed to load.
        </p>
      );
    }
    return this.props.children;
  }
}

function ShapeGlyph({ shape }: { shape: NodeShape }) {
  const common = { fill: 'currentColor' } as const;
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" data-shape={shape}>
      {shape === 'circle' && <circle cx="6" cy="6" r="4" {...common} />}
      {shape === 'hexagon' && <polygon points="6,1.5 10.5,4 10.5,8 6,10.5 1.5,8 1.5,4" {...common} />}
      {shape === 'rounded-square' && <rect x="2" y="2" width="8" height="8" rx="2" {...common} />}
      {shape === 'ring' && <circle cx="6" cy="6" r="4" fill="none" stroke="currentColor" strokeWidth="1.5" />}
      {shape === 'notched-circle' && (
        <>
          <circle cx="6" cy="6" r="4" {...common} />
          <circle cx="9.2" cy="2.8" r="1.7" fill="var(--card)" />
        </>
      )}
      {shape === 'dot' && <circle cx="6" cy="6" r="2" {...common} />}
      {shape === 'square' && <rect x="2.5" y="2.5" width="7" height="7" {...common} />}
      {shape === 'diamond' && <polygon points="6,1.5 10.5,6 6,10.5 1.5,6" {...common} />}
      {shape === 'dashed-ring' && <circle cx="6" cy="6" r="4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 1.5" />}
    </svg>
  );
}

function visibleKinds(graph: DisplayGraph): GraphKind[] {
  const seen: GraphKind[] = [];
  for (const node of graph.nodes) {
    if (!seen.includes(node.kind)) seen.push(node.kind);
  }
  return seen;
}

export function GraphCanvas({ subgraph, issueKey, selection, onNodeSelect, onClearSelection, stats = null, source = 'fixture' }: GraphCanvasProps) {
  const display = useMemo(() => buildDisplayGraph(subgraph), [subgraph]);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [settled, setSettled] = useState(false);

  const isEmpty = display.nodes.length === 0;
  const live = source === 'live';

  // Evidence is only drawn for a resolved selection; loading/error show a
  // status without fabricating edges, and the trace stays visible regardless.
  const evidence = useMemo(() => {
    if (!selection || selection.state !== 'ready') return null;
    const edgeIds = [...pathEdgeIds(selection.paths, subgraph.edges)];
    const riskKeys = selection.riskKeys ?? [];
    if (!selection.nodeKeys.length && !edgeIds.length && !riskKeys.length) return null;
    return { nodeKeys: [...selection.nodeKeys], edgeIds, riskKeys };
  }, [selection, subgraph.edges]);

  const evidenceEdges = evidence ? [...evidence.edgeIds].sort() : [];
  const evidenceNodes = evidence ? [...evidence.nodeKeys].sort() : [];
  const evidenceRisk = evidence ? [...evidence.riskKeys].sort() : [];
  const reachedKeys = useMemo(() => new Set(selection?.paths.flatMap(path => path.keys) ?? []), [selection]);
  const unreached = selection?.state === 'ready' ? selection.citationKeys.filter(key => !reachedKeys.has(key)) : [];

  // Escape clears the selection without trapping focus anywhere.
  useEffect(() => {
    if (!selection || !onClearSelection) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClearSelection(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selection, onClearSelection]);

  const liveCounts = live && stats ? Object.entries(stats.node_counts).map(([kind, count]) => `${kind}: ${count}`).join(' · ') : '';

  // Measure the stage and follow container resizes; the observer is cleaned up
  // on unmount and whenever the stage is remounted (empty <-> non-empty).
  useEffect(() => {
    if (isEmpty) return;
    const element = stageRef.current;
    if (!element) return;
    const update = () => {
      const rect = element.getBoundingClientRect();
      setSize(current => {
        const width = Math.round(rect.width);
        const height = Math.round(rect.height);
        return current.width === width && current.height === height ? current : { width, height };
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [isEmpty]);

  useEffect(() => {
    setSettled(false);
  }, [display]);

  const ready = !isEmpty && size.width > 0 && size.height > 0;
  const kinds = useMemo(() => visibleKinds(display), [display]);

  return (
    <section
      className="panel canvas-panel graph-canvas"
      aria-label="Evidence graph"
      data-graph-ready={ready ? 'true' : 'false'}
      data-graph-settled={settled ? 'true' : 'false'}
      data-graph-truncated={display.truncated ? 'true' : 'false'}
      data-canvas-width={size.width}
      data-evidence-state={selection?.state ?? 'none'}
      data-evidence-nodes={JSON.stringify(evidenceNodes)}
      data-evidence-edges={JSON.stringify(evidenceEdges)}
      data-evidence-risk={JSON.stringify(evidenceRisk)}
    >
      <div className="canvas-heading">
        <h2 className="type-title">Evidence graph</h2>
        <span className="type-caption muted-text">
          {display.nodes.length} {display.nodes.length === 1 ? 'node' : 'nodes'} · {display.links.length} {display.links.length === 1 ? 'edge' : 'edges'}
        </span>
      </div>
      {live && <p className="type-caption muted-text canvas-counts">{subgraph.nodes.length} live nodes · {subgraph.edges.length} live edges{liveCounts ? ` · ${liveCounts}` : ''}</p>}
      {display.truncated && (
        <p className="type-caption muted-text graph-limit-notice" data-graph-limit-notice>
          Showing {display.limit} of {display.totalNodes} nodes.
        </p>
      )}
      <div className="graph-canvas-body">
        {isEmpty ? (
          <div className="canvas-empty graph-canvas-empty" data-graph-empty>
            <Network />
            <p>No graph nodes</p>
            <p className="type-caption muted-text">This neighbourhood has no nodes to draw.</p>
          </div>
        ) : (
          <>
            <div className="graph-canvas-stage" data-graph-stage ref={stageRef}>
              <GraphErrorBoundary>
                {ready ? (
                  <ForceCanvas graph={display} issueKey={issueKey} width={size.width} height={size.height} evidence={evidence} onNodeSelect={onNodeSelect} onBackgroundClick={onClearSelection} onSettled={() => setSettled(true)} />
                ) : null}
              </GraphErrorBoundary>
            </div>
            <div className="graph-canvas-legend" data-graph-legend aria-label="Node kinds on screen">
              <ul>
                {kinds.map(kind => {
                  const style = kindStyle(kind, false);
                  return (
                    <li key={kind} className="graph-legend-row type-caption" data-kind={kind} style={{ color: `var(--${style.colorToken})` }}>
                      <ShapeGlyph shape={style.shape} />
                      <span className="muted-text">{kind}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          </>
        )}
      </div>
      <div className="canvas-selection">
        {live && (
          <>
            <span className="type-label">Selected issue</span>
            {issueKey ? <p className="type-mono-sm evidence-text" data-issue-key>{issueKey}</p> : <p className="type-caption muted-text">No issue selected. Load an issue key to anchor the graph.</p>}
          </>
        )}
        <div className="canvas-selection-heading">
          <span className="type-label">Selected evidence</span>
          {selection && onClearSelection && (
            <Button variant="ghost" className="canvas-clear" onClick={onClearSelection} aria-label="Clear selection"><X />Clear</Button>
          )}
        </div>
        {selection?.citationKeys.length
          ? selection.citationKeys.map(key => <p className="type-mono-sm evidence-text" key={key}>{key}</p>)
          : <p className="type-caption muted-text">Select a citation to inspect its supporting trace.</p>}
        {selection?.state === 'loading' && <p className="type-caption muted-text" role="status" data-evidence-loading>Resolving evidence path…</p>}
        {selection?.state === 'error' && <p className="type-caption error-text" role="alert" data-evidence-error>Path unavailable. Showing the graph without an evidence path.</p>}
        {selection && unreached.length > 0 && <p className="type-caption muted-text" data-no-path>No path found for {unreached.join(', ')}.</p>}
        {selection?.missingKeys.length ? <p className="type-caption error-text" data-missing-evidence>Missing evidence node: {selection.missingKeys.join(', ')}.</p> : null}
      </div>
      {!isEmpty && (
        <details className="graph-node-controls">
          <summary className="type-label">Nodes</summary>
          <ul className="graph-node-list" data-graph-node-list>
            {display.nodes.map(node => (
              <li key={node.key}>
                <button type="button" className="type-mono-sm" onClick={() => onNodeSelect(node.key)}>
                  {node.label}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
