import { test, expect, type Page } from '@playwright/test';
import type { Stats } from '../lib/types';
import { createFakeApi, startFakeApi, stopFakeApi, KNOWN_RUN, type FakeApi } from './fake-api';

let api: FakeApi;
function sample(): Stats {
  return {
    node_counts: { Issue: 7 }, edge_counts: { ABOUT: 2 }, last_ingest_at: null,
    proof: { n: 10, rows: [], methods: {
      graph: { reviewer_hit_at_3: 0.6, module_hit_at_1: 0.51, module_hit_at_3: 0.82, label_precision_at_2: 0.733 },
      codeowners: { reviewer_hit_at_3: 0.7, module_hit_at_1: 0.62, module_hit_at_3: 0.9, label_precision_at_2: 0.8 },
      similar_issues: { reviewer_hit_at_3: 0.4, module_hit_at_1: 0.3, module_hit_at_3: 0.5, label_precision_at_2: 0.6 },
    } },
    latencies: [{ tool: 'affected_modules', p50_ms: 12.5, p95_ms: 43 }],
  };
}
const trigger = (page: Page) => page.getByRole('button', { name: /Proof/ });
const panel = (page: Page) => page.getByRole('dialog', { name: 'Proof' });
const reads = () => api.state.requests.filter(r => r === 'GET /tenants/acme/stats').length;
test.beforeAll(async () => { api = createFakeApi(); await startFakeApi(api); });
test.afterAll(async () => { await stopFakeApi(api); });
test.beforeEach(() => { Object.assign(api.state, { stats: { acme: sample() }, statsOutage: false, delays: {}, requests: [], runs: {}, scenario: 'ok' }); });

test('headline and four comparisons expose exact rates, graph losses and latency units', async ({ page }) => {
  await page.goto('/t/acme');
  await expect(trigger(page)).toContainText('60%');
  await expect(trigger(page).locator('.type-metric')).toHaveText('60%');
  await trigger(page).click();
  await expect(panel(page).getByText('n=10', { exact: true })).toBeVisible();
  const groups = panel(page).locator('[data-proof-group]');
  await expect(groups).toHaveCount(4);
  for (const [i, values] of [['60%', '70%', '40%'], ['51%', '62%', '30%'], ['82%', '90%', '50%'], ['73.3%', '80%', '60%']].entries()) {
    await expect(groups.nth(i).locator('[data-proof-value]')).toHaveText(values);
    await expect(groups.nth(i)).toContainText('Graph');
    await expect(groups.nth(i)).toContainText('CODEOWNERS');
    await expect(groups.nth(i)).toContainText('Similar issues');
  }
  await expect(panel(page).getByRole('columnheader')).toHaveText(['Tool', 'p50 (ms)', 'p95 (ms)']);
  await expect(panel(page).getByRole('row', { name: /affected_modules/ })).toContainText('12.5 ms');
  await expect(panel(page).getByRole('row', { name: /affected_modules/ })).toContainText('43 ms');
});

for (const kind of ['zero', 'absent', 'empty'] as const) {
  test(`${kind} measurements do not conflate missing Proof with zero; latency is independent`, async ({ page }) => {
    if (kind === 'zero') api.state.stats.acme.proof!.methods.graph.reviewer_hit_at_3 = 0;
    if (kind === 'absent') api.state.stats.acme.proof = null;
    if (kind === 'empty') api.state.stats.acme.proof!.n = 0;
    await page.goto('/t/acme');
    await expect(trigger(page)).toContainText(kind === 'zero' ? '0%' : 'Proof not measured');
    await expect(trigger(page).locator('.type-metric')).toHaveCount(kind === 'zero' ? 1 : 0);
    await trigger(page).click();
    await expect(panel(page).getByRole('row', { name: /affected_modules/ })).toBeVisible();
    if (kind === 'absent') await expect(panel(page).locator('[data-proof-group]')).toHaveCount(0);
    if (kind === 'empty') {
      await expect(panel(page).getByText('Proof not measured', { exact: true })).toBeVisible();
      await expect(panel(page).getByText('n=0', { exact: true })).toBeVisible();
      await expect(panel(page).locator('[data-proof-group]')).toHaveCount(0);
      await expect(panel(page).locator('[data-proof-value]')).toHaveCount(0);
      await expect(panel(page)).not.toContainText('%');
      await expect(panel(page).getByText('No evaluable sample. Rates need at least one evaluated issue.')).toBeVisible();
    }
  });
}

