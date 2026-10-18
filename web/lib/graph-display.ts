import type { GraphEdge, GraphKind, GraphNode, JsonValue, Subgraph } from './types';

/**
 * B5 Task 1 — pure display helpers.
 *
 * These helpers are the single authority for node shape, colour token and size,
 * and for turning the immutable wire `Subgraph` into a cloned, library-safe
 * `DisplayGraph`. They never touch the DOM, never read CSS and never invent a
 * product path; the renderer reads resolved colours and paints.
 */

export type NodeShape =
  | 'circle'
  | 'hexagon'
  | 'rounded-square'
  | 'ring'
  | 'notched-circle'
  | 'dot'
  | 'square'
  | 'diamond'
  | 'dashed-ring';

export type RadiusToken = 'sm' | 'md' | 'lg';

/** Show at most 150 nodes; past that, collapse Files into Modules and Chunks into Docs. */
export const NODE_LIMIT = 150;

/** `node-size-sm` / `node-size-md` / `node-size-lg` from design/tokens.json. */
export const RADIUS_PX: Record<RadiusToken, number> = { sm: 5, md: 8, lg: 12 };

export interface NodeStyle {
  shape: NodeShape;
  colorToken: string;
  radiusToken: RadiusToken;
}

const SHAPE_BY_KIND: Record<GraphKind, NodeShape> = {
  Person: 'circle',
  Team: 'hexagon',
  Repo: 'rounded-square',
  Module: 'rounded-square',
  File: 'rounded-square',
  Issue: 'ring',
  PR: 'notched-circle',
  Comment: 'dot',
  Label: 'square',
  Doc: 'diamond',
  Chunk: 'diamond',
  Decision: 'diamond',
  AgentRun: 'dashed-ring',
  Action: 'dashed-ring',
  Feedback: 'dashed-ring',
  Rule: 'dashed-ring',
};

const COLOR_BY_KIND: Record<GraphKind, string> = {
  Person: 'node-person',
  Team: 'node-team',
  Repo: 'node-code',
  Module: 'node-code',
  File: 'node-code',
  Issue: 'node-issue',
  PR: 'node-pr',
  Comment: 'node-pr',
  Label: 'node-code',
  Doc: 'node-knowledge',
  Chunk: 'node-knowledge',
  Decision: 'node-knowledge',
  AgentRun: 'node-memory',
  Action: 'node-memory',
  Feedback: 'node-memory',
  Rule: 'node-memory',
};

const LARGE_KINDS: ReadonlySet<GraphKind> = new Set<GraphKind>(['Team', 'Repo', 'Module']);
const SMALL_KINDS: ReadonlySet<GraphKind> = new Set<GraphKind>([
  'File',
  'Chunk',
  'Comment',
  'Label',
]);

/**
 * The one place shapes, colours and sizes are decided.
 *
 * `isIssue` is true only for the issue under triage, which alone is drawn at
 * `node-size-lg` and outlined at rest. All other Issue nodes stay medium.
 */
export function kindStyle(kind: GraphKind, isIssue: boolean): NodeStyle {
  let radiusToken: RadiusToken;
  if (LARGE_KINDS.has(kind)) radiusToken = 'lg';
  else if (SMALL_KINDS.has(kind)) radiusToken = 'sm';
  else radiusToken = 'md';
  if (isIssue && kind === 'Issue') radiusToken = 'lg';
  return { shape: SHAPE_BY_KIND[kind], colorToken: COLOR_BY_KIND[kind], radiusToken };
}

/** A wire node cloned into a mutable, library-safe node. */
export type DisplayNode = {
  key: string;
  kind: GraphKind;
  label: string;
  properties: Record<string, JsonValue>;
};

/** A wire edge cloned into a link whose endpoints stay stable string keys. */
export type DisplayLink = {
  id: string;
  source: string;
  target: string;
  type: string;
};

/**
 * The stable identity of a wire edge: its relationship type plus its *stored*
 * direction. `evidence-selection.pathEdgeIds` resolves a returned path segment
 * (which may run against the stored direction) to this same id, so the renderer
 * and the path matcher can never disagree about which segment is evidence.
 */
export function segmentId(edge: Pick<GraphEdge, 'type' | 'source' | 'target'>): string {
  return `${edge.type}:${edge.source}->${edge.target}`;
}

