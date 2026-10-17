/**
 * B11 — the three write-adjacent flows in API mode, against the fake API:
 * the issue input starts a triage and selects the new run, a suggested
 * reviewer can be rejected (then re-triaged), and the Ask box renders cited
 * answers with the same chips as triage claims.
 */
import { test, expect } from '@playwright/test';
import { ASK_EVIDENCE, buildRecallRun, createFakeApi, KNOWN_RUN, KNOWN_RUN_ISSUE, startFakeApi, stopFakeApi, type FakeApi } from './fake-api';

const NEW_RUN = '22222222-3333-4444-8555-666666666666';

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
  await expect(page.locator('[data-triage-status]')).toContainText('Running triage on #42');
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  await expect(page).toHaveURL(new RegExp(`run=${NEW_RUN}`));
  await expect(page.locator(`[data-live-run="run:${NEW_RUN}"]`)).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-run-triage]')).toBeEnabled();
  expect(api.state.triageRequests).toEqual([{ tenant: 'acme', body: { issue_number: 42 } }]);
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
  await expect(page.locator('[data-live-error]')).toContainText('matches issue:Acme/Widgets#42, issue:Acme/Gadgets#42');
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
});

test('reject a suggested reviewer, see the rule, then re-triage into a new run', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  const assign = page.locator('[data-action-key="action:acme-assign-1"]');
  await expect(page.locator('[data-action-key="action:acme-label-1"]').getByRole('button', { name: /Reject/ })).toHaveCount(0);
  await assign.getByRole('button', { name: 'Reject @acme-maintainer-a' }).click();
  await assign.getByLabel(/Why\? Keystone will remember this as a rule/).fill('On leave this month');
  await assign.getByRole('button', { name: 'Reject reviewer' }).click();
  await expect(assign.locator('[data-rule-key]')).toHaveText('rule:acme-avoid-1');
  await expect(assign).toHaveAttribute('data-action-status', 'rejected');
  expect(api.state.feedbackRequests).toEqual([{
    tenant: 'acme',
    run: KNOWN_RUN,
    body: { action_key: 'action:acme-assign-1', verdict: 'reject', reason: 'On leave this month', scope: 'module' },
  }]);
  // Reject is secondary: the card keeps exactly one primary (amber) action.
  await expect(page.locator('.triage-panel .button-primary')).toHaveCount(1);

  await assign.getByRole('button', { name: 'Re-triage' }).click();
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${NEW_RUN}`);
  expect(api.state.triageRequests).toEqual([{ tenant: 'acme', body: { issue_number: 42 } }]);
});

test('Ask renders cited claims with evidence chips and the ask trace', async ({ page }) => {
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await page.locator('#ask-question').fill('Why were widgets split?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  const answer = page.locator('[data-ask-status]');
  await expect(answer).toContainText('Widgets were split out');
  await expect(answer).not.toContainText('An uncited model claim');
  await expect(answer).toContainText('Uncited answer claims were hidden.');
  expect(api.state.askRequests).toEqual([{ tenant: 'acme', body: { question: 'Why were widgets split?' } }]);

  const chip = answer.getByRole('button', { name: `Show evidence: ${ASK_EVIDENCE[0]}` });
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
