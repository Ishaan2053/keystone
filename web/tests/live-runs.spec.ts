import { test, expect, type Page } from '@playwright/test';
import { buildRecallRun, createFakeApi, startFakeApi, stopFakeApi, KNOWN_RUN, KNOWN_RUN_ISSUE, type FakeApi } from './fake-api';

let api: FakeApi;
const A = 'aaaaaaaa-1111-4111-8111-111111111111';
const B = 'bbbbbbbb-2222-4222-8222-222222222222';
function add(uuid: string, issue: string, at: number, tenant = 'acme') {
  const body = buildRecallRun(tenant);
  body.data.run = { ...body.data.run, run_key: `run:${uuid}`, issue_key: issue, at, summary: `Selected ${issue}` };
  body.data.triage.run_key = `run:${uuid}`;
  body.data.triage.trace[0].tool = `trace_${uuid.slice(0, 8)}`;
  (api.state.recalls[tenant] ??= {})[uuid] = body;
  (api.state.runs[tenant] ??= []).push(body.data.run);
  return body;
}
const row = (page: Page, uuid: string) => page.locator(`[data-live-run="run:${uuid}"]`);
async function selected(page: Page, uuid: string, issue: string) {
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${uuid}`);
  await expect(page.locator('[data-issue-key]')).toHaveText(issue);
  await expect(page.locator('#issue-key')).toHaveValue(issue);
  await expect(page.locator('.trace-panel')).toContainText(`trace_${uuid.slice(0, 8)}`);
  await expect(row(page, uuid)).toHaveAttribute('aria-pressed', 'true');
}
test.beforeAll(async () => { api = createFakeApi(); await startFakeApi(api); });
test.afterAll(async () => { await stopFakeApi(api); });
test.beforeEach(() => {
  Object.assign(api.state, { scenario: 'ok', runs: {}, recalls: {}, delays: {}, listOutage: false, requests: [], applyRequests: [] });
});

test('immediate newest-first deduplicated capped list and empty-view auto-selection', async ({ page }) => {
  for (let i = 1; i <= 23; i++) add(`00000000-0000-4000-8000-${String(i).padStart(12, '0')}`, `issue:Acme/Widgets#${i}`, 1760400000 + i);
  api.state.runs.acme.push(api.state.runs.acme[22]);
  const listRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/tenants/acme/runs');
  await page.goto('/t/acme');
  expect(new URL((await listRequest).url()).searchParams.get('limit')).toBe('20');
  await expect(page.locator('[data-live-run]')).toHaveCount(20);
  await expect(page.locator('[data-live-run]').first()).toContainText('#23');
  await expect(page.locator('[data-live-run]').last()).toContainText('#4');
  await expect(page.locator('[data-run-key]')).toHaveText('run:00000000-0000-4000-8000-000000000023');
  await expect(page.locator('[data-live-run] time').first()).toHaveAttribute('datetime', new Date(1760400023 * 1000).toISOString());
  expect(api.state.requests).toContain('GET /tenants/acme/runs');
  expect(api.state.requests.filter(r => r.includes('/runs/'))).toHaveLength(1);
  expect(api.state.applyRequests).toHaveLength(0);
});

test('arrival within ten seconds marks New without reloading or replacing selected evidence', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  await page.goto(`/t/acme?run=${A}`);
  await selected(page, A, 'issue:Acme/Widgets#1');
  await page.evaluate(() => { (window as unknown as { documentMarker: boolean }).documentMarker = true; });
  const graph = page.locator('.canvas-panel');
  await graph.evaluate(el => el.setAttribute('data-mounted-marker', 'kept'));
  add(B, 'issue:Acme/Widgets#2', 2);
  await expect(row(page, B)).toBeVisible({ timeout: 10_000 });
  await expect(row(page, B)).toContainText('New');
  await selected(page, A, 'issue:Acme/Widgets#1');
  await expect(graph).toHaveAttribute('data-mounted-marker', 'kept');
  expect(await page.evaluate(() => (window as unknown as { documentMarker: boolean }).documentMarker)).toBe(true);
  expect(api.state.applyRequests).toHaveLength(0);
});