export interface DisplayGraph {
  nodeId: 'key';
  nodes: DisplayNode[];
  links: DisplayLink[];
  /** Node count before the cap and before any display-only collapse. */
  totalNodes: number;
  limit: number;
  truncated: boolean;
  collapsedFiles: number;
  collapsedChunks: number;
}

function cloneProperties(properties: Record<string, JsonValue>): Record<string, JsonValue> {
  return JSON.parse(JSON.stringify(properties ?? {})) as Record<string, JsonValue>;
}

function cloneNodes(nodes: GraphNode[]): DisplayNode[] {
  return nodes.map((node) => ({
    key: node.key,
    kind: node.kind,
    label: node.label,
    properties: cloneProperties(node.properties),
  }));
}

function cloneEdges(edges: GraphEdge[]): Omit<DisplayLink, 'id'>[] {
  return edges.map((edge) => ({ source: edge.source, target: edge.target, type: edge.type }));
}

/**
 * Clone the wire subgraph and apply the honest display cap.
 *
 * The library mutates the objects it is handed (positions, and link endpoints
 * become node objects), so every node, property bag and link is a fresh copy.
 * Past `NODE_LIMIT` nodes, File nodes contained by a present Module (`IN`) and
 * Chunk nodes contained by a present Doc (`HAS_CHUNK`) are collapsed away; if
 * no containment data exists the cap is kept rather than inventing edges.
 */
export function buildDisplayGraph(subgraph: Subgraph): DisplayGraph {
  const totalNodes = subgraph.nodes.length;
  let nodes = cloneNodes(subgraph.nodes);
  let edges = cloneEdges(subgraph.edges);
  let collapsedFiles = 0;
  let collapsedChunks = 0;

  if (nodes.length > NODE_LIMIT) {
    const kindByKey = new Map(nodes.map((node) => [node.key, node.kind]));
    const collapsedFileKeys = new Set<string>();
    const collapsedChunkKeys = new Set<string>();
    for (const edge of edges) {
      const sourceKind = kindByKey.get(edge.source);
      const targetKind = kindByKey.get(edge.target);
      if (edge.type === 'IN') {
        if (sourceKind === 'File' && targetKind === 'Module') collapsedFileKeys.add(edge.source);
        else if (sourceKind === 'Module' && targetKind === 'File')
          collapsedFileKeys.add(edge.target);
      } else if (edge.type === 'HAS_CHUNK') {
        if (sourceKind === 'Doc' && targetKind === 'Chunk') collapsedChunkKeys.add(edge.target);
        else if (sourceKind === 'Chunk' && targetKind === 'Doc')
          collapsedChunkKeys.add(edge.source);
      }
    }
    if (collapsedFileKeys.size || collapsedChunkKeys.size) {
      collapsedFiles = collapsedFileKeys.size;
      collapsedChunks = collapsedChunkKeys.size;
      nodes = nodes.filter(
        (node) => !collapsedFileKeys.has(node.key) && !collapsedChunkKeys.has(node.key),
      );
      edges = edges.filter(
        (edge) =>
          !collapsedFileKeys.has(edge.source) &&
          !collapsedFileKeys.has(edge.target) &&
          !collapsedChunkKeys.has(edge.source) &&
          !collapsedChunkKeys.has(edge.target),
      );
    }
  }

  const truncated = nodes.length > NODE_LIMIT;
  if (truncated) {
    const kept = new Set(nodes.slice(0, NODE_LIMIT).map((node) => node.key));
    nodes = nodes.filter((node) => kept.has(node.key));
  }

  const present = new Set(nodes.map((node) => node.key));
  // Exact duplicate edges are collapsed so every link id is unique and matches
  // the stable `segmentId` that `pathEdgeIds` returns.
  const seenLinks = new Set<string>();
  const links: DisplayLink[] = [];
  for (const edge of edges) {
    if (!present.has(edge.source) || !present.has(edge.target)) continue;
    const id = segmentId(edge);
    if (seenLinks.has(id)) continue;
    seenLinks.add(id);
    links.push({ id, source: edge.source, target: edge.target, type: edge.type });
  }

  return {
    nodeId: 'key',
    nodes,
    links,
    totalNodes,
    limit: NODE_LIMIT,
    truncated,
    collapsedFiles,
    collapsedChunks,
  };
}
