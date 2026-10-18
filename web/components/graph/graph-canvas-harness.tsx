'use client';

import type { Subgraph } from '@/lib/types';
import type { EvidenceSelection } from '@/lib/evidence-selection';
import { GraphCanvas } from './graph-canvas';

/**
 * Test-only wrapper for `GraphCanvas`.
 *
 * A server page cannot pass an event handler to a client component, so this
 * thin client boundary supplies the callbacks used by the `/harness/graph`
 * route that the B5 Playwright spec drives.
 */
export function GraphCanvasHarness({
  subgraph,
  issueKey,
  selection = null,
  initialHoverKey = null,
}: {
  subgraph: Subgraph;
  issueKey: string | null;
  selection?: EvidenceSelection | null;
  initialHoverKey?: string | null;
}) {
  return (
    <GraphCanvas
      subgraph={subgraph}
      issueKey={issueKey}
      selection={selection}
      initialHoverKey={initialHoverKey}
      onNodeSelect={() => {}}
      onClearSelection={() => {}}
    />
  );
}
