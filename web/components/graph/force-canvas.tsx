'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from 'react-force-graph-2d';
import { kindStyle, type DisplayGraph, type DisplayLink, type DisplayNode, type NodeShape, type RadiusToken } from '@/lib/graph-display';

/**
 * B5 Task 1 — the browser-only force renderer.
 *
 * This module statically imports `react-force-graph-2d` (which needs `window`)
 * and is only ever reached through a `next/dynamic(..., { ssr: false })` import
 * from the client wrapper, so it never runs during SSR. The library mutates its
 * own node/link copies; the wire models are cloned in `buildDisplayGraph`.
 */

const MONO_FALLBACK = 'ui-monospace, SFMono-Regular, Menlo, monospace';
const MAX_LABELS = 30;

interface Palette {
  colors: Record<string, string>;
  radii: Record<RadiusToken, number>;
  edgeWidth: number;
  edgeWidthEvidence: number;
  opacityDim: number;
  opacityGhost: number;
  mono: string;
  canvas: string;
  foreground: string;
}

const FALLBACK: Palette = {
  colors: {
    'node-person': '#8fb4e8',
    'node-team': '#62c3b6',
    'node-code': '#a3abb5',
    'node-issue': '#7cc98e',
    'node-pr': '#b9a3e3',
    'node-knowledge': '#e59bbd',
    'node-memory': '#d9d4c7',
    'muted-foreground': '#9ba4ae',
    edge: '#3a4149',
    'edge-strong': '#646d77',
    evidence: '#f2b54a',
  },
  radii: { sm: 5, md: 8, lg: 12 },
  edgeWidth: 1,
  edgeWidthEvidence: 2.5,
  opacityDim: 0.22,
  opacityGhost: 0.5,
  mono: MONO_FALLBACK,
  canvas: '#0b0d0f',
  foreground: '#e7e9eb',
};

/**
 * Read resolved token values from the cascade. Canvas needs concrete colours,
 * never `var(...)` strings, so every token is read as its computed value and
 * re-read when the theme class changes.
 */
