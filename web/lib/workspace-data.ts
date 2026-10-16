import type { ApplyResult, PathData, RecallRunData, Stats, Subgraph, TenantSummary, ToolResult, TriageResult } from './types';

/**
 * UI-local data shapes for the workspace. These are *not* a replacement for the
 * shared wire contracts in `types.ts`: the fixture and live shapes wrap the same
 * `TriageResult` / `Subgraph` / `Stats` / `ApplyResult` models and only add the
 * presentation-only `source`, `issueKey` and `error` fields the UI needs to be
 * honest about where its data came from.
 */

export type WorkspaceSource = 'fixture' | 'live';

/** Dataset returned by `fixture-data.ts`; fixture JSON stays the source of truth. */
export interface FixtureDataset {
  tenant: string;
  tenants: TenantSummary[];
  triage: TriageResult;
  subgraph: Subgraph;
  stats: Stats;
  /**
   * The explicit fixture evidence path (`api/fixtures/path.json`). Shown only as
   * a labelled demonstration; fixture paths are never computed in the browser.
   */
  path: ToolResult<PathData>;
  applyPreview: ApplyResult;
  applyRefused: ApplyResult;
}

export interface FixtureWorkspaceData extends FixtureDataset {
  source: 'fixture';
  /** Fixtures are not keyed by a real issue; the fixture demo is labelled as such. */
  issueKey: null;
}

export interface LiveWorkspaceData {
  source: 'live';
  tenant: string;
  tenants: TenantSummary[];
  /** The `?issue=` anchor; null until the visitor selects an issue. */
  issueKey: string | null;
  /** Canonical selected run key (`run:<uuid>`); set when a valid `?run=` deep link resolves. */
  runKey?: string | null;
  /** The recalled persisted run when the link selected one; null for a bare issue/tenant view. */
  run?: RecallRunData | null;
  /** Real triage is absent until a run exists; never synthesised from fixtures. */
  triage: TriageResult | null;
  subgraph: Subgraph;
  stats: Stats | null;
  applyPreview: null;
  applyRefused: null;
  /** Non-null when the live API failed; the UI shows this and never fixture data. */
  error: string | null;
}

export type WorkspaceData = FixtureWorkspaceData | LiveWorkspaceData;
