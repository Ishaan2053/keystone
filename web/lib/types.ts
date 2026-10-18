export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

export type GraphKind =
  | "Person"
  | "Team"
  | "Repo"
  | "Module"
  | "File"
  | "Issue"
  | "PR"
  | "Comment"
  | "Label"
  | "Doc"
  | "Chunk"
  | "Decision"
  | "AgentRun"
  | "Action"
  | "Feedback"
  | "Rule";

export interface ToolResult<TData = unknown> {
  tool: string;
  data: TData;
  cypher: string[];
  params: Record<string, unknown>;
  evidence: string[];
  ms: number;
  empty: boolean;
}

export interface Claim {
  text: string;
  citations: string[];
}

export interface TriageResult {
  run_key: string;
  labels: Claim[];
  reviewers: Claim[];
  risks: Claim[];
  decisions: Claim[];
  comment_markdown: string;
  trace: ToolResult[];
}

export interface HealthResponse {
  status: "ok";
}

export interface TenantSummary {
  slug: string;
  name: string;
}

export interface TenantsResponse {
  tenants: TenantSummary[];
}

/** What a run records and recall returns; never carries UI feedback lineage. */
export interface RecordedTriageRequest {
  /** `issue:<owner>/<name>#<n>`: exactly that issue; a bare number can match several repos. */
  issue_key?: string | null;
  issue_number?: number | null;
  title?: string | null;
  body?: string;
}

export interface TriageRequest extends RecordedTriageRequest {
  /**
   * The prefix-free UUID of the run the feedback was recorded on, sent only with
   * `issue_key` and only by an explicit Re-triage. The API expands it to `run:<uuid>`.
   */
  feedback_parent_run?: string | null;
  /** The persisted feedback key the API returned, paired with `feedback_parent_run`. */
  feedback_key?: string | null;
}

export interface ApplyRequest {
  dry_run?: boolean;
}

export interface ActionResult {
  action_key: string;
  kind: "label" | "assign" | "comment";
  value: string;
  status: "applied" | "dry_run" | "refused";
  note: string | null;
}

export interface ApplyResult {
  run_key: string;
  dry_run: boolean;
  actions: ActionResult[];
}

export interface FeedbackRequest {
  action_key: string;
  verdict: "accept" | "reject";
  reason?: string;
  scope?: "module" | "global";
}

export interface FeedbackResult {
  feedback_key: string;
  action_key: string;
  verdict: "accept" | "reject";
  rule_key: string | null;
}

export interface AskRequest {
  question: string;
}

export interface AskResult {
  answer: Claim[];
  trace: ToolResult[];
}

export interface RunSummary {
  run_key: string;
  at: number;
  issue_key: string;
  summary: string;
  fallback: boolean;
  model: string;
  llm_calls: number;
  ms: number;
}

export interface RunsResponse {
  runs: RunSummary[];
}

export interface RecordedAction {
  action_key: string;
  kind: "label" | "assign" | "comment";
  value: string;
  module: string | null;
  status: "proposed" | "applied" | "dry_run" | "refused" | "rejected";
  note: string | null;
  feedback: FeedbackResult[];
}

export interface RecallRunData {
  run: RunSummary;
  evidence: string[];
  actions: RecordedAction[];
  triage: TriageResult;
  /** The title and body a raw-title run was asked; null (or absent) for an issue run. */
  request?: RecordedTriageRequest | null;
}

export interface PathData {
  keys: string[];
  rels: string[];
}

export interface GraphNode {
  key: string;
  kind: GraphKind;
  label: string;
  properties: Record<string, JsonValue>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}

export interface Subgraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ToolLatency {
  tool: string;
  p50_ms: number;
  p95_ms: number;
}

export interface ProofRates {
  reviewer_hit_at_3: number;
  module_hit_at_1: number;
  module_hit_at_3: number;
  label_precision_at_2: number;
}

export interface Proof {
  n: number;
  methods: Record<"graph" | "codeowners" | "similar_issues", ProofRates>;
  rows: Record<string, JsonValue>[];
}

export interface Stats {
  node_counts: Record<string, number>;
  edge_counts: Record<string, number>;
  last_ingest_at: number | null;
  proof: Proof | null;
  latencies: ToolLatency[];
  /** Artifacts whose file exists but could not be read; a missing file is not measured and not listed. */
  unavailable: ('proof' | 'latencies')[];
}
