'use client';

import type { Subgraph } from '@/lib/types';
import { GraphCanvas } from './graph-canvas';

/**
 * Test-only wrapper for `GraphCanvas`.
 *
 * A server page cannot pass an event handler to a client component, so this
 * thin client boundary supplies the no-op `onNodeSelect` used by the
 * `/harness/graph` route that the Task 1 Playwright spec drives.
 */
export function GraphCanvasHarness({ subgraph, issueKey }: { subgraph: Subgraph; issueKey: string | null }) {
  return <GraphCanvas subgraph={subgraph} issueKey={issueKey} selection={null} onNodeSelect={() => {}} />;
}
