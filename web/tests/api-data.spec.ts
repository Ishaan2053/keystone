/**
 * B4 Task 2 — fixture/API switch: API-mode browser tests.
 *
 * The API-mode Next server (port 3002) reads from the deterministic fake graph
 * API on `FAKE_API_PORT`, started here in the Playwright worker process. The
 * default fixture suite is a separate config, so these two modes never leak.
 */
import { test, expect, type Page, type Request } from '@playwright/test';
import {
  createFakeApi,
  startFakeApi,
  stopFakeApi,
  KNOWN_RUN,
  PALLET_ISSUE,
  type FakeApi,
} from './fake-api';

let api: FakeApi;

const SLOW_ISSUE = 'issue:Acme/slow';

/**
 * Resolves once the browser has settled the slow `issue:Acme/slow` subgraph read,
 * whether it finished (the response landed) or failed (the client aborted it),
 * then lets one frame and task pass so any handler for it has run and rendered.
 * Arm it before the click that starts the read.
 */
function slowReadSettled(page: Page): () => Promise<void> {
  const isSlow = (request: Request) =>
    request.url().includes('/subgraph') && decodeURIComponent(request.url()).includes(SLOW_ISSUE);
  const settled = new Promise<void>((resolve) => {
    const onSettled = (request: Request) => {
      if (!isSlow(request)) return;
      page.off('requestfinished', onSettled);
      page.off('requestfailed', onSettled);
      resolve();
    };
    page.on('requestfinished', onSettled);
    page.on('requestfailed', onSettled);
  });
  return async () => {
    await settled;
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0))));
  };
}

test.beforeAll(async () => {
  api = createFakeApi();
  await startFakeApi(api);
});

test.afterAll(async () => {
  if (api) await stopFakeApi(api);
});

test.beforeEach(() => {
  api.state.scenario = 'ok';
  api.state.lastKeys = null;
  api.state.requests = [];
});