function readPalette(element: HTMLElement): Palette {
  const style = getComputedStyle(element);
  const raw = (name: string) => style.getPropertyValue(`--${name}`).trim();
  const number = (name: string, fallback: number) => {
    const parsed = Number.parseFloat(raw(name));
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const color = (name: string) => raw(name) || FALLBACK.colors[name] || FALLBACK.foreground;
  return {
    colors: {
      'node-person': color('node-person'),
      'node-team': color('node-team'),
      'node-code': color('node-code'),
      'node-issue': color('node-issue'),
      'node-pr': color('node-pr'),
      'node-knowledge': color('node-knowledge'),
      'node-memory': color('node-memory'),
      'muted-foreground': color('muted-foreground'),
      edge: color('edge'),
      'edge-strong': color('edge-strong'),
      evidence: color('evidence'),
    },
    radii: { sm: number('node-size-sm', 5), md: number('node-size-md', 8), lg: number('node-size-lg', 12) },
    edgeWidth: number('edge-width', 1),
    edgeWidthEvidence: number('edge-width-evidence', 2.5),
    opacityDim: number('opacity-dim', 0.22),
    opacityGhost: number('opacity-ghost', 0.5),
    mono: raw('font-mono') || MONO_FALLBACK,
    canvas: color('canvas'),
    foreground: color('foreground'),
  };
}

type RenderNode = DisplayNode & { x?: number; y?: number };

function polygon(ctx: CanvasRenderingContext2D, x: number, y: number, radius: number, sides: number, rotation: number) {
  ctx.beginPath();
  for (let i = 0; i < sides; i += 1) {
    const angle = rotation + (i * 2 * Math.PI) / sides;
    const px = x + radius * Math.cos(angle);
    const py = y + radius * Math.sin(angle);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
}

function paintShape(ctx: CanvasRenderingContext2D, shape: NodeShape, x: number, y: number, radius: number, color: string, strokeWidth: number) {
  switch (shape) {
    case 'circle':
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      break;
    case 'hexagon':
      polygon(ctx, x, y, radius, 6, Math.PI / 6);
      ctx.fillStyle = color;
      ctx.fill();
      break;
    case 'rounded-square':
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') ctx.roundRect(x - radius, y - radius, radius * 2, radius * 2, Math.min(3, radius * 0.5));
      else ctx.rect(x - radius, y - radius, radius * 2, radius * 2);
      ctx.fillStyle = color;
      ctx.fill();
      break;
    case 'ring':
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = strokeWidth;
      ctx.stroke();
      break;
    case 'notched-circle':
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.save();
      ctx.globalCompositeOperation = 'destination-out';
      ctx.beginPath();
      ctx.arc(x + radius * 0.62, y - radius * 0.62, radius * 0.42, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
      break;
    case 'dot':
      ctx.beginPath();
      ctx.arc(x, y, Math.max(2, radius * 0.6), 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      break;
    case 'square':
      ctx.fillStyle = color;
      ctx.fillRect(x - radius * 0.7, y - radius * 0.7, radius * 1.4, radius * 1.4);
      break;
    case 'diamond':
      polygon(ctx, x, y, radius * 1.15, 4, -Math.PI / 2);
      ctx.fillStyle = color;
      ctx.fill();
      break;
    case 'dashed-ring':
      ctx.save();
      ctx.setLineDash([3, 2]);
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = strokeWidth;
      ctx.stroke();
      ctx.restore();
      break;
  }
}

/** Resolve a link endpoint to its stable key even after the library objectifies it. */
function endpointKey(endpoint: unknown): string | undefined {
  if (typeof endpoint === 'string') return endpoint;
  if (endpoint && typeof endpoint === 'object' && 'key' in endpoint) {
    const key = (endpoint as { key?: unknown }).key;
    return typeof key === 'string' ? key : undefined;
  }
  return undefined;
}

type Methods = ForceGraphMethods<NodeObject<DisplayNode>, LinkObject<DisplayNode, DisplayLink>>;

export interface ForceCanvasProps {
  graph: DisplayGraph;
  issueKey: string | null;
  width: number;
  height: number;
  onNodeSelect?: (key: string) => void;
  onSettled?: () => void;
}

export default function ForceCanvas({ graph, issueKey, width, height, onNodeSelect, onSettled }: ForceCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<Methods | undefined>(undefined);
  const fittedRef = useRef(false);
  const [palette, setPalette] = useState<Palette>(FALLBACK);
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  // Resolved token colours, refreshed when the theme class changes.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const refresh = () => setPalette(readPalette(element));
    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  // Spread nodes out a little; bounded so tests settle quickly.
  useEffect(() => {
    const graph2d = fgRef.current;
    if (!graph2d) return;
    const charge = graph2d.d3Force('charge');
    if (charge && typeof charge.strength === 'function') charge.strength(-170);
    fittedRef.current = false;
  }, [graph]);

  const neighbours = useMemo(() => {
    const set = new Set<string>();
    if (!hoverKey) return set;
    set.add(hoverKey);
    for (const link of graph.links) {
      const source = endpointKey(link.source);
      const target = endpointKey(link.target);
      if (source === hoverKey && target) set.add(target);
      else if (target === hoverKey && source) set.add(source);
    }
    return set;
  }, [graph, hoverKey]);

  const labelKeys = useMemo(() => {
    const keys: string[] = [];
    for (const node of graph.nodes) {
      if (keys.length >= MAX_LABELS) break;
      if (kindStyle(node.kind, node.key === issueKey).radiusToken === 'lg') keys.push(node.key);
    }
    if (hoverKey && keys.length < MAX_LABELS && !keys.includes(hoverKey)) keys.push(hoverKey);
    return new Set(keys);
  }, [graph, issueKey, hoverKey]);

  const paintGround = useCallback(
    (ctx: CanvasRenderingContext2D) => {
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = palette.canvas;
      ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
      ctx.restore();
    },
    [palette.canvas],
  );

  const drawNode = useCallback(
    (node: RenderNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const style = kindStyle(node.kind, node.key === issueKey);
      const radius = palette.radii[style.radiusToken];
      const color = palette.colors[style.colorToken] ?? palette.foreground;
      const strokeWidth = 2 / globalScale;
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const dim = hoverKey !== null && !neighbours.has(node.key);
      const ghost = node.kind === 'Comment' ? 0.5 : 1;

      ctx.globalAlpha = (dim ? palette.opacityGhost : 1) * ghost;
      paintShape(ctx, style.shape, x, y, radius, color, strokeWidth);
      ctx.globalAlpha = 1;

      // The issue under triage alone carries a foreground outline at rest.
      if (node.key === issueKey) {
        ctx.beginPath();
        ctx.arc(x, y, radius + strokeWidth, 0, Math.PI * 2);
        ctx.strokeStyle = palette.foreground;
        ctx.lineWidth = strokeWidth;
        ctx.stroke();
      }

      if (labelKeys.has(node.key)) {
        ctx.font = `${12 / globalScale}px ${palette.mono}`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = palette.colors['muted-foreground'] ?? palette.foreground;
        ctx.fillText(node.label, x, y + radius + 2 / globalScale);
      }
    },
    [palette, issueKey, hoverKey, neighbours, labelKeys],
  );

  const handleEngineStop = useCallback(() => {
    if (!fittedRef.current) {
      fittedRef.current = true;
      const graph2d = fgRef.current;
      if (graph2d) {
        graph2d.zoomToFit(0, 40);
        const zoom = graph2d.zoom();
        if (zoom > 3) graph2d.zoom(3, 0);
        else if (zoom < 0.5) graph2d.zoom(0.5, 0);
      }
    }
    onSettled?.();
  }, [onSettled]);

  return (
    <div ref={containerRef} data-force-canvas className="graph-force-canvas">
      <ForceGraph2D<DisplayNode, DisplayLink>
        ref={fgRef}
        graphData={graph}
        nodeId="key"
        width={width}
        height={height}
        backgroundColor={palette.canvas}
        minZoom={0.2}
        maxZoom={8}
        warmupTicks={0}
        cooldownTicks={120}
        d3AlphaDecay={0.04}
        d3VelocityDecay={0.4}
        nodeRelSize={4}
        nodeCanvasObject={(node, ctx, scale) => drawNode(node as unknown as RenderNode, ctx, scale)}
        nodePointerAreaPaint={(node, color, ctx) => {
          const style = kindStyle((node as unknown as RenderNode).kind, (node as unknown as RenderNode).key === issueKey);
          const radius = palette.radii[style.radiusToken] + 2;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc((node as unknown as RenderNode).x ?? 0, (node as unknown as RenderNode).y ?? 0, radius, 0, Math.PI * 2);
          ctx.fill();
        }}
        linkColor={link => {
          const source = endpointKey(link.source);
          const target = endpointKey(link.target);
          if (hoverKey && (source === hoverKey || target === hoverKey)) return palette.colors['edge-strong'];
          return palette.colors.edge;
        }}
        linkWidth={() => palette.edgeWidth}
        linkLineDash={() => null}
        linkDirectionalArrowLength={0}
        onRenderFramePre={paintGround}
        onEngineStop={handleEngineStop}
        onNodeClick={node => {
          const key = node.key as string | undefined;
          if (key) onNodeSelect?.(key);
        }}
        onNodeHover={node => setHoverKey(node ? ((node.key as string | undefined) ?? null) : null)}
        enableNodeDrag
      />
    </div>
  );
}