test('missing latencies have their own empty state', async ({ page }) => {
  api.state.stats.acme.latencies = [];
  await page.goto('/t/acme'); await trigger(page).click();
  await expect(panel(page).getByText('Tool latencies not measured')).toBeVisible();
  await expect(panel(page).getByText('n=10', { exact: true })).toBeVisible();
});

for (const [entry, url] of [
  ['bare', '/t/acme'],
  ['run', `/t/acme?run=${KNOWN_RUN}`],
  ['issue', '/t/acme?issue=issue%3AAcme%2FWidgets%2342'],
] as const) {
test(`initial stats-only outage (${entry}) is unavailable, not unmeasured, and Retry recovers one shared snapshot`, async ({ page }) => {
  api.state.statsOutage = true;
  await page.goto(url);
  await expect(page.getByRole('combobox', { name: 'Tenant' })).toContainText('Acme');
  await expect(page.locator('[data-source="live"]')).toContainText('Live data');
  await expect(page.locator('[data-live-error]')).toContainText('Live data unavailable');
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();
  if (entry === 'issue') await expect(page.getByLabel('Issue key')).toHaveValue('issue:Acme/Widgets#42');
  await expect(trigger(page)).toContainText('Proof unavailable');
  await expect(trigger(page)).not.toContainText('not measured');
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/tenants/acme/stats', async route => { await gate; await route.continue(); }, { times: 1 });
  await trigger(page).click();
  await expect(panel(page).getByText('Loading measurements…', { exact: true })).toBeVisible();
  await expect(page.locator('.app-header')).toContainText('Proof loading');
  await expect(panel(page).getByRole('button', { name: 'Retry' })).toBeDisabled();
  await expect(panel(page)).not.toContainText('not measured');
  release();
  await expect(panel(page).getByRole('button', { name: 'Retry' })).toBeEnabled();
  await expect(panel(page).getByRole('alert')).toContainText('Unable to refresh stats');
  await expect(panel(page).getByText('Stats unavailable', { exact: true })).toBeVisible();
  await expect(panel(page)).not.toContainText('not measured');
  await expect(panel(page).locator('[data-proof-value]')).toHaveCount(0);
  api.state.statsOutage = false;
  await panel(page).getByRole('button', { name: 'Retry' }).click();
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('60%');
  await expect(panel(page).getByRole('row', { name: /affected_modules/ })).toContainText('12.5 ms');
  await expect(panel(page).getByRole('alert')).toHaveCount(0);
  await expect(page.locator('.app-header .type-metric')).toHaveText('60%');
  await expect(page.locator('.canvas-panel')).toContainText('Issue: 7');
  // Retrying stats alone must not silently mark a failed issue/run load successful.
  await expect(page.locator('[data-live-error]')).toContainText('Live data unavailable');
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
});
}

test('closing a pending initial stats read returns to unavailable and reopening can retry', async ({ page }) => {
  api.state.statsOutage = true;
  await page.goto('/t/acme');
  await expect(trigger(page)).toHaveText('Proof unavailable');
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  api.state.statsOutage = false;
  let release!: () => void;
  let captured!: () => void;
  let handled!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const responseCaptured = new Promise<void>(resolve => { captured = resolve; });
  const delivered = new Promise<void>(resolve => { handled = resolve; });
  await page.route('**/tenants/acme/stats', async route => {
    const response = await route.fetch();
    captured();
    await gate;
    await route.fulfill({ response }).catch(() => {});
    handled();
  }, { times: 1 });
  await trigger(page).click();
  await responseCaptured;
  await expect(page.locator('.app-header')).toContainText('Proof loading');
  await expect(panel(page).getByText('Loading measurements…', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(trigger(page)).toHaveText('Proof unavailable');
  const closedReads = reads();
  await page.clock.runFor(15000);
  expect(reads()).toBe(closedReads);
  await expect(trigger(page)).toHaveText('Proof unavailable');
  release();
  await delivered;
  await page.clock.runFor(32);
  await expect(trigger(page)).toHaveText('Proof unavailable');
  await expect(page.locator('.canvas-panel')).not.toContainText('Issue: 7');
  api.state.statsOutage = true;
  await trigger(page).click();
  await expect(panel(page).getByRole('button', { name: 'Retry' })).toBeEnabled();
  await expect(panel(page).getByRole('alert')).toContainText('Unable to refresh stats');
  await expect(panel(page).locator('[data-proof-value]')).toHaveCount(0);
  api.state.statsOutage = false;
  api.state.stats.acme.proof!.methods.graph.reviewer_hit_at_3 = 0.3;
  await panel(page).getByRole('button', { name: 'Retry' }).click();
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('30%');
  await expect(page.locator('.app-header .type-metric')).toHaveText('30%');
  await expect(page.locator('.canvas-panel')).toContainText('Issue: 7');
  await expect(panel(page).getByRole('alert')).toHaveCount(0);
  expect(api.state.applyRequests).toHaveLength(0);
});

test('immediate open, 5-second refresh, shared snapshot, stale retention and Retry; close stops reads', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(trigger(page)).toContainText('60%');
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  const before = reads();
  await trigger(page).click();
  await expect.poll(reads).toBe(before + 1);
  api.state.stats.acme.proof!.methods.graph.reviewer_hit_at_3 = 0.2;
  api.state.stats.acme.node_counts.Issue = 99;
  await page.clock.runFor(4999);
  expect(reads()).toBe(before + 1);
  await page.clock.runFor(1);
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('20%');
  await expect(page.locator('.app-header .type-metric')).toHaveText('20%');
  await expect(page.locator('.canvas-panel')).toContainText('99');
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);
  api.state.statsOutage = true;
  await page.clock.runFor(5000);
  await expect(panel(page).getByRole('alert')).toContainText('Stale');
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('20%');
  api.state.statsOutage = false;
  api.state.stats.acme.proof!.methods.graph.reviewer_hit_at_3 = 0.3;
  await panel(page).getByRole('button', { name: 'Retry' }).click();
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('30%');
  await page.keyboard.press('Escape');
  const closed = reads();
  await page.clock.runFor(15000);
  expect(reads()).toBe(closed);
  expect(api.state.applyRequests).toHaveLength(0);
});