test('api mode shows supplied counts and selected real issue', async ({ page }) => {
  const key = 'issue:Acme/Widgets#42';
  await page.goto(`/t/acme?issue=${encodeURIComponent(key)}`);
  await expect(page.locator('[data-source="live"]')).toBeVisible();
  await expect(page.locator('.canvas-counts')).toContainText('Issue: 7');
  await expect(page.locator('.canvas-counts')).toContainText('live nodes');
  await expect(page.locator('[data-issue-key]')).toHaveText(key);
  await expect(page.getByText('Fixture preview', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Suggested reviewers' })).toHaveCount(0);
});

test('API outage never displays fixture success', async ({ page }) => {
  api.state.scenario = 'outage';
  await page.goto('/t/acme');
  await expect(page.locator('[data-live-error]')).toContainText('Live data unavailable');
  await expect(page.getByText('Fixture preview', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Suggested reviewers' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Apply to sandbox/ })).toHaveCount(0);
});

test('unknown API tenant is 404', async ({ page }) => {
  expect((await page.goto('/t/unknown'))?.status()).toBe(404);
});

test('changing issue discards stale graph response', async ({ page }) => {
  await page.goto('/t/acme');
  const input = page.getByLabel('Issue key');
  const waitForSlowRead = slowReadSettled(page);
  await input.fill(SLOW_ISSUE);
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await input.fill('issue:Acme/fast');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('.canvas-counts')).toContainText('5 live nodes');
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/fast');
  // The slower first request must not replace the newer result once it settles.
  await waitForSlowRead();
  await expect(page.locator('.canvas-counts')).toContainText('5 live nodes');
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/fast');
});

test('reserved characters survive issue selection', async ({ page }) => {
  const key = 'issue:Acme/Widgets#12, with/slash and #hash';
  await page.goto(`/t/acme?issue=${encodeURIComponent(key)}`);
  await expect(page.locator('[data-issue-key]')).toHaveText(key);
  expect(api.state.lastKeys).toEqual([key]);

  await page.getByLabel('Issue key').fill(key);
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('[data-issue-key]')).toHaveText(key);
  expect(api.state.lastKeys).toEqual([key]);
});

test('tenant switch clears selected run and pending graph', async ({ page }) => {
  // B9: the second tenant must remount the workspace so old-tenant local state
  // (run card, evidence selection, pending Acme subgraph read) never leaks.
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(page.locator('[data-run-summary]')).toContainText('Persisted run: triaged Acme/Widgets#42');
  await expect(page.locator('.canvas-counts')).toContainText('Issue: 7');

  // The current live Dry run control is disabled and locked on; never toggled.
  const dryRun = page.getByRole('switch', { name: 'Dry run' });
  await expect(dryRun).toBeChecked();
  await expect(dryRun).toBeDisabled();

  // Select one evidence citation: workspace-local state that must not survive.
  await page.getByRole('button', { name: 'Show evidence: label:Acme/Widgets/graph', exact: true }).click();
  await expect(page.locator('[data-evidence-state]')).toHaveAttribute('data-evidence-state', 'ready');

  // Start the 1,200 ms slow subgraph read so a response is pending in flight.
  const issueInput = page.getByLabel('Issue key');
  const waitForSlowRead = slowReadSettled(page);
  await issueInput.fill(SLOW_ISSUE);
  await page.getByRole('button', { name: 'Load issue graph' }).click();

  // Switch through the existing Tenant select; the Header pushes no query parameters.
  await page.getByLabel('Tenant').click();
  await page.getByRole('option', { name: 'Pallets' }).click();

  // Let the slow Acme read settle (landed or aborted) before inspecting the destination.
  await waitForSlowRead();

  await expect(page).toHaveURL(/\/t\/pallets$/);
  await expect(page.getByLabel('Issue key')).toHaveValue('');
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
  await expect(page.locator('[data-action-status]')).toHaveCount(0);
  await expect(page.locator('.canvas-counts')).toContainText('Issue: 3');

  // No Acme run, claim, trace or evidence selection survives the switch.
  await expect(page.locator('[data-run-summary]')).toHaveCount(0);
  await expect(page.locator('[data-run-actions]')).toHaveCount(0);
  await expect(page.locator('[data-claim-section]')).toHaveCount(0);
  await expect(page.locator('[data-issue-key]')).toHaveCount(0);
  await expect(page.locator('.trace-panel')).not.toContainText('affected_modules');
  await expect(page.locator('[data-evidence-state]')).toHaveAttribute('data-evidence-state', 'none');

  // The bare Pallets route is the honest empty canvas with local stats only.
  await expect(page.locator('[data-graph-empty]')).toBeVisible();

  // The one Flask issue loads and anchors the distinct Pallets graph.
  await issueInput.fill(PALLET_ISSUE);
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('[data-issue-key]')).toHaveText(PALLET_ISSUE);
  await expect(page.locator('.canvas-counts')).toContainText('3 live nodes');
  expect(api.state.lastKeys).toEqual([PALLET_ISSUE]);

  // Switching back leaves a clean URL: no run or issue query parameters.
  await page.getByLabel('Tenant').click();
  await page.getByRole('option', { name: 'Acme' }).click();
  await expect(page).toHaveURL(/\/t\/acme$/);
  await expect(page.getByLabel('Issue key')).toHaveValue('');
  await expect(page.locator('[data-issue-key]')).toHaveCount(0);
  await expect(page.locator('.canvas-counts')).toContainText('Issue: 7');
});

test('destination outage never reuses source data', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(page.locator('[data-run-summary]')).toContainText('Persisted run: triaged Acme/Widgets#42');
  await expect(page.locator('.canvas-counts')).toContainText('Issue: 7');

  // The fake API outage starts only after the source page is fully loaded.
  api.state.scenario = 'outage';
  await page.getByLabel('Tenant').click();
  await page.getByRole('option', { name: 'Pallets' }).click();
  await expect(page).toHaveURL(/\/t\/pallets$/);

  await expect(page.locator('[data-live-error]')).toContainText('Live data unavailable');
  // No old run, graph or fixture success is carried into the failed tenant.
  await expect(page.locator('[data-run-summary]')).toHaveCount(0);
  await expect(page.locator('[data-claim-section]')).toHaveCount(0);
  await expect(page.locator('.canvas-counts')).toHaveCount(0);
  await expect(page.getByText('Fixture preview', { exact: true })).toHaveCount(0);
});
