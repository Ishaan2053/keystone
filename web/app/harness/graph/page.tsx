import { notFound } from 'next/navigation';
import type { GraphEdge, GraphKind, GraphNode, Subgraph } from '@/lib/types';
import { GraphCanvasHarness } from '@/components/graph/graph-canvas-harness';

/**
 * Test-only harness for B5 Task 1.
 *
 * The force renderer is not wired into `Workspace` until B5 Task 2, so the
 * Task 1 Playwright spec drives `GraphCanvas` through this route. It mirrors the
 * wire subgraph shape without importing files outside `web/`, and is not
 * available in a production build.
 */

const ALL_KINDS: GraphKind[] = [
  'Person',
  'Team',
  'Repo',
  'Module',
  'File',
  'Issue',
  'PR',
  'Comment',
  'Label',
  'Doc',
  'Chunk',
  'Decision',
  'AgentRun',
  'Action',
  'Feedback',
  'Rule',
];

/** Mirrors `api/fixtures/subgraph.json`: eight nodes across seven kinds. */
function fixtureSubgraph(): { subgraph: Subgraph; issueKey: string } {
  const issueKey = 'issue:FalkorDB/FalkorDB#fixture-demo';
  const nodes: GraphNode[] = [
    { key: 'repo:FalkorDB/FalkorDB', kind: 'Repo', label: 'FalkorDB/FalkorDB', properties: { name: 'FalkorDB' } },
    { key: 'doc:FalkorDB/FalkorDB/README.md', kind: 'Doc', label: 'README.md', properties: { path: 'README.md' } },
    { key: 'module:FalkorDB/FalkorDB/src/graph', kind: 'Module', label: 'src/graph', properties: { path: 'src/graph' } },
    { key: issueKey, kind: 'Issue', label: 'Fixture demonstration issue', properties: { number: 0, state: 'open' } },
    { key: 'person:falkordb-maintainer-a', kind: 'Person', label: '@falkordb-maintainer-a', properties: { login: 'falkordb-maintainer-a' } },
    { key: 'person:falkordb-maintainer-b', kind: 'Person', label: '@falkordb-maintainer-b', properties: { login: 'falkordb-maintainer-b' } },
    { key: 'label:FalkorDB/FalkorDB/graph', kind: 'Label', label: 'graph', properties: { name: 'graph' } },
    { key: 'decision:fixture-public-source-links', kind: 'Decision', label: 'Fixture evidence is repo-level', properties: {} },
  ];
  const edges: GraphEdge[] = [
    { source: 'doc:FalkorDB/FalkorDB/README.md', target: 'module:FalkorDB/FalkorDB/src/graph', type: 'DESCRIBES' },
    { source: 'module:FalkorDB/FalkorDB/src/graph', target: 'repo:FalkorDB/FalkorDB', type: 'IN' },
    { source: issueKey, target: 'label:FalkorDB/FalkorDB/graph', type: 'HAS_LABEL' },
    { source: 'decision:fixture-public-source-links', target: 'doc:FalkorDB/FalkorDB/README.md', type: 'MADE_IN' },
    { source: 'decision:fixture-public-source-links', target: 'module:FalkorDB/FalkorDB/src/graph', type: 'ABOUT' },
  ];
  return { subgraph: { nodes, edges }, issueKey };
}

/** One node of every kind, chained, to prove every shape and colour is painted. */
function allKindsSubgraph(): { subgraph: Subgraph; issueKey: string } {
  const nodes: GraphNode[] = ALL_KINDS.map((kind, index) => ({ key: `${kind.toLowerCase()}:${index}`, kind, label: kind, properties: {} }));
  const edges: GraphEdge[] = [];
  for (let i = 0; i < nodes.length - 1; i += 1) edges.push({ source: nodes[i].key, target: nodes[i + 1].key, type: 'RELATED' });
  return { subgraph: { nodes, edges }, issueKey: nodes[ALL_KINDS.indexOf('Issue')].key };
}

/** More than 150 nodes: Files collapse into their Modules, then the cap applies. */
function manySubgraph(): Subgraph {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  for (let i = 0; i < 200; i += 1) nodes.push({ key: `module:repo/src/m${i}`, kind: 'Module', label: `src/m${i}`, properties: {} });
  for (let i = 0; i < 200; i += 1) {
    const moduleKey = `module:repo/src/m${i % 200}`;
    const fileKey = `file:repo/src/m${i % 200}/f${i}.ts`;
    nodes.push({ key: fileKey, kind: 'File', label: `f${i}.ts`, properties: {} });
    edges.push({ source: fileKey, target: moduleKey, type: 'IN' });
  }
  return { nodes, edges };
}

export default async function GraphHarnessPage({ searchParams }: { searchParams: Promise<{ case?: string }> }) {
  if (process.env.NODE_ENV === 'production') notFound();
  const { case: variant = 'fixture' } = await searchParams;

  let subgraph: Subgraph = { nodes: [], edges: [] };
  let issueKey: string | null = null;
  if (variant === 'all') ({ subgraph, issueKey } = allKindsSubgraph());
  else if (variant === 'many') subgraph = manySubgraph();
  else if (variant !== 'empty') ({ subgraph, issueKey } = fixtureSubgraph());

  return (
    <main style={{ height: '100vh', padding: 16 }}>
      <GraphCanvasHarness subgraph={subgraph} issueKey={issueKey} />
    </main>
  );
}
