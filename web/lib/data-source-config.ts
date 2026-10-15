/**
 * One frontend switch between fixture data and the live API.
 *
 * `NEXT_PUBLIC_DATA_SOURCE=fixtures|api` defaults to `fixtures`; any other value
 * is an error. `NEXT_PUBLIC_API_BASE_URL` is a location, not a second mode flag.
 * Kept free of Next/server imports so it can be checked without a runtime.
 */
export type DataSource = 'fixtures' | 'api';

export const DATA_SOURCES: readonly DataSource[] = ['fixtures', 'api'];

export function dataSource(): DataSource {
  const raw = (process.env.NEXT_PUBLIC_DATA_SOURCE ?? 'fixtures').trim();
  if (raw === 'fixtures' || raw === 'api') return raw;
  throw new Error(`NEXT_PUBLIC_DATA_SOURCE must be "fixtures" or "api" (received "${raw}")`);
}
