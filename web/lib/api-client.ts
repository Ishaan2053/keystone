/**
 * Browser-safe typed HTTP reads for the live API (B4 Task 2).
 *
 * Server Components use these through `data-source.ts`; client Components (B5's
 * canvas and path resolution) use `getPath`/`getSubgraph` directly with an
 * `AbortSignal` so a superseded request can never replace a newer one.
 *
 * Every read is uncached (`cache: 'no-store'`) with a finite timeout, and the
 * base URL comes from `NEXT_PUBLIC_API_BASE_URL` — a location, never a mode.
 */
import type { ApplyResult, AskResult, FeedbackRequest, FeedbackResult, PathData, RecallRunData, RunsResponse, Stats, Subgraph, TenantSummary, TenantsResponse, ToolResult, TriageRequest, TriageResult } from './types';

/** Finite request budget so a hung API surfaces an error instead of stalling a render. */
export const API_TIMEOUT_MS = 10_000;
/** A live triage runs the agent (up to 5 LLM calls plus tools): allow it minutes, not seconds. */
export const TRIAGE_TIMEOUT_MS = 180_000;
/** Ask is one search plus one compose call. */
export const ASK_TIMEOUT_MS = 60_000;

const RUN_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Canonical run UUID from a deep-link value, or `null` when malformed. At most
 * one leading `run:` prefix is stripped; the HTTP path is the bare UUID while
 * contracts keep the canonical `run:<uuid>` form.
 */
export function runKeyUuid(runKey: string): string | null {
  const trimmed = runKey.trim();
  const uuid = trimmed.startsWith('run:') ? trimmed.slice('run:'.length) : trimmed;
  return RUN_UUID.test(uuid) ? uuid.toLowerCase() : null;
}

export class ApiError extends Error {
  readonly status: number;
  /** The API's sanitized `detail` string, when it sent one (e.g. a 409 naming the matching issues). */
  readonly detail: string | null;

  constructor(status: number, message: string, detail: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** The `detail` of a FastAPI error body when it is a plain string; validation lists are not shown. */
async function errorDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    const detail = body && typeof body === 'object' ? (body as { detail?: unknown }).detail : null;
    return typeof detail === 'string' && detail.trim() ? detail.trim() : null;
  } catch {
    return null;
  }
}

export function apiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!raw || !raw.trim()) {
    throw new ApiError(0, 'NEXT_PUBLIC_API_BASE_URL is not configured');
  }
  return raw.trim().replace(/\/+$/, '');
}

interface RequestBudget {
  signal: AbortSignal;
  release: () => void;
}

/** Combine the caller's signal with a finite timeout into one abort signal. */
function requestBudget(signal?: AbortSignal, timeoutMs = API_TIMEOUT_MS): RequestBudget {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(new ApiError(0, 'API request timed out')),
    timeoutMs,
  );
  const forward = () => controller.abort(signal?.reason);
  if (signal) {
    if (signal.aborted) controller.abort(signal.reason);
    else signal.addEventListener('abort', forward, { once: true });
  }
  return {
    signal: controller.signal,
    release: () => {
      clearTimeout(timeout);
      signal?.removeEventListener('abort', forward);
    },
  };
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = new URL(path, `${apiBaseUrl()}/`);
  const budget = requestBudget(signal);
  try {
    const response = await fetch(url, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: budget.signal,
    });
    if (!response.ok) {
      throw new ApiError(response.status, `API request failed with status ${response.status}`);
    }
    return (await response.json()) as T;
  } finally {
    budget.release();
  }
}

export async function getTenants(signal?: AbortSignal): Promise<TenantSummary[]> {
  const body = await apiGet<TenantsResponse>('/tenants', signal);
  return body.tenants;
}

export async function getStats(tenant: string, signal?: AbortSignal): Promise<Stats> {
  return apiGet<Stats>(`/tenants/${encodeURIComponent(tenant)}/stats`, signal);
}

export async function getRuns(tenant: string, limit = 20, signal?: AbortSignal): Promise<RunsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return apiGet<RunsResponse>(`/tenants/${encodeURIComponent(tenant)}/runs?${params}`, signal);
}

/**
 * One-hop neighbourhood for the given stable keys. Keys travel as a JSON array
 * string so keys containing commas, slashes, `#` or spaces stay unambiguous.
 */
export async function getSubgraph(
  tenant: string,
  keys: string[],
  signal?: AbortSignal,
): Promise<Subgraph> {
  const params = new URLSearchParams({ keys: JSON.stringify(keys) });
  return apiGet<Subgraph>(`/tenants/${encodeURIComponent(tenant)}/subgraph?${params.toString()}`, signal);
}

