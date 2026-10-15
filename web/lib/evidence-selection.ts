import { getPath } from './api-client';
import { segmentId } from './graph-display';
import type { Claim, GraphEdge, GraphNode, PathData, Subgraph, ToolResult } from './types';

/**
 * B5 Task 2 — claim ↔ path ↔ node selection.
 *
 * Pure types and helpers that turn a selected claim into exactly the graph
 * evidence the API returned. Nothing here invents a path: an empty `/path`
 * result stays empty, a missing node stays missing, and a negative result is
 * only recognised when a trace entry proves it.
 */

export type EvidenceState = 'loading' | 'ready' | 'error';

export interface EvidenceSelection {
  claimId: string | null;
  citationKeys: string[];
  paths: PathData[];
  nodeKeys: string[];
  missingKeys: string[];
  state: EvidenceState;
  /**
   * Additive to the plan's shape: nodes whose absence the trace supports (an
   * empty tool result that names the module). The renderer draws these with a
   * dashed `risk` ring; the resolver cannot compute them because it never sees
   * the trace, so the workspace classifies and passes them.
   */
  riskKeys?: string[];
}

/** Claim identity is local to the run and resets with tenant/run. */
export function claimId(section: string, index: number): string {
  return `${section}:${index}`;
}

/** Order-preserving de-duplication of evidence keys. */
export function distinctKeys(keys: string[]): string[] {
  const out: string[] = [];
  for (const key of keys) if (!out.includes(key)) out.push(key);
  return out;
}

/** Every node that appears on a returned path, in first-seen order. */
export function pathNodeKeys(paths: PathData[]): string[] {
  const out: string[] = [];
  for (const path of paths) for (const key of path.keys) if (!out.includes(key)) out.push(key);
  return out;
}

/**
 * The exact wire edges a returned path traverses.
 *
 * A segment matches only when the adjacent stable keys *and* the relationship
 * type agree, so a parallel edge of another type between the same endpoints is
 * never highlighted. Traversal may run against the stored direction, but the id
 * returned always keeps the stored direction.
 */
export function pathEdgeIds(paths: PathData[], edges: GraphEdge[]): Set<string> {
  const ids = new Set<string>();
  for (const path of paths) {
    for (let i = 0; i + 1 < path.keys.length; i += 1) {
      const from = path.keys[i];
      const to = path.keys[i + 1];
      const rel = path.rels[i];
      if (typeof rel !== 'string') continue;
      for (const edge of edges) {
        if (edge.type !== rel) continue;
        const forward = edge.source === from && edge.target === to;
        const reverse = edge.source === to && edge.target === from;
        if (forward || reverse) ids.add(segmentId(edge));
      }
    }
  }
  return ids;
}

/**
 * Citations whose absence the trace actually supports.
 *
 * A negative result requires a supporting trace entry with `empty: true` that
 * references the module either through its `evidence` keys or through the tool's
 * `module` parameter. Prose is never classified, and a citation that is not a
 * graph node is never ringed.
 */
export function supportedAbsenceKeys(
  trace: ToolResult[],
  citationKeys: string[],
  graphNodeKeys: Set<string>,
): string[] {
  const citations = new Set(citationKeys);
  const out: string[] = [];
  for (const tool of trace) {
    if (!tool || tool.empty !== true) continue;
    const referenced = new Set<string>(Array.isArray(tool.evidence) ? tool.evidence : []);
    const moduleParam = tool.params && typeof tool.params.module === 'string' ? tool.params.module : null;
    if (moduleParam) referenced.add(moduleParam);
    for (const key of citations) {
      if (!graphNodeKeys.has(key)) continue;
      if (referenced.has(key) && !out.includes(key)) out.push(key);
    }
  }
  return out;
}

/**
 * Union of the evidence paths from the selected issue to each distinct citation.
 *
 * One `getPath` call per distinct citation. A citation equal to the issue is one
 * node with zero edges. An empty (or single-key) tool result contributes no path
 * — absence is preserved, never replaced by a guessed connection.
 */
