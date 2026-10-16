/**
 * B7 Task 3 — selected-run deep link: API-mode browser tests.
 *
 * `/t/<tenant>?run=<uuid>` must resolve exactly one persisted run through the
 * fake recall route and render its triage, evidence and recorded actions in the
 * existing live workspace. Unknown, malformed or wrong-tenant run links are an
 * explicit error and never substitute fixture data. Plain `?issue=` still works.
 */
import { test, expect } from '@playwright/test';
import {
  createFakeApi,
  startFakeApi,
  stopFakeApi,
  KNOWN_RUN,
  KNOWN_RUN_ISSUE,
  type FakeApi,
} from './fake-api';

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

test('run deep link renders the persisted triage, evidence and actions', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(page.locator('[data-source="live"]')).toBeVisible();

  // The run, its issue anchor and the graph it produced.
  await expect(page.locator('[data-run-summary]')).toContainText('Persisted run: triaged Acme/Widgets#42');
  await expect(page.locator('[data-issue-key]')).toHaveText(KNOWN_RUN_ISSUE);

  // Exact persisted triage claims.
  await expect(page.locator('[data-claim-section="labels"]')).toContainText('graph');
  await expect(page.locator('[data-claim-section="labels"]')).toContainText('bug');
  await expect(page.locator('[data-claim-section="reviewers"]')).toContainText('@acme-maintainer-a');
  await expect(page.locator('[data-claim-section="risks"]')).toContainText('Persisted recall risk: validate reviewer load before applying.');
  await expect(page.locator('[data-claim-section="decisions"]')).toContainText('Persisted recall decision: reuse graph evidence for this issue.');

  // Evidence is reachable from a claim citation and shown in the tool trace.
  await expect(page.getByRole('button', { name: 'Show evidence: label:Acme/Widgets/graph', exact: true })).toBeVisible();
  await expect(page.locator('.trace-panel')).toContainText('affected_modules');

  // Exact persisted actions with their status.
  const actions = page.locator('[data-run-actions] li');
  await expect(actions).toHaveCount(2);
  await expect(actions.filter({ hasText: 'acme-maintainer-a' })).toContainText('applied');
  await expect(actions.filter({ hasText: 'proposed' })).toContainText('graph');

  // Never a fixture peek behind a real run.
  await expect(page.getByText('Fixture preview', { exact: true })).toHaveCount(0);
  expect(api.state.requests).toContain(`GET /tenants/acme/runs/${KNOWN_RUN}`);
});

test('run: prefix in the deep link resolves the same run', async ({ page }) => {
  await page.goto(`/t/acme?run=${encodeURIComponent(`run:${KNOWN_RUN}`)}`);
  await expect(page.locator('[data-issue-key]')).toHaveText(KNOWN_RUN_ISSUE);
  await expect(page.locator('[data-claim-section="labels"]')).toContainText('graph');
  expect(api.state.requests).toContain(`GET /tenants/acme/runs/${KNOWN_RUN}`);
});

test('a valid run takes precedence over ?issue=', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}&issue=${encodeURIComponent('issue:Acme/Widgets#999')}`);
  await expect(page.locator('[data-issue-key]')).toHaveText(KNOWN_RUN_ISSUE);
  await expect(page.locator('[data-claim-section="labels"]')).toContainText('graph');
});

test('loading an issue supersedes the deep-linked run card', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(page.locator('[data-run-summary]')).toBeVisible();
  await expect(page.locator('[data-claim-section="labels"]')).toContainText('graph');

  // A manual issue load must clear the run's summary, claims, actions and Apply.
  await page.locator('#issue-key').fill('issue:Acme/Widgets#7');
  await page.getByRole('button', { name: 'Load issue graph' }).click();

  await expect(page.locator('[data-run-summary]')).toHaveCount(0);
  await expect(page.locator('[data-claim-section="labels"]')).toHaveCount(0);
  await expect(page.locator('[data-run-actions]')).toHaveCount(0);
  await expect(page.locator('[data-triage-empty]')).toContainText('No triage run selected');
  await expect(page.getByRole('button', { name: /Apply to sandbox/ })).toHaveCount(0);
  expect(api.state.lastKeys).toEqual(['issue:Acme/Widgets#7']);
});

test('malformed run id shows an explicit error with no fixture data', async ({ page }) => {
  await page.goto('/t/acme?run=not-a-uuid');
  await expect(page.locator('[data-live-error]')).toContainText('not a valid run id');
  await expect(page.getByText('Fixture preview', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Suggested reviewers' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Apply to sandbox/ })).toHaveCount(0);
});

test('unknown run shows an explicit error with no fixture substitution', async ({ page }) => {
  await page.goto('/t/acme?run=99999999-9999-4999-8999-999999999999');
  await expect(page.locator('[data-live-error]')).toContainText('Run not found for this tenant');
  await expect(page.getByText('Fixture preview', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Suggested reviewers' })).toHaveCount(0);
  await expect(page.locator('[data-claim-section="labels"]')).toHaveCount(0);
});

test('a run requested under the wrong tenant shows an explicit error', async ({ page }) => {
  await page.goto(`/t/falkordb?run=${KNOWN_RUN}`);
  await expect(page.locator('[data-live-error]')).toContainText('Run not found for this tenant');
  await expect(page.getByText('Fixture preview', { exact: true })).toHaveCount(0);
});

test('plain ?issue= still selects live issue data', async ({ page }) => {
  const key = 'issue:Acme/Widgets#7';
  await page.goto(`/t/acme?issue=${encodeURIComponent(key)}`);
  await expect(page.locator('[data-issue-key]')).toHaveText(key);
  await expect(page.locator('[data-triage-empty]')).toContainText('No triage run selected');
  await expect(page.locator('[data-run-actions]')).toHaveCount(0);
  expect(api.state.lastKeys).toEqual([key]);
});
