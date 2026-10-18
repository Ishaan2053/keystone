/**
 * B11 — the three write-adjacent flows in API mode, against the fake API:
 * the issue input starts a triage and selects the new run, a suggested
 * reviewer can be rejected (then re-triaged), and the Ask box renders cited
 * answers with the same chips as triage claims.
 */
import { test, expect, type Page } from '@playwright/test';
import { evidenceName } from './evidence-name';
import { ASK_EVIDENCE, buildRecallRun, createFakeApi, KNOWN_RUN, KNOWN_RUN_ISSUE, startFakeApi, stopFakeApi, type FakeApi } from './fake-api';

const NEW_RUN = '22222222-3333-4444-8555-666666666666';

/**
 * Reject the known run's suggested reviewer and return the feedback key the fake API
 * actually returned, so the Re-triage expectation tracks the API, not a literal.
 */
async function rejectReviewer(page: Page, reason = 'On leave this month'): Promise<string> {
  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await assign.getByRole('button', { name: 'Reject @acme-maintainer-a' }).click();
  await assign.getByLabel(/Why\? Keystone will remember this as a rule/).fill(reason);
  const [response] = await Promise.all([
    page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/feedback')),
    assign.getByRole('button', { name: 'Reject reviewer' }).click(),
  ]);
  await expect(assign.locator('[data-rule-key]')).toHaveText('rule:acme-avoid-1');
  return (await response.json()).feedback_key as string;
}

/** Route only the feedback POSTs (a preflight OPTIONS must pass straight through). */
async function routeFeedback(page: Page, handle: (route: import('@playwright/test').Route) => Promise<void>): Promise<void> {
  await page.route('**/runs/*/feedback', async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return; }
    await handle(route);
  });
}

let api: FakeApi;

test.beforeAll(async () => {
  api = createFakeApi();
  await startFakeApi(api);
});

test.afterAll(async () => {
  if (api) await stopFakeApi(api);
});

test.beforeEach(() => {
  const recall = buildRecallRun('acme');
  recall.data!.run.run_key = `run:${NEW_RUN}`;
  recall.data!.triage.run_key = `run:${NEW_RUN}`;
  recall.data!.run.summary = 'New run from the UI.';
  Object.assign(api.state, {
    scenario: 'ok', requests: [], runs: { acme: [] }, recalls: { acme: { [NEW_RUN]: recall } },
    triageScenario: 'ok', triageRunKey: `run:${NEW_RUN}`, triageDelay: 0, triageRequests: [],
    askScenario: 'ok', askRequests: [], feedbackRequests: [],
  });
});

test('the issue input starts a triage, shows progress, and selects the new run', async ({ page }) => {
  api.state.triageDelay = 800;
  await page.goto(`/t/acme?issue=${encodeURIComponent(KNOWN_RUN_ISSUE)}`);
  await expect(page.locator('#issue-key')).toHaveValue(KNOWN_RUN_ISSUE);
  api.state.runs.acme = [{ ...buildRecallRun('acme').data!.run, run_key: `run:${NEW_RUN}`, at: 1760500000 }];
  const runButton = page.getByRole('button', { name: 'Run triage', exact: true });
  await runButton.click();
  await expect(page.locator('[data-run-triage]')).toBeDisabled();
  await expect(page.locator('[data-triage-status]')).toContainText(`Running triage on ${KNOWN_RUN_ISSUE}`);
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  await expect(page).toHaveURL(new RegExp(`run=${NEW_RUN}`));
  await expect(page.locator(`[data-live-run="run:${NEW_RUN}"]`)).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-run-triage]')).toBeEnabled();
  expect(api.state.triageRequests).toEqual([{ tenant: 'acme', body: { issue_key: KNOWN_RUN_ISSUE } }]);
});

test('a bare number triages by issue_number', async ({ page }) => {
  await page.goto('/t/acme?issue=issue%3AAcme%2FWidgets%23manual');
  await page.locator('#issue-key').fill('#42');
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  expect(api.state.triageRequests[0].body).toEqual({ issue_number: 42 });
});

