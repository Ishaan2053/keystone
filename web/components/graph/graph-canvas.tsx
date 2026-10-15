'use client';

import dynamic from 'next/dynamic';
import { Component, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  initialHoverKey?: string | null;
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

export function GraphCanvas({ subgraph, issueKey, selection, onNodeSelect, onClearSelection, stats = null, source = 'fixture', initialHoverKey = null }: GraphCanvasProps) {
  const display = useMemo(() => buildDisplayGraph(subgraph), [subgraph]);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [settled, setSettled] = useState(false);
  const [hoveredKey, setHoveredKey] = useState<string | null>(initialHoverKey ?? null);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [detailsKey, setDetailsKey] = useState<string | null>(null);
  const [pathReveal, setPathReveal] = useState(1);

  const isEmpty = display.nodes.length === 0;
  const live = source === 'live';

  // Evidence is only drawn for a resolved selection; loading/error show a
  // status without fabricating edges, and the trace stays visible regardless.
  const evidence = useMemo(() => {
    if (!selection || selection.state !== 'ready') return null;
    const edgeIds = [...pathEdgeIds(selection.paths, subgraph.edges)];
    // A failed lookup is unknown, never a negative result: no risk ring for it.
    const failed = new Set(selection.failedKeys);
    const riskKeys = (selection.riskKeys ?? []).filter(key => !failed.has(key));
    if (!selection.nodeKeys.length && !edgeIds.length && !riskKeys.length) return null;
    return { nodeKeys: [...selection.nodeKeys], edgeIds, riskKeys };
  }, [selection, subgraph.edges]);

  const evidenceEdges = evidence ? [...evidence.edgeIds].sort() : [];
  const evidenceNodes = evidence ? [...evidence.nodeKeys].sort() : [];
  const evidenceRisk = evidence ? [...evidence.riskKeys].sort() : [];
  // "No path found" only for citations whose lookup completed empty; a node
  // click or a fixture claim never looked a path up, and a failed request is
  // "Path unavailable", not absence.
  const noPath = selection?.state === 'ready' ? selection.noPathKeys : [];
  const failedPaths = selection?.state === 'ready' ? selection.failedKeys : [];

  // Clearing also drops the sticky node details and any hover emphasis.
  const clearSelection = useCallback(() => {
    setHoveredKey(null);
    setDetailsKey(null);
    onClearSelection?.();
  }, [onClearSelection]);

  // Escape clears the selection without trapping focus anywhere, unless another
  // layer (the trace sheet, the tenant select, a menu) already owns the key.
  useEffect(() => {
    if (!selection || !onClearSelection) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      const active = document.activeElement;
      if (active instanceof Element && active.closest('[role="dialog"]:not([data-graph-hover-card]), [role="alertdialog"], [role="listbox"], [role="menu"]')) return;
      clearSelection();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selection, onClearSelection, clearSelection]);

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
  const inspectedKey = hoveredKey ?? detailsKey ?? focusedKey;
  const inspectedNode = useMemo(() => display.nodes.find(node => node.key === inspectedKey) ?? null, [display.nodes, inspectedKey]);
  const inspectedEdges = useMemo(() => subgraph.edges.filter(edge => { const s = typeof edge.source === 'string' ? edge.source : (edge.source && typeof edge.source === 'object' && 'key' in edge.source ? (edge.source as { key?: string }).key : undefined); const t = typeof edge.target === 'string' ? edge.target : (edge.target && typeof edge.target === 'object' && 'key' in edge.target ? (edge.target as { key?: string }).key : undefined); return s === inspectedKey || t === inspectedKey; }), [subgraph.edges, inspectedKey]);

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
      data-path-reveal={pathReveal.toFixed(2)}    >
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
                  <ForceCanvas graph={display} issueKey={issueKey} width={size.width} height={size.height} evidence={evidence} hoverKey={hoveredKey} onNodeSelect={key => { setHoveredKey(key); setDetailsKey(key); onNodeSelect(key); }} onBackgroundClick={onClearSelection ? clearSelection : undefined} onNodeHover={setHoveredKey} onRevealChange={setPathReveal} onSettled={() => setSettled(true)} />
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
                {evidence && (
                  <li className="graph-legend-row type-caption" data-kind="Evidence" style={{ color: 'var(--evidence)' }}>
                    <ShapeGlyph shape="circle" />
                    <span className="muted-text">Evidence</span>
                  </li>
                )}
              </ul>
            </div>
            {inspectedNode && (
              <div className="graph-hover-card" data-graph-hover-card data-graph-node-card data-node-details data-graph-inspect role="dialog" aria-label="Node details">
                <p className="type-label muted-text">{inspectedNode.kind}</p>
                <p className="type-body-strong" data-node-label>{inspectedNode.label}</p>
                <dl>
                  {Object.entries(inspectedNode.properties ?? {}).slice(0, 3).map(([key, value]) => (
                    <div key={key}>
                      <dt className="type-caption muted-text">{key}</dt>
                      <dd className="type-mono-sm">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
                {(() => {
                  const link = Object.entries(inspectedNode.properties ?? {}).find(([, value]) => typeof value === 'string' && /^https?:\/\//.test(value))?.[1] as string | undefined;
                  return link ? <a href={link} target="_blank" rel="noopener noreferrer" className="type-caption muted-text">GitHub source</a> : null;
                })()}
                {inspectedEdges.length > 0 && (
                  <ul className="type-caption muted-text" data-node-edges>
                    {inspectedEdges.map((edge, index) => {
                      const s = typeof edge.source === 'string' ? edge.source : (edge.source as { key?: string })?.key;
                      const t = typeof edge.target === 'string' ? edge.target : (edge.target as { key?: string })?.key;
                      const direction = s === inspectedKey ? 'outgoing' : 'incoming';
                      return <li key={index}>{direction}: {edge.type} ({s} → {t})</li>;
                    })}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </div>
      <div className="canvas-selection">
        {live && (
          <>
            <span className="type-label">Selected issue</span>
            {issueKey ? <p className="type-mono-sm" data-issue-key>{issueKey}</p> : <p className="type-caption muted-text">No issue selected. Load an issue key to anchor the graph.</p>}
          </>
        )}
        <div className="canvas-selection-heading">
          <span className="type-label">Selected evidence</span>
          {selection && onClearSelection && (
            <Button variant="ghost" className="canvas-clear" onClick={clearSelection} aria-label="Clear selection"><X />Clear</Button>
          )}
        </div>
        {selection?.citationKeys.length
          ? selection.citationKeys.map(key => <p className="type-mono-sm evidence-text" key={key}>{key}</p>)
          : <p className="type-caption muted-text">Select a citation to inspect its supporting trace.</p>}
        {selection?.state === 'loading' && <p className="type-caption muted-text" role="status" data-evidence-loading>Resolving evidence path…</p>}
        {selection?.state === 'error' && <p className="type-caption error-text" role="alert" data-evidence-error>Path unavailable. Showing the graph without an evidence path.</p>}
        {failedPaths.length > 0 && <p className="type-caption error-text" data-path-failed>Path unavailable for {failedPaths.join(', ')}.</p>}
        {noPath.length > 0 && <p className="type-caption muted-text" data-no-path>No path found for {noPath.join(', ')}.</p>}
        {selection?.missingKeys.length ? <p className="type-caption error-text" data-missing-evidence>Missing evidence node: {selection.missingKeys.join(', ')}.</p> : null}
      </div>
      {!isEmpty && (
        <details className="graph-node-controls">
          <summary className="type-label">Nodes</summary>
          <ul className="graph-node-list" data-graph-node-list>
            {display.nodes.map(node => (
              <li key={node.key}>
                <button type="button" className="type-mono-sm" onClick={() => { setHoveredKey(null); setDetailsKey(node.key); onNodeSelect(node.key); }} onFocus={() => setFocusedKey(node.key)} onBlur={() => setFocusedKey(null)}>
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
