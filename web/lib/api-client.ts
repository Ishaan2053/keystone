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
import type { ApplyResult, PathData, RecallRunData, Stats, Subgraph, TenantSummary, TenantsResponse, ToolResult } from './types';

/** Finite request budget so a hung API surfaces an error instead of stalling a render. */
export const API_TIMEOUT_MS = 10_000;

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

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
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
function requestBudget(signal?: AbortSignal): RequestBudget {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(new ApiError(0, 'API request timed out')),
    API_TIMEOUT_MS,
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

export async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const url = new URL(path, `${apiBaseUrl()}/`);
  const budget = requestBudget(signal);
  try {
    const response = await fetch(url, {
      method: 'POST',
      cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
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

/** Apply a triage run. Only the canonical current run may be posted; never arbitrary actions. */
export async function applyRun(tenant: string, runKey: string, dryRun: boolean = true, signal?: AbortSignal): Promise<ApplyResult> {
  let key = runKey;
  if (key.startsWith('run:')) {
    key = key.slice('run:'.length);
  }
  if (!key) {
    throw new ApiError(400, 'run key is required');
  }
  return apiPost<ApplyResult>(`/tenants/${encodeURIComponent(tenant)}/runs/${encodeURIComponent(key)}/apply`, { dry_run: dryRun }, signal);
}
