import { Network } from 'lucide-react';
import type { Subgraph } from '@/lib/types';
export function CanvasPlaceholder({ subgraph, selectedEvidence }: { subgraph: Subgraph; selectedEvidence: string[] }) {
  const kindCounts = subgraph.nodes.reduce<Record<string, number>>((acc, node) => {
    acc[node.kind] = (acc[node.kind] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <section className="panel canvas-panel" aria-label="Graph preview">
      <div className="canvas-heading"><h2 className="type-title">Evidence graph</h2><span className="type-caption muted-text">Fixture geometry</span></div>
      <div className="canvas-empty">
        <Network />
        <p>Graph preview — interactive canvas in B5</p>
        <p className="type-caption muted-text">{subgraph.nodes.length} fixture nodes · {subgraph.edges.length} fixture edges</p>
        <p className="type-caption muted-text">Placeholder only. No graph paths rendered.</p>
        <ul className="canvas-legend type-caption muted-text" aria-label="Fixture node counts">
          {Object.entries(kindCounts).map(([kind, count]) => <li key={kind}>{kind}: {count}</li>)}
        </ul>
      </div>
      <div className="canvas-selection">
        <span className="type-label">Selected evidence</span>
        {selectedEvidence.length ? selectedEvidence.map(k => <p className="type-mono-sm evidence-text" key={k}>{k}</p>) : <p className="type-caption muted-text">Select a citation to inspect its supporting trace.</p>}
      </div>
    </section>
  );
}