test('free text triages as a title; an ambiguous number shows the API detail', async ({ page }) => {
  await page.goto('/t/acme?issue=issue%3AAcme%2FWidgets%23manual');
  await page.locator('#issue-key').fill('Widget crashes on resize');
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  expect(api.state.triageRequests[0].body).toEqual({ title: 'Widget crashes on resize' });

  api.state.triageScenario = 'ambiguous';
  await page.locator('#issue-key').fill('#42');
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect(page.locator('[data-live-error]')).toContainText('Enter the full key instead: issue:Acme/Widgets#42, issue:Acme/Gadgets#42.');
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
});

test('reject a suggested reviewer, see the rule, then re-triage into a new run', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await expect(page.locator('[data-action-key="action:acme-label-1"]').getByRole('button', { name: /Reject/ })).toHaveCount(0);
  const feedbackKey = await rejectReviewer(page);
  await expect(assign).toHaveAttribute('data-action-status', 'rejected');
  expect(api.state.feedbackRequests).toEqual([{
    tenant: 'acme',
    run: KNOWN_RUN,
    body: { action_key: 'action:acme-assign-1', verdict: 'reject', reason: 'On leave this month', scope: 'module' },
  }]);
  // Reject is secondary: the card keeps exactly one primary (amber) action.
  await expect(page.locator('.triage-panel .button-primary')).toHaveCount(1);
  // Saving feedback never starts a triage by itself.
  expect(api.state.triageRequests).toEqual([]);

  await assign.getByRole('button', { name: 'Re-triage' }).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  expect(api.state.triageRequests).toEqual([{
    tenant: 'acme',
    body: { issue_key: KNOWN_RUN_ISSUE, feedback_parent_run: KNOWN_RUN, feedback_key: feedbackKey },
  }]);
});

test('delayed feedback cannot enrich another run', async ({ page }) => {
  const second = buildRecallRun('acme');
  second.data!.run.run_key = `run:${NEW_RUN}`;
  second.data!.triage.run_key = `run:${NEW_RUN}`;
  api.state.recalls.acme[NEW_RUN] = second;
  api.state.runs.acme = [
    { ...buildRecallRun('acme').data!.run, at: 1760500000 },
    { ...second.data!.run, at: 1760400000 },
  ];
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);

  // Hold the feedback response until after the view has moved on.
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  await routeFeedback(page, async route => { await gate; await route.continue(); });

  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await assign.getByRole('button', { name: 'Reject @acme-maintainer-a' }).click();
  await assign.getByLabel(/Why\? Keystone will remember this as a rule/).fill('On leave this month');
  await assign.getByRole('button', { name: 'Reject reviewer' }).click();

  await page.locator(`[data-live-run="run:${NEW_RUN}"]`).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  expect(api.state.triageRequests).toEqual([]);

  // The late response belongs to the run it was recorded on, never to this one.
  const late = page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/feedback'));
  release();
  await late;
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect.poll(() => api.state.triageRequests.length).toBe(1);
  expect(api.state.triageRequests[0].body).toEqual({ issue_key: KNOWN_RUN_ISSUE });
});

test('failed feedback never supplies context', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await routeFeedback(page, async route => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      headers: { 'Access-Control-Allow-Origin': '*' },
      body: JSON.stringify({ detail: 'Live graph call failed' }),
    });
  });

  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await assign.getByRole('button', { name: 'Reject @acme-maintainer-a' }).click();
  await assign.getByLabel(/Why\? Keystone will remember this as a rule/).fill('On leave this month');
  await assign.getByRole('button', { name: 'Reject reviewer' }).click();

  await expect(assign.locator('[role="alert"]')).toContainText('Reject failed');
  await expect(assign.getByRole('button', { name: 'Re-triage' })).toHaveCount(0);
  expect(api.state.triageRequests).toEqual([]);

  // Nothing was persisted, so the only triage left is the plain manual one.
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect.poll(() => api.state.triageRequests.length).toBe(1);
  expect(api.state.triageRequests[0].body).toEqual({ issue_key: KNOWN_RUN_ISSUE });
});