export async function resolveClaimEvidence(
  tenant: string,
  issueKey: string,
  claim: Claim,
  signal: AbortSignal,
): Promise<EvidenceSelection> {
  const citationKeys = distinctKeys(claim.citations);
  const paths: PathData[] = [];
  let attempts = 0;
  let failures = 0;

  for (const key of citationKeys) {
    if (key === issueKey) {
      paths.push({ keys: [issueKey], rels: [] });
      continue;
    }
    attempts += 1;
    try {
      const result = await getPath(tenant, issueKey, key, signal);
      if (result && result.empty !== true && result.data && Array.isArray(result.data.keys) && result.data.keys.length) {
        paths.push({ keys: result.data.keys, rels: Array.isArray(result.data.rels) ? result.data.rels : [] });
      }
    } catch (error) {
      if (signal.aborted) throw error;
      failures += 1;
    }
  }

  const state: EvidenceState = attempts > 0 && failures === attempts ? 'error' : 'ready';
  return { claimId: null, citationKeys, paths, nodeKeys: pathNodeKeys(paths), missingKeys: [], state, riskKeys: [] };
}

/** A selection for a node the user clicked directly: that key is the evidence. */
export function nodeSelection(key: string): EvidenceSelection {
  return { claimId: null, citationKeys: [key], paths: [], nodeKeys: [key], missingKeys: [], state: 'ready', riskKeys: [] };
}

/**
 * Merge an extra neighbourhood into the current graph, keeping the prioritised
 * keys first and closing over edge endpoints. The 150-node cap is applied after
 * prioritisation, so a requested issue/path/citation is never dropped for an
 * unrelated neighbour.
 */
export function mergeSubgraph(base: Subgraph, extra: Subgraph, priorityKeys: string[], limit = 150): Subgraph {
  const nodeByKey = new Map<string, GraphNode>();
  for (const node of base.nodes) if (!nodeByKey.has(node.key)) nodeByKey.set(node.key, node);
  for (const node of extra.nodes) if (!nodeByKey.has(node.key)) nodeByKey.set(node.key, node);

  const order: string[] = [];
  const seen = new Set<string>();
  const add = (key: string) => {
    if (!nodeByKey.has(key) || seen.has(key)) return;
    seen.add(key);
    order.push(key);
  };
  for (const key of priorityKeys) add(key);
  for (const node of base.nodes) add(node.key);
  for (const node of extra.nodes) add(node.key);

  const kept = order.slice(0, limit);
  const keptSet = new Set(kept);
  const nodes = kept.map(key => nodeByKey.get(key) as GraphNode);

  const edgeIds = new Set<string>();
  const edges: GraphEdge[] = [];
  for (const edge of [...base.edges, ...extra.edges]) {
    if (!keptSet.has(edge.source) || !keptSet.has(edge.target)) continue;
    const id = segmentId(edge);
    if (edgeIds.has(id)) continue;
    edgeIds.add(id);
    edges.push(edge);
  }
  return { nodes, edges };
}

export interface SelectionGuard {
  /** Run a task; the result is returned only when it is still the newest run. */
  run<T>(task: (signal: AbortSignal) => Promise<T>): Promise<T | null>;
  /** Invalidate any in-flight run and abort it. */
  cancel(): void;
}

/**
 * A monotonic token plus an `AbortController`: a superseded response can never
 * replace a newer selection, and a newer selection cancels the older request.
 */
export function createSelectionGuard(): SelectionGuard {
  let current = 0;
  let controller: AbortController | null = null;
  return {
    async run<T>(task: (signal: AbortSignal) => Promise<T>): Promise<T | null> {
      const token = current + 1;
      current = token;
      controller?.abort();
      const next = new AbortController();
      controller = next;
      try {
        const value = await task(next.signal);
        return token === current ? value : null;
      } catch (error) {
        if (token !== current || next.signal.aborted) return null;
        throw error;
      }
    },
    cancel() {
      current += 1;
      controller?.abort();
      controller = null;
    },
  };
}
