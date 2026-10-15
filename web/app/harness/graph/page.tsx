import { notFound } from 'next/navigation';
import type { GraphEdge, GraphKind, GraphNode, Subgraph } from '@/lib/types';
import type { EvidenceSelection } from '@/lib/evidence-selection';
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

/**
 * Two relationship types between the same endpoints: a returned path over one
 * of them must not highlight the other.
 */
function parallelSubgraph(): { subgraph: Subgraph; issueKey: string; selection: EvidenceSelection } {
  const issueKey = 'issue:x';
  const module = 'module:y';
  const subgraph: Subgraph = {
    nodes: [
      { key: issueKey, kind: 'Issue', label: 'x', properties: {} },
      { key: module, kind: 'Module', label: 'y', properties: {} },
    ],
    edges: [
      { source: issueKey, target: module, type: 'TOUCHES' },
      { source: module, target: issueKey, type: 'ABOUT' },
    ],
  };
  const selection: EvidenceSelection = {
    claimId: 'labels:0',
    citationKeys: [issueKey, module],
    paths: [{ keys: [issueKey, module], rels: ['TOUCHES'] }],
    nodeKeys: [issueKey, module],
    missingKeys: [],
    state: 'ready',
    riskKeys: [],
  };
  return { subgraph, issueKey, selection };
}

/** A trace-supported negative result: the cited module has no evidence path. */
function negativeSubgraph(): { subgraph: Subgraph; issueKey: string; selection: EvidenceSelection } {
  const issueKey = 'issue:x';
  const module = 'module:y';
  const subgraph: Subgraph = {
    nodes: [
      { key: issueKey, kind: 'Issue', label: 'x', properties: {} },
      { key: module, kind: 'Module', label: 'y', properties: {} },
    ],
    edges: [{ source: issueKey, target: module, type: 'ABOUT' }],
  };
  const selection: EvidenceSelection = {
    claimId: 'risks:0',
    citationKeys: [module],
    paths: [],
    nodeKeys: [module],
    missingKeys: [],
    state: 'ready',
    riskKeys: [module],
  };
  return { subgraph, issueKey, selection };
}

/** A cited key that is not in the returned graph: an explicit missing notice. */
function missingSubgraph(): { subgraph: Subgraph; issueKey: string; selection: EvidenceSelection } {
  const issueKey = 'issue:x';
  const subgraph: Subgraph = {
    nodes: [
      { key: issueKey, kind: 'Issue', label: 'x', properties: {} },
      { key: 'module:y', kind: 'Module', label: 'y', properties: {} },
    ],
    edges: [{ source: issueKey, target: 'module:y', type: 'ABOUT' }],
  };
  const selection: EvidenceSelection = {
    claimId: 'reviewers:0',
    citationKeys: ['module:y', 'module:z'],
    paths: [{ keys: [issueKey, 'module:y'], rels: ['ABOUT'] }],
    nodeKeys: [issueKey, 'module:y'],
    missingKeys: ['module:z'],
    state: 'ready',
    riskKeys: [],
  };
  return { subgraph, issueKey, selection };
}

export default async function GraphHarnessPage({ searchParams }: { searchParams: Promise<{ case?: string }> }) {
  if (process.env.NODE_ENV === 'production') notFound();
  const { case: variant = 'fixture' } = await searchParams;

  let subgraph: Subgraph = { nodes: [], edges: [] };
  let issueKey: string | null = null;
  let selection: EvidenceSelection | null = null;
  if (variant === 'all') ({ subgraph, issueKey } = allKindsSubgraph());
  else if (variant === 'many') subgraph = manySubgraph();
  else if (variant === 'parallel') ({ subgraph, issueKey, selection } = parallelSubgraph());
  else if (variant === 'negative') ({ subgraph, issueKey, selection } = negativeSubgraph());
  else if (variant === 'missing') ({ subgraph, issueKey, selection } = missingSubgraph());
  else if (variant !== 'empty') ({ subgraph, issueKey } = fixtureSubgraph());

  return (
    <main style={{ height: '100vh', padding: 16 }}>
      <GraphCanvasHarness subgraph={subgraph} issueKey={issueKey} selection={selection} />
    </main>
  );
}
