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
  api.state.applyScenario = 'ok';
  api.state.lastKeys = null;
  api.state.requests = [];
  api.state.applyRequests = [];
});

const RUN = '11111111-2222-4333-8444-555555555555';

test('API apply posts current tenant/run and dry_run', async ({ page }) => {
  await page.goto(`/t/acme?run=${RUN}`);
  const button = page.getByRole('button', { name: 'Apply to sandbox (dry run)', exact: true });
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.locator('.preview-status')).toContainText('Skipped (dry run)');
  expect(api.state.applyRequests).toHaveLength(1);
  expect(api.state.applyRequests[0].tenant).toBe('acme');
  expect(api.state.applyRequests[0].run).toBe(RUN);
  expect(api.state.applyRequests[0].body).toEqual({ dry_run: true });
});

test('pending disables repeat submit', async ({ page }) => {
  await page.goto(`/t/acme?run=${RUN}`);
  const button = page.getByRole('button', { name: 'Apply to sandbox (dry run)', exact: true });
  await button.click();
  await expect(button).toBeDisabled();
  await expect(page.locator('.preview-status')).toContainText('Skipped (dry run)');
  await expect(button).toBeEnabled();
});

test('failure is not shown as applied', async ({ page }) => {
  api.state.applyScenario = 'fail';
  await page.goto(`/t/acme?run=${RUN}`);
  const button = page.getByRole('button', { name: 'Apply to sandbox (dry run)', exact: true });
  await button.click();
  await expect(page.locator('.preview-status')).toContainText('Apply failed');
  await expect(page.locator('.preview-status')).not.toContainText('Applied');
});