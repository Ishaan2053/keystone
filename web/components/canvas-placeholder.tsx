import { Network } from 'lucide-react';
import type { Stats, Subgraph } from '@/lib/types';
import type { WorkspaceSource } from '@/lib/workspace-data';
export function CanvasPlaceholder({ subgraph, selectedEvidence, stats = null, issueKey = null, source = 'fixture' }: { subgraph: Subgraph; selectedEvidence: string[]; stats?: Stats | null; issueKey?: string | null; source?: WorkspaceSource }) {
  const live = source === 'live';
  const kindCounts = subgraph.nodes.reduce<Record<string, number>>((acc, node) => {
    acc[node.kind] = (acc[node.kind] ?? 0) + 1;
    return acc;
  }, {});
  const liveCounts = stats ? Object.entries(stats.node_counts).map(([kind, count]) => `${kind}: ${count}`).join(' · ') : '';
  return (
    <section className="panel canvas-panel" aria-label="Graph preview">
      <div className="canvas-heading"><h2 className="type-title">Evidence graph</h2><span className="type-caption muted-text">{live ? 'Live neighbourhood' : 'Fixture geometry'}</span></div>
      <div className="canvas-empty">
        <Network />
        <p>Graph preview — interactive canvas in B5</p>
        {live
          ? <p className="type-caption muted-text canvas-counts">{subgraph.nodes.length} live nodes · {subgraph.edges.length} live edges{liveCounts ? ` · ${liveCounts}` : ''}</p>
          : <p className="type-caption muted-text">{subgraph.nodes.length} fixture nodes · {subgraph.edges.length} fixture edges</p>}
        <p className="type-caption muted-text">Placeholder only. No graph paths rendered.</p>
        <ul className="canvas-legend type-caption muted-text" aria-label={live ? 'Live node counts' : 'Fixture node counts'}>
          {Object.entries(kindCounts).map(([kind, count]) => <li key={kind}>{kind}: {count}</li>)}
        </ul>
      </div>
      <div className="canvas-selection">
        {live && <><span className="type-label">Selected issue</span>{issueKey ? <p className="type-mono-sm evidence-text" data-issue-key>{issueKey}</p> : <p className="type-caption muted-text">No issue selected. Load an issue key to anchor the graph.</p>}</>}
        <span className="type-label">Selected evidence</span>
        {selectedEvidence.length ? selectedEvidence.map(k => <p className="type-mono-sm evidence-text" key={k}>{k}</p>) : <p className="type-caption muted-text">Select a citation to inspect its supporting trace.</p>}
      </div>
    </section>
  );
}