test('slow reads are single-flight, closing cancels and reopening does not accept late results', async ({ page }) => {
  await page.goto('/t/acme'); await page.clock.install();
  await page.clock.pauseAt(new Date());
  let release!: () => void;
  let handled!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const delivered = new Promise<void>(resolve => { handled = resolve; });
  let first = true;
  await page.route('**/tenants/acme/stats', async route => {
    if (!first) { await route.continue(); return; }
    first = false;
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response }).catch(() => {});
    handled();
  });
  const before = reads();
  await trigger(page).click();
  await expect.poll(reads).toBe(before + 1);
  // Cross a polling tick, but stay below the shared client's 10-second timeout.
  await page.clock.runFor(5000);
  expect(reads()).toBe(before + 1);
  await page.keyboard.press('Escape');
  api.state.stats.acme.proof!.methods.graph.reviewer_hit_at_3 = 0.9;
  await trigger(page).click();
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('90%');
  release();
  await delivered;
  await expect.poll(reads).toBe(before + 2);
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('90%');
});

test('tenant switch cannot leak a delayed stats snapshot', async ({ page }) => {
  const other = sample(); other.proof!.methods.graph.reviewer_hit_at_3 = 0.1;
  api.state.stats.pallets = other;
  await page.goto('/t/acme');
  api.state.delays['/tenants/acme/stats'] = 1200;
  await trigger(page).click(); await expect.poll(reads).toBeGreaterThan(1);
  await page.keyboard.press('Escape');
  await page.getByRole('combobox', { name: 'Tenant' }).click();
  await page.getByRole('option', { name: 'Pallets' }).click();
  await expect(trigger(page)).toContainText('10%');
  await trigger(page).click();
  await page.waitForTimeout(1400);
  await expect(panel(page).locator('[data-proof-value]').first()).toHaveText('10%');
});

for (const theme of ['dark', 'light']) {
  test(`${theme}: keyboard open, Escape/focus return and phone-width long labels`, async ({ page }) => {
    api.state.stats.acme.latencies[0].tool = 'tool_with_a_very_long_unbroken_name_for_responsive_latency_measurements';
    await page.goto('/t/acme');
    if (theme === 'light') await page.getByRole('button', { name: 'Toggle color theme' }).click();
    await expect(page.locator('html')).toHaveClass(theme === 'light' ? /light/ : /^(?!.*light)/);
    await trigger(page).focus(); await page.keyboard.press('Enter');
    await expect(panel(page)).toBeVisible();
    await page.screenshot({ path: `test-results/proof-${theme}-desktop.png` });
    await page.keyboard.press('Escape');
    await expect(trigger(page)).toBeFocused();
    await page.setViewportSize({ width: 375, height: 812 });
    await trigger(page).click();
    await expect(panel(page).getByRole('row', { name: /tool_with/ })).toBeVisible();
    expect(await panel(page).evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/proof-${theme}-phone.png` });
  });
}