test('empty list is distinct from outage and auto-selects an arriving run', async ({ page }) => {
  await page.goto('/t/acme');
  await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  add(A, 'issue:Acme/Widgets#1', 1);
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${A}`, { timeout: 10_000 });
});

for (const target of ['?run=not-a-uuid', '?run=99999999-9999-4999-8999-999999999999', '?issue=issue%3AAcme%2FWidgets%237']) {
  test(`never auto-selects over explicit selection ${target}`, async ({ page }) => {
    add(A, 'issue:Acme/Widgets#1', 1);
    await page.goto(`/t/acme${target}`);
    await expect(row(page, A)).toBeVisible();
    await expect(page.locator('[data-run-key]')).toHaveCount(0);
    await row(page, A).click();
    await selected(page, A, 'issue:Acme/Widgets#1');
    await expect(page).toHaveURL(new RegExp(`\\?run=${A}$`));
  });
}

test('switches all panes, resets Apply state and reconciles back/forward and reload', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1); add(B, 'issue:Acme/Widgets#2', 2);
  await page.goto(`/t/acme?run=${A}`);
  await selected(page, A, 'issue:Acme/Widgets#1');
  await page.getByRole('button', { name: /Apply to sandbox/ }).click();
  await expect(page.getByText('Apply complete.')).toBeVisible();
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await expect(page.getByText('Apply complete.')).toHaveCount(0);
  await expect(page.getByRole('switch', { name: 'Dry run' })).toBeChecked();
  await page.goBack(); await selected(page, A, 'issue:Acme/Widgets#1');
  await page.goForward(); await selected(page, B, 'issue:Acme/Widgets#2');
  await page.reload(); await selected(page, B, 'issue:Acme/Widgets#2');
});

test('rapid selections and manual issue transitions cancel delayed run and issue reads', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1); add(B, 'issue:Acme/Widgets#2', 2);
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(row(page, A)).toBeVisible();
  api.state.delays[`/tenants/acme/runs/${A}`] = 1200;
  await row(page, A).click(); await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await page.waitForTimeout(1400); await selected(page, B, 'issue:Acme/Widgets#2');
  await page.locator('#issue-key').fill('issue:Acme/Widgets#slow');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await page.waitForTimeout(1400); await selected(page, B, 'issue:Acme/Widgets#2');
  await row(page, A).click();
  await page.locator('#issue-key').fill('issue:Acme/Widgets#manual');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
  await page.waitForTimeout(1400);
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
});

test('failed or mismatched recall keeps prior coherent view and Apply binding', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1); const bad = add(B, 'issue:Acme/Widgets#2', 2);
  bad.data.triage.run_key = `run:${A}`;
  await page.goto(`/t/acme?run=${A}`); await selected(page, A, 'issue:Acme/Widgets#1');
  await row(page, B).click();
  await expect(page.locator('[data-live-error]')).toContainText('Unable to select run');
  await selected(page, A, 'issue:Acme/Widgets#1');
  await page.getByRole('button', { name: /Apply to sandbox/ }).click();
  await expect.poll(() => api.state.applyRequests.length).toBe(1);
  expect(api.state.applyRequests[0].run).toBe(A);
});

test('tenant navigation cannot leak a delayed prior-tenant list or recall', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(row(page, A)).toBeVisible();
  api.state.delays[`/tenants/acme/runs/${A}`] = 1200;
  await row(page, A).click();
  await page.getByRole('combobox', { name: 'Tenant' }).click();
  await page.getByRole('option', { name: 'Pallets', exact: true }).click();
  await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  await page.waitForTimeout(1400);
  await expect(row(page, A)).toHaveCount(0);
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
  expect(api.state.requests).toContain('GET /tenants/pallets/runs');
});

test('transient list outage preserves list and selection and recovers on subsequent tick', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  await page.goto(`/t/acme?run=${A}`); await selected(page, A, 'issue:Acme/Widgets#1');
  api.state.listOutage = true;
  await expect(page.getByText('Run list stale; retrying automatically.')).toBeVisible({ timeout: 10_000 });
  await selected(page, A, 'issue:Acme/Widgets#1');
  api.state.listOutage = false; add(B, 'issue:Acme/Widgets#2', 2);
  await expect(row(page, B)).toBeVisible({ timeout: 10_000 });
  await selected(page, A, 'issue:Acme/Widgets#1');
  await expect(page.getByText('Run list stale; retrying automatically.')).toHaveCount(0);
});

test('slow list reads are single-flight and polling remains GET-only', async ({ page }) => {
  api.state.delays['/tenants/acme/runs'] = 6500;
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect.poll(() => api.state.requests.filter(r => r === 'GET /tenants/acme/runs').length).toBe(1);
  await page.waitForTimeout(5500);
  expect(api.state.requests.filter(r => r === 'GET /tenants/acme/runs')).toHaveLength(1);
  expect(api.state.requests.some(r => r.startsWith('POST'))).toBe(false);
  await expect(page.locator('[data-issue-key]')).toHaveText(KNOWN_RUN_ISSUE);
});

test('manual issue load blocks auto-selection of a later arriving run', async ({ page }) => {
  await page.goto('/t/acme');
  await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  await page.locator('#issue-key').fill('issue:Acme/Widgets#manual');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
  add(A, 'issue:Acme/Widgets#1', 1);
  await expect(row(page, A)).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
});

test('late graph reads and evidence paths cannot replace a newer selected run', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#slow', 1); add(B, 'issue:Acme/Widgets#2', 2);
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(row(page, A)).toBeVisible();
  await row(page, A).click();
  await expect.poll(() => api.state.lastKeys).toEqual(['issue:Acme/Widgets#slow']);
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await page.waitForTimeout(1400);
  await selected(page, B, 'issue:Acme/Widgets#2');
  let pathStarted = false;
  await page.route('**/tenants/acme/path?*', async route => {
    pathStarted = true;
    await new Promise(resolve => setTimeout(resolve, 1200));
    try { await route.continue(); } catch { /* Superseded request was aborted. */ }
  });
  await page.getByRole('button', { name: 'Show evidence: label:Acme/Widgets/graph', exact: true }).click();
  await expect.poll(() => pathStarted).toBe(true);
  await page.locator('#issue-key').fill('issue:Acme/Widgets#manual');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
  await page.waitForTimeout(1400);
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
  await expect(page.locator('.citation[aria-pressed="true"]')).toHaveCount(0);
});
