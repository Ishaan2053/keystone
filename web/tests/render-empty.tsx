import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Subgraph, ToolResult, TriageResult } from '../lib/types';
import { TracePanel } from '../components/trace-panel';
import { TriageCard } from '../components/triage-card';
import { CanvasPlaceholder } from '../components/canvas-placeholder';

const emptyTool: ToolResult = {
  tool: 'empty_tool',
  data: [],
  cypher: [],
  params: {},
  evidence: [],
  ms: 0,
  empty: true,
};

const emptyTriage: TriageResult = {
  run_key: 'empty',
  labels: [],
  reviewers: [],
  risks: [],
  decisions: [],
  comment_markdown: '',
  trace: [emptyTool],
};

const emptySubgraph: Subgraph = { nodes: [], edges: [] };

const traceHtml = renderToStaticMarkup(<TracePanel trace={[emptyTool]} selectedEvidence={[]} />);
const emptyTraceHtml = renderToStaticMarkup(<TracePanel trace={[]} selectedEvidence={[]} />);
const triageHtml = renderToStaticMarkup(
  <TriageCard
    triage={emptyTriage}
    dryRun={true}
    onDryRunChange={() => {}}
    selectedEvidence={[]}
    onSelectEvidence={() => {}}
    applyPreview={{ run_key: 'empty', dry_run: true, actions: [] }}
    applyRefused={{ run_key: 'empty', dry_run: false, actions: [] }}
  />
);
const canvasHtml = renderToStaticMarkup(<CanvasPlaceholder subgraph={emptySubgraph} selectedEvidence={[]} />);

function assertContains(name: string, html: string, expected: string) {
  if (!html.includes(expected)) {
    throw new Error(`${name}: expected ${JSON.stringify(expected)} in rendered output`);
  }
  console.log(`PASS ${name}: ${expected}`);
}

function assertRegex(name: string, html: string, pattern: RegExp) {
  if (!pattern.test(html)) {
    throw new Error(`${name}: expected pattern ${pattern}`);
  }
  console.log(`PASS ${name}: ${pattern}`);
}

assertContains('empty tool summary', traceHtml, 'No evidence');
assertContains('empty trace list', emptyTraceHtml, 'No evidence');
const claimSections = ['labels', 'reviewers', 'risks', 'decisions'] as const;
const claimTitles = {
  labels: 'Suggested labels',
  reviewers: 'Suggested reviewers',
  risks: 'Risk flags',
  decisions: 'Past decisions',
} as const;
let noSuggestionsCount = 0;
for (const section of claimSections) {
  assertContains(`empty claim section title ${section}`, triageHtml, claimTitles[section]);
  assertRegex(
    `empty claim section ${section} has no suggestions`,
    triageHtml,
    new RegExp(`data-claim-section="${section}"[\\s\\S]*?No suggestions`)
  );
}
for (const match of triageHtml.matchAll(/No suggestions/g)) noSuggestionsCount += 1;
if (noSuggestionsCount !== claimSections.length) {
  throw new Error(`expected ${claimSections.length} "No suggestions" empty-state entries, found ${noSuggestionsCount}`);
}
console.log(`PASS repeated no-suggestions count: ${noSuggestionsCount}`);
assertContains('empty subgraph count', canvasHtml, '0 fixture nodes · 0 fixture edges');

console.log('ALL EMPTY RENDER CHECKS PASSED');
