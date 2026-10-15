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

test('live dry run switch is locked on with an honest note', async ({ page }) => {
  await page.goto(`/t/acme?run=${RUN}`);
  const toggle = page.getByRole('switch', { name: 'Dry run' });
  await expect(toggle).toBeChecked();
  await expect(toggle).toBeDisabled();
  await expect(page.locator('[data-live-dry-run-note]')).toContainText('only through the sandbox webhook');
  await expect(page.getByRole('button', { name: 'Apply to sandbox (dry run)', exact: true })).toBeVisible();
});

test('a refused live write explains the webhook rule instead of a bare failure', async ({ page }) => {
  api.state.applyScenario = 'forbidden';
  await page.goto(`/t/acme?run=${RUN}`);
  await page.getByRole('button', { name: 'Apply to sandbox (dry run)', exact: true }).click();
  await expect(page.locator('.preview-status')).toContainText('only through the sandbox webhook');
  await expect(page.locator('.preview-status')).not.toContainText('Apply failed');
  await expect(page.locator('.preview-status')).not.toContainText('Applied');
});