test('stale triage completion preserves feedback saved in the new view', async ({ page }) => {
  api.state.runs.acme = [buildRecallRun('acme').data!.run];
  await page.goto('/t/acme?issue=issue%3AAcme%2FWidgets%23manual');
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  let first = true;
  await page.route('**/tenants/acme/triage', async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return; }
    if (first) { first = false; await gate; }
    await route.continue();
  });
  await page.locator('#issue-key').fill('#42');
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect(page.locator('[data-run-triage]')).toBeDisabled();
  await page.locator(`[data-live-run="run:${KNOWN_RUN}"]`).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);
  const feedbackKey = await rejectReviewer(page);
  release();
  await expect(page.locator('[data-triage-status]')).toContainText(`Triage recorded as run:${NEW_RUN}`);
  await expect(page.locator('[data-run-triage]')).toBeEnabled();
  await page.locator('[data-action-key="action:acme-assign-1"]').getByRole('button', { name: 'Re-triage' }).click();
  await expect.poll(() => api.state.triageRequests.length).toBe(2);
  expect(api.state.triageRequests[1].body).toEqual({
    issue_key: KNOWN_RUN_ISSUE, feedback_parent_run: KNOWN_RUN, feedback_key: feedbackKey,
  });
});

test('Run triage stays manual after feedback', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await rejectReviewer(page);
  await expect(assign.getByRole('button', { name: 'Re-triage' })).toHaveCount(1);

  // Only the explicit Re-triage carries the lineage; the sidebar's triage never does.
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect.poll(() => api.state.triageRequests.length).toBe(1);
  expect(api.state.triageRequests[0].body).toEqual({ issue_key: KNOWN_RUN_ISSUE });
});

test('reloading drops pending context', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await rejectReviewer(page);
  await expect(page.locator('[data-action-key="action:acme-assign-1"]').getByRole('button', { name: 'Re-triage' })).toHaveCount(1);

  await page.reload();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);
  // The session-local lineage did not survive: no Re-triage, and triage is manual.
  await expect(page.locator('[data-action-key="action:acme-assign-1"]').getByRole('button', { name: 'Re-triage' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect.poll(() => api.state.triageRequests.length).toBe(1);
  expect(api.state.triageRequests[0].body).toEqual({ issue_key: KNOWN_RUN_ISSUE });
});

test('retrying failed retriage keeps the same feedback identity', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  const feedbackKey = await rejectReviewer(page);
  const lineage = { issue_key: KNOWN_RUN_ISSUE, feedback_parent_run: KNOWN_RUN, feedback_key: feedbackKey };

  api.state.triageScenario = 'fail';
  await assign.getByRole('button', { name: 'Re-triage' }).click();
  await expect(page.locator('[data-live-error]')).toContainText('Triage failed');
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);

  api.state.triageScenario = 'ok';
  await assign.getByRole('button', { name: 'Re-triage' }).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  expect(api.state.triageRequests).toEqual([
    { tenant: 'acme', body: lineage },
    { tenant: 'acme', body: lineage },
  ]);
});

test('Load issue graph needs a full key; a number or free text shows a hint and reads nothing', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);
  for (const value of ['#42', '42', 'Widget crashes on resize']) {
    api.state.lastKeys = null;
    await page.locator('#issue-key').fill(value);
    await page.getByRole('button', { name: 'Load issue graph' }).click();
    await expect(page.locator('[data-issue-hint]')).toContainText('needs a full key');
    expect(api.state.lastKeys).toBeNull();
    await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);
  }
  await page.locator('#issue-key').fill('issue:Acme/Widgets#7');
  await expect(page.locator('[data-issue-hint]')).toHaveCount(0);
});

