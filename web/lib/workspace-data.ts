import type { ApplyResult, Stats, Subgraph, TenantSummary, TriageResult } from './types';

export interface WorkspaceData {
  tenant: string;
  tenants: TenantSummary[];
  triage: TriageResult;
  subgraph: Subgraph;
  stats: Stats;
  applyPreview: ApplyResult;
  applyRefused: ApplyResult;
}
