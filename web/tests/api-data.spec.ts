/**
 * B4 Task 2 — fixture/API switch: API-mode browser tests.
 *
 * The API-mode Next server (port 3002) reads from the deterministic fake graph
 * API on `FAKE_API_PORT`, started here in the Playwright worker process. The
 * default fixture suite is a separate config, so these two modes never leak.
 */
import { test, expect } from '@playwright/test';
import { createFakeApi, startFakeApi, stopFakeApi, type FakeApi } from './fake-api';

let api: FakeApi;

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
  await input.fill('issue:Acme/slow');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await input.fill('issue:Acme/fast');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('.canvas-counts')).toContainText('5 live nodes');
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/fast');
  // The slower first request must not replace the newer result once it resolves.
  await page.waitForTimeout(1600);
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