test('a finished triage does not steal the view after the user picked another run', async ({ page }) => {
  api.state.triageDelay = 1500;
  api.state.runs.acme = [buildRecallRun('acme').data!.run];
  await page.goto('/t/acme?issue=issue%3AAcme%2FWidgets%23manual');
  await page.locator('#issue-key').fill('#42');
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect(page.locator('[data-run-triage]')).toBeDisabled();
  await page.locator(`[data-live-run="run:${KNOWN_RUN}"]`).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);
  await expect(page.locator('[data-triage-status]')).toContainText(`Triage recorded as run:${NEW_RUN}`);
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${KNOWN_RUN}`);
  await expect(page).toHaveURL(new RegExp(`run=${KNOWN_RUN}`));
});

test('a raw-title run has no issue anchor; its chips resolve, and Re-triage repeats the title', async ({ page }) => {
  const adhoc = buildRecallRun('acme');
  adhoc.data!.run.run_key = `run:${NEW_RUN}`;
  adhoc.data!.triage.run_key = `run:${NEW_RUN}`;
  adhoc.data!.run.issue_key = 'adhoc';
  api.state.recalls.acme[NEW_RUN] = adhoc;
  await page.goto('/t/acme?issue=issue%3AAcme%2FWidgets%23manual');
  await page.locator('#issue-key').fill('Widget crashes on resize');
  await page.getByRole('button', { name: 'Run triage', exact: true }).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  await expect(page.locator('#issue-key')).toHaveValue('Widget crashes on resize');
  await expect(page.locator('[data-issue-key]')).toHaveCount(0);
  expect(api.state.requests.some(r => r.includes('adhoc'))).toBe(false);
  expect(api.state.lastKeys).not.toContain('adhoc');

  const chip = page.locator('[data-claim-section="labels"]').getByRole('button', { name: evidenceName('label:Acme/Widgets/graph') });
  await chip.click();
  await expect(chip).toHaveAttribute('aria-pressed', 'true');

  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await assign.getByRole('button', { name: 'Reject @acme-maintainer-a' }).click();
  await assign.getByLabel(/Why\? Keystone will remember this as a rule/).fill('Not this area');
  await assign.getByRole('button', { name: 'Reject reviewer' }).click();
  await assign.getByRole('button', { name: 'Re-triage' }).click();
  await expect.poll(() => api.state.triageRequests.length).toBe(2);
  // A raw-title run has no issue key, so it never carries feedback lineage.
  expect(api.state.triageRequests[1].body).toEqual({ title: 'Widget crashes on resize', body: '' });
});

test('a reopened raw-title run re-triages from its recorded request; an older one without it explains why not', async ({ page }) => {
  const adhoc = buildRecallRun('acme');
  adhoc.data!.run.run_key = `run:${NEW_RUN}`;
  adhoc.data!.triage.run_key = `run:${NEW_RUN}`;
  adhoc.data!.run.issue_key = 'adhoc';
  adhoc.data!.request = { issue_key: null, issue_number: null, title: 'Widget crashes on resize', body: 'Steps: resize twice' };
  api.state.recalls.acme[NEW_RUN] = adhoc;
  // Opened by link, as after a reload: nothing was sent from this session.
  await page.goto(`/t/acme?run=${NEW_RUN}`);
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await assign.getByRole('button', { name: 'Reject @acme-maintainer-a' }).click();
  await assign.getByLabel(/Why\? Keystone will remember this as a rule/).fill('Not this area');
  await assign.getByRole('button', { name: 'Reject reviewer' }).click();
  await assign.getByRole('button', { name: 'Re-triage' }).click();
  await expect.poll(() => api.state.triageRequests.length).toBe(1);
  expect(api.state.triageRequests[0].body).toEqual({ title: 'Widget crashes on resize', body: 'Steps: resize twice' });

  delete adhoc.data!.request;
  await page.goto(`/t/acme?run=${NEW_RUN}`);
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  await assign.getByRole('button', { name: 'Reject @acme-maintainer-a' }).click();
  await assign.getByLabel(/Why\? Keystone will remember this as a rule/).fill('Not this area');
  await assign.getByRole('button', { name: 'Reject reviewer' }).click();
  await expect(assign).toContainText('Re-triage needs an issue key or the original title; this run has neither.');
  await expect(assign.getByRole('button', { name: 'Re-triage' })).toHaveCount(0);
});

test('Ask renders cited claims with evidence chips and the ask trace', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  // With a run selected, Ask is the issue panel's third tab.
  await page.getByRole('tab', { name: 'Ask' }).click();
  await page.locator('#ask-question').fill('Why were widgets split?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  const answer = page.locator('[data-ask-status]');
  await expect(answer).toContainText('Widgets were split out');
  await expect(answer).not.toContainText('An uncited model claim');
  await expect(answer).toContainText('Uncited answer claims were hidden.');
  expect(api.state.askRequests).toEqual([{ tenant: 'acme', body: { question: 'Why were widgets split?' } }]);

  const chip = answer.getByRole('button', { name: evidenceName(ASK_EVIDENCE[0]) });
  await chip.click();
  await expect(chip).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.trace-inline [data-ask-trace] [data-trace-row]')).toHaveAttribute('data-supporting', 'true');
  await expect(page.locator('.triage-panel .button-primary')).toHaveCount(1);

  api.state.askScenario = 'empty';
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.locator('[data-ask-empty]')).toContainText('No evidence');

  api.state.askScenario = 'fail';
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(answer).toContainText('Ask is unavailable');
  await expect(answer).not.toContainText('Widgets were split out');
});