/** Evidence-only shortest path between two stable keys (consumed by B5). */
export async function getPath(
  tenant: string,
  fromKey: string,
  toKey: string,
  signal?: AbortSignal,
): Promise<ToolResult<PathData>> {
  const params = new URLSearchParams({ from: fromKey, to: toKey });
  return apiGet<ToolResult<PathData>>(`/tenants/${encodeURIComponent(tenant)}/path?${params.toString()}`, signal);
}

/**
 * Recall one persisted run by canonical UUID path (B7 selected-run deep link).
 * Only a well-formed UUID reaches the wire; the returned identity is verified by
 * the caller against the requested run.
 */
export async function getRun(
  tenant: string,
  runKey: string,
  signal?: AbortSignal,
): Promise<ToolResult<RecallRunData>> {
  const uuid = runKeyUuid(runKey);
  if (!uuid) throw new ApiError(400, 'run key must be a UUID');
  return apiGet<ToolResult<RecallRunData>>(`/tenants/${encodeURIComponent(tenant)}/runs/${encodeURIComponent(uuid)}`, signal);
}

export async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal, timeoutMs = API_TIMEOUT_MS): Promise<T> {
  const url = new URL(path, `${apiBaseUrl()}/`);
  const budget = requestBudget(signal, timeoutMs);
  try {
    const response = await fetch(url, {
      method: 'POST',
      cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: budget.signal,
    });
    if (!response.ok) {
      throw new ApiError(response.status, `API request failed with status ${response.status}`, await errorDetail(response));
    }
    return (await response.json()) as T;
  } finally {
    budget.release();
  }
}

/** Apply a triage run. Only the canonical current run may be posted; never arbitrary actions. */
export async function applyRun(tenant: string, runKey: string, dryRun: boolean = true, signal?: AbortSignal): Promise<ApplyResult> {
  const key = runKeyUuid(runKey);
  if (!key) throw new ApiError(400, 'run key must be a UUID');
  return apiPost<ApplyResult>(`/tenants/${encodeURIComponent(tenant)}/runs/${encodeURIComponent(key)}/apply`, { dry_run: dryRun }, signal);
}

/**
 * Start a triage: `{issue_number}` or `{title, body}` (exactly one mode, as
 * `TriageRequest` validates). Live mode runs the agent and records the run with
 * no GitHub writes; the API's fixture mode returns its fixed preview.
 */
export async function triage(tenant: string, body: TriageRequest, signal?: AbortSignal): Promise<TriageResult> {
  return apiPost<TriageResult>(`/tenants/${encodeURIComponent(tenant)}/triage`, body, signal, TRIAGE_TIMEOUT_MS);
}

/** Cited Q&A over the tenant's decisions; read-only on the API side. */
export async function ask(tenant: string, question: string, signal?: AbortSignal): Promise<AskResult> {
  return apiPost<AskResult>(`/tenants/${encodeURIComponent(tenant)}/ask`, { question }, signal, ASK_TIMEOUT_MS);
}

/** Accept or reject one action the run proposed; a reject on an assign creates an avoid Rule. */
export async function postFeedback(tenant: string, runKey: string, body: FeedbackRequest, signal?: AbortSignal): Promise<FeedbackResult> {
  const key = runKeyUuid(runKey);
  if (!key) throw new ApiError(400, 'run key must be a UUID');
  return apiPost<FeedbackResult>(`/tenants/${encodeURIComponent(tenant)}/runs/${encodeURIComponent(key)}/feedback`, body, signal);
}

/** `issue:<owner>/<name>#<n>`, exactly as `TriageRequest.issue_key` validates it in `api/schemas.py`. */
const FULL_ISSUE_KEY = /^issue:[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?#[1-9][0-9]*$/;

/** True for a key the triage route accepts as `issue_key`. */
export function isFullIssueKey(value: string): boolean {
  return FULL_ISSUE_KEY.test(value);
}

/**
 * True for a graph issue key (`issue:` prefix, no whitespace): the only input the
 * issue graph read and the path anchor accept. A raw-title run's `adhoc`, a bare
 * number or free text is not one.
 */
export function isIssueKey(value: string | null | undefined): value is string {
  return typeof value === 'string' && /^issue:\S+$/.test(value);
}

/**
 * The triage request for the issue input: a full issue key is `{issue_key}`
 * (exactly that issue); `#<n>` or a bare number is `{issue_number}`; any other
 * text that is not an issue key is a raw `{title}`. An `issue:` key that is not a
 * full key cannot be triaged (null).
 */
export function triageRequestFor(input: string): TriageRequest | null {
  const text = input.trim();
  if (!text) return null;
  if (isFullIssueKey(text)) return { issue_key: text };
  const number = /^#?(\d+)$/.exec(text);
  if (number) {
    const value = Number(number[1]);
    return Number.isSafeInteger(value) && value > 0 ? { issue_number: value } : null;
  }
  if (text.startsWith('issue:')) return null;
  return { title: text };
}
