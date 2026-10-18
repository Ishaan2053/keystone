import { test, expect, type Page, type Request } from '@playwright/test';
import { evidenceName } from './evidence-name';
import {
  buildRecallRun,
  createFakeApi,
  startFakeApi,
  stopFakeApi,
  KNOWN_RUN,
  KNOWN_RUN_ISSUE,
  type FakeApi,
} from './fake-api';

let api: FakeApi;
const A = 'aaaaaaaa-1111-4111-8111-111111111111';
const B = 'bbbbbbbb-2222-4222-8222-222222222222';
function add(uuid: string, issue: string, at: number, tenant = 'acme') {
  const body = buildRecallRun(tenant);
  body.data.run = {
    ...body.data.run,
    run_key: `run:${uuid}`,
    issue_key: issue,
    at,
    summary: `Selected ${issue}`,
  };
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
/**
 * Resolves once the first matching read finishes or fails (an aborted read fails
 * at once), then lets React commit, as `slowReadSettled` in api-data.spec.ts does.
 */
function readSettled(page: Page, matches: (url: URL) => boolean): () => Promise<void> {
  const settled = new Promise<void>((resolve) => {
    const onSettled = (request: Request) => {
      if (!matches(new URL(request.url()))) return;
      page.off('requestfinished', onSettled);
      page.off('requestfailed', onSettled);
      resolve();
    };
    page.on('requestfinished', onSettled);
    page.on('requestfailed', onSettled);
  });
  return async () => {
    await settled;
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0))),
    );
  };
}
const runRead = (uuid: string) => (url: URL) => url.pathname === `/tenants/acme/runs/${uuid}`;
const slowGraph = (url: URL) =>
  url.pathname === '/tenants/acme/subgraph' &&
  (url.searchParams.get('keys') ?? '').toLowerCase().includes('slow');
test.beforeAll(async () => {
  api = createFakeApi();
  await startFakeApi(api);
});
test.afterAll(async () => {
  await stopFakeApi(api);
});
test.beforeEach(() => {
  Object.assign(api.state, {
    scenario: 'ok',
    runs: {},
    recalls: {},
    delays: {},
    listOutage: false,
    requests: [],
    applyRequests: [],
  });
});

test('immediate newest-first deduplicated capped list and empty-view auto-selection', async ({
  page,
}) => {
  for (let i = 1; i <= 23; i++)
    add(
      `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
      `issue:Acme/Widgets#${i}`,
      1760400000 + i,
    );
  api.state.runs.acme.push(api.state.runs.acme[22]);
  const listRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/tenants/acme/runs',
  );
  await page.goto('/t/acme');
  expect(new URL((await listRequest).url()).searchParams.get('limit')).toBe('20');
  await expect(page.locator('[data-live-run]')).toHaveCount(20);
  await expect(page.locator('[data-live-run]').first()).toContainText('#23');
  await expect(page.locator('[data-live-run]').last()).toContainText('#4');
  await expect(page.locator('[data-run-key]')).toHaveText(
    'run:00000000-0000-4000-8000-000000000023',
  );
  await expect(page.locator('[data-live-run] time').first()).toHaveAttribute(
    'datetime',
    new Date(1760400023 * 1000).toISOString(),
  );
  expect(api.state.requests).toContain('GET /tenants/acme/runs');
  expect(api.state.requests.filter((r) => r.includes('/runs/'))).toHaveLength(1);
  expect(api.state.applyRequests).toHaveLength(0);
});

test('arrival within ten seconds marks New without reloading or replacing selected evidence', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  await page.goto(`/t/acme?run=${A}`);
  await selected(page, A, 'issue:Acme/Widgets#1');
  await page.evaluate(() => {
    (window as unknown as { documentMarker: boolean }).documentMarker = true;
  });
  const graph = page.locator('.canvas-panel');
  await graph.evaluate((el) => el.setAttribute('data-mounted-marker', 'kept'));
  add(B, 'issue:Acme/Widgets#2', 2);
  await expect(row(page, B)).toBeVisible({ timeout: 10_000 });
  await expect(row(page, B)).toContainText('New');
  await selected(page, A, 'issue:Acme/Widgets#1');
  await expect(graph).toHaveAttribute('data-mounted-marker', 'kept');
  expect(
    await page.evaluate(() => (window as unknown as { documentMarker: boolean }).documentMarker),
  ).toBe(true);
  expect(api.state.applyRequests).toHaveLength(0);
});

test('empty list is distinct from outage and auto-selects an arriving run', async ({ page }) => {
  await page.goto('/t/acme');
  await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  add(A, 'issue:Acme/Widgets#1', 1);
  await expect(page.locator('[data-run-key]')).toHaveText(`run:${A}`, { timeout: 10_000 });
});

test('the run list marks fallback runs and nothing else', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  add(B, 'issue:Acme/Widgets#2', 2).data.run.fallback = true;
  await page.goto(`/t/acme?run=${A}`);
  // A normal run carries no status token; a fallback run is marked as one.
  await expect(row(page, A).getByText('Fallback', { exact: true })).toHaveCount(0);
  await expect(row(page, B).getByText('Fallback', { exact: true })).toBeVisible();
});

for (const failure of ['recall', 'graph']) {
  test(`New survives failed ${failure} selection and clears only after successful inspection`, async ({
    page,
  }) => {
    add(A, 'issue:Acme/Widgets#1', 1);
    await page.goto(`/t/acme?run=${A}`);
    await selected(page, A, 'issue:Acme/Widgets#1');
    add(B, 'issue:Acme/Widgets#2', 2);
    const marker = row(page, B).getByText('New', { exact: true });
    await expect(marker).toBeVisible({ timeout: 10_000 });
    const pattern =
      failure === 'recall' ? `**/tenants/acme/runs/${B}` : '**/tenants/acme/subgraph?*';
    await page.route(pattern, (route) => route.fulfill({ status: 503, body: '{}' }));
    await row(page, B).click();
    await expect(page.locator('[data-live-error]')).toContainText('Unable to select run');
    await selected(page, A, 'issue:Acme/Widgets#1');
    await expect(marker).toBeVisible();
    await page.unroute(pattern);
    await row(page, B).click();
    await selected(page, B, 'issue:Acme/Widgets#2');
    await expect(marker).toHaveCount(0);
    await row(page, A).click();
    await selected(page, A, 'issue:Acme/Widgets#1');
    await expect(marker).toHaveCount(0);
    expect(api.state.applyRequests).toHaveLength(0);
  });
}

test('automatic selection replaces history, so Back leaves the workspace instead of re-selecting', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  const start = await page.evaluate(() => history.length);
  await page.goto('/t/acme');
  await selected(page, A, 'issue:Acme/Widgets#1');
  await expect(page).toHaveURL(new RegExp(`\\?run=${A}$`));
  // Only the navigation added an entry; the automatic ?run= replaced it.
  expect(await page.evaluate(() => history.length)).toBe(start + 1);
  await page.goBack();
  await expect(page).toHaveURL('about:blank');
  expect(
    api.state.requests.filter((request) => request === `GET /tenants/acme/runs/${A}`),
  ).toHaveLength(1);
});

test('an explicit run after an automatic one pushes history, and Back returns to the automatic run', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  add(B, 'issue:Acme/Widgets#2', 2);
  const start = await page.evaluate(() => history.length);
  await page.goto('/t/acme');
  await selected(page, B, 'issue:Acme/Widgets#2');
  const autoLength = await page.evaluate(() => history.length);
  expect(autoLength).toBe(start + 1);
  await row(page, A).click();
  await selected(page, A, 'issue:Acme/Widgets#1');
  await expect(page).toHaveURL(new RegExp(`\\?run=${A}$`));
  expect(await page.evaluate(() => history.length)).toBe(autoLength + 1);
  const recallB = page.waitForRequest(
    (request) => new URL(request.url()).pathname === `/tenants/acme/runs/${B}`,
  );
  await page.goBack();
  await recallB;
  await selected(page, B, 'issue:Acme/Widgets#2');
  await expect(page).toHaveURL(new RegExp(`\\?run=${B}$`));
  await page.goBack();
  await expect(page).toHaveURL('about:blank');
});

test('a failed automatic selection is not retried by every list poll', async ({ page }) => {
  const bad = add(A, 'issue:Acme/Widgets#1', 1);
  bad.data.triage.run_key = `run:${B}`;
  await page.goto('/t/acme');
  await expect(page.locator('[data-live-error]')).toContainText('Unable to select run');
  const initialPolls = api.state.requests.filter(
    (request) => request === 'GET /tenants/acme/runs',
  ).length;
  await expect
    .poll(
      () => api.state.requests.filter((request) => request === 'GET /tenants/acme/runs').length,
      { timeout: 10_000 },
    )
    .toBeGreaterThan(initialPolls);
  expect(
    api.state.requests.filter((request) => request === `GET /tenants/acme/runs/${A}`),
  ).toHaveLength(1);
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
});

for (const target of [
  '?run=not-a-uuid',
  '?run=99999999-9999-4999-8999-999999999999',
  '?issue=issue%3AAcme%2FWidgets%237',
]) {
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

test('switches all panes, resets Apply state and reconciles back/forward and reload', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  add(B, 'issue:Acme/Widgets#2', 2);
  await page.goto(`/t/acme?run=${A}`);
  await selected(page, A, 'issue:Acme/Widgets#1');
  await page.getByRole('button', { name: /Apply to sandbox/ }).click();
  await expect(page.getByText('Apply complete.')).toBeVisible();
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await expect(page.getByText('Apply complete.')).toHaveCount(0);
  await expect(page.getByRole('switch', { name: 'Dry run' })).toBeChecked();
  await page.goBack();
  await selected(page, A, 'issue:Acme/Widgets#1');
  await page.goForward();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await page.reload();
  await selected(page, B, 'issue:Acme/Widgets#2');
});

test('rapid selections and manual issue transitions cancel delayed run and issue reads', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  add(B, 'issue:Acme/Widgets#2', 2);
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(row(page, A)).toBeVisible();
  api.state.delays[`/tenants/acme/runs/${A}`] = 1200;
  let delayedRead = readSettled(page, runRead(A));
  await row(page, A).click();
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await delayedRead();
  await selected(page, B, 'issue:Acme/Widgets#2');
  delayedRead = readSettled(page, slowGraph);
  await page.locator('#issue-key').fill('issue:Acme/Widgets#slow');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await delayedRead();
  await selected(page, B, 'issue:Acme/Widgets#2');
  delayedRead = readSettled(page, runRead(A));
  await row(page, A).click();
  await page.locator('#issue-key').fill('issue:Acme/Widgets#manual');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
  await delayedRead();
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
});

test('failed or mismatched recall keeps prior coherent view and Apply binding', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  const bad = add(B, 'issue:Acme/Widgets#2', 2);
  bad.data.triage.run_key = `run:${A}`;
  await page.goto(`/t/acme?run=${A}`);
  await selected(page, A, 'issue:Acme/Widgets#1');
  await row(page, B).click();
  await expect(page.locator('[data-live-error]')).toContainText('Unable to select run');
  await selected(page, A, 'issue:Acme/Widgets#1');
  await page.getByRole('button', { name: /Apply to sandbox/ }).click();
  await expect.poll(() => api.state.applyRequests.length).toBe(1);
  expect(api.state.applyRequests[0].run).toBe(A);
});

for (const failure of ['run', 'issue']) {
  test(`a failed ${failure} read settles a pending claim on the preserved view`, async ({
    page,
  }) => {
    add(A, 'issue:Acme/Widgets#1', 1);
    add(B, 'issue:Acme/Widgets#2', 2);
    await page.goto(`/t/acme?run=${A}`);
    await selected(page, A, 'issue:Acme/Widgets#1');
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route('**/tenants/acme/path?*', async (route) => {
      await gate;
      try {
        await route.continue();
      } catch {
        /* An aborted path read is the bug under test. */
      }
    });
    const failed = (url: URL) =>
      failure === 'run'
        ? url.pathname === `/tenants/acme/runs/${B}`
        : url.pathname === '/tenants/acme/subgraph' &&
          (url.searchParams.get('keys') ?? '').includes('#9');
    await page.route(failed, (route) => route.fulfill({ status: 503, body: '{}' }));
    const pathRequest = page.waitForRequest(
      (request) => new URL(request.url()).pathname === '/tenants/acme/path',
    );
    await page.getByRole('button', { name: evidenceName('label:Acme/Widgets/graph') }).click();
    await pathRequest;
    await expect(page.locator('[data-evidence-loading]')).toBeVisible();
    const readFailed = page.waitForResponse((response) => failed(new URL(response.url())));
    if (failure === 'run') await row(page, B).click();
    else {
      await page.locator('#issue-key').fill('issue:Acme/Widgets#9');
      await page.getByRole('button', { name: 'Load issue graph' }).click();
    }
    await readFailed;
    await expect(page.locator('[data-live-error]')).toContainText(
      failure === 'run' ? 'Unable to select run' : 'Live data unavailable',
    );
    await expect(page.locator('[data-run-key]')).toHaveText(`run:${A}`);
    await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#1');
    release();
    await expect(page.locator('[data-evidence-loading]')).toHaveCount(0);
    await expect(page.locator('[data-evidence-error], [data-path-failed]')).toHaveCount(0);
    await expect(page.locator('.canvas-selection .evidence-text')).toHaveText([
      'label:Acme/Widgets/graph',
      'doc:Acme/Widgets/README.md',
    ]);
    await expect(page.locator('[data-run-key]')).toHaveText(`run:${A}`);
  });
}

test('a pending claim path cannot apply to a newly committed run', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  add(B, 'issue:Acme/Widgets#2', 2);
  await page.goto(`/t/acme?run=${A}`);
  await selected(page, A, 'issue:Acme/Widgets#1');
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let settled = 0;
  await page.route('**/tenants/acme/path?*', async (route) => {
    await gate;
    try {
      await route.continue();
    } catch {
      /* The committed run aborted this read. */
    }
    settled += 1;
  });
  const pathRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/tenants/acme/path',
  );
  await page.getByRole('button', { name: evidenceName('label:Acme/Widgets/graph') }).click();
  await pathRequest;
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  release();
  // Paths resolve one citation at a time; the commit aborted the first.
  await expect.poll(() => settled).toBe(1);
  await expect(
    page.locator('[data-evidence-loading], .canvas-selection .evidence-text'),
  ).toHaveCount(0);
  await expect(page.locator('.citation[aria-pressed="true"]')).toHaveCount(0);
  await selected(page, B, 'issue:Acme/Widgets#2');
});

test('tenant navigation cannot leak a delayed prior-tenant recall', async ({ page }) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(row(page, A)).toBeVisible();
  api.state.delays[`/tenants/acme/runs/${A}`] = 1200;
  const delayedRead = readSettled(page, runRead(A));
  await row(page, A).click();
  await page.getByRole('combobox', { name: 'Tenant' }).click();
  await page.getByRole('option', { name: 'Pallets', exact: true }).click();
  await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  await delayedRead();
  await expect(row(page, A)).toHaveCount(0);
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
  expect(api.state.requests).toContain('GET /tenants/pallets/runs');
});

test('a prior-tenant list held across navigation cannot populate or auto-select in the destination', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  api.state.delays['/tenants/acme/runs'] = 1200;
  let release!: () => void;
  let started = 0;
  let handled = 0;
  let captured = 0;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/tenants/acme/runs?*', async (route) => {
    started += 1;
    const response = await route.fetch();
    expect((await response.json()).runs[0].run_key).toBe(`run:${A}`);
    captured += 1;
    await gate;
    // Tenant cleanup may already have aborted the browser's old list read.
    await route.fulfill({ response }).catch(() => {});
    handled += 1;
  });
  await page.goto('/t/acme');
  await expect.poll(() => captured).toBeGreaterThan(0);
  expect(api.state.requests.filter((request) => request.endsWith(`/runs/${A}`))).toEqual([]);
  await page.clock.install({ time: new Date('2026-01-01T00:00:00Z') });
  await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
  await page.getByRole('combobox', { name: 'Tenant' }).click();
  await page.getByRole('option', { name: 'Pallets', exact: true }).click();
  await expect(page).toHaveURL(/\/t\/pallets$/);
  await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  expect(api.state.requests.filter((request) => request.endsWith(`/runs/${A}`))).toEqual([]);
  release();
  await expect.poll(() => handled === started && captured === started).toBe(true);
  const destinationReads = () =>
    api.state.requests.filter((request) => request === 'GET /tenants/pallets/runs').length;
  const before = destinationReads();
  await page.clock.runFor(5000);
  await expect.poll(destinationReads).toBeGreaterThan(before);
  await expect(page.getByText('No runs yet', { exact: true })).toBeVisible();
  await expect(page.locator('[data-live-run]')).toHaveCount(0);
  await expect(page.locator('[data-run-key]')).toHaveCount(0);
  await expect(page.locator('[data-issue-key]')).toHaveCount(0);
  await expect(page).toHaveURL(/\/t\/pallets$/);
  expect(api.state.requests.filter((request) => request.endsWith(`/runs/${A}`))).toEqual([]);
  // The destination's own arrival still gets its normal automatic selection.
  add(B, 'issue:pallets/flask#500', 2, 'pallets');
  await page.clock.runFor(5000);
  await selected(page, B, 'issue:pallets/flask#500');
  await expect(page).toHaveURL(new RegExp(`/t/pallets\\?run=${B}$`));
  await expect(row(page, A)).toHaveCount(0);
  expect(api.state.requests.filter((request) => request.endsWith(`/runs/${A}`))).toEqual([]);
  expect(api.state.applyRequests).toHaveLength(0);
});

test('transient list outage preserves list and selection and recovers on subsequent tick', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#1', 1);
  await page.goto(`/t/acme?run=${A}`);
  await selected(page, A, 'issue:Acme/Widgets#1');
  api.state.listOutage = true;
  await expect(page.getByText('Run list stale; retrying automatically.')).toBeVisible({
    timeout: 10_000,
  });
  await selected(page, A, 'issue:Acme/Widgets#1');
  api.state.listOutage = false;
  add(B, 'issue:Acme/Widgets#2', 2);
  await expect(row(page, B)).toBeVisible({ timeout: 10_000 });
  await selected(page, A, 'issue:Acme/Widgets#1');
  await expect(page.getByText('Run list stale; retrying automatically.')).toHaveCount(0);
});

test('slow list reads are single-flight and polling remains GET-only', async ({ page }) => {
  api.state.delays['/tenants/acme/runs'] = 6500;
  const isList = (request: Request) => new URL(request.url()).pathname === '/tenants/acme/runs';
  const listReads: Request[] = [];
  const finishedBeforeStart = new Map<Request, number>();
  let finished = 0;
  page.on('request', (request) => {
    if (isList(request)) {
      listReads.push(request);
      finishedBeforeStart.set(request, finished);
    }
  });
  page.on('requestfinished', (request) => {
    if (isList(request)) finished += 1;
  });
  // React's development effect replay aborts its first read. Count live reads,
  // not that cancelled lifecycle, when testing overlap at the interval tick.
  const liveReads = () => listReads.filter((request) => request.failure() === null);
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect.poll(() => liveReads().length).toBe(1);
  // The 6.5 s read spans the 5 s tick. Wait for the next poll, not the clock:
  // single-flight means it starts only after the first read has finished.
  await expect.poll(() => liveReads().length, { timeout: 15_000 }).toBe(2);
  expect(finishedBeforeStart.get(liveReads()[1])).toBe(1);
  expect(api.state.requests.some((r) => r.startsWith('POST'))).toBe(false);
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

test('late graph reads and evidence paths cannot replace a newer selected run', async ({
  page,
}) => {
  add(A, 'issue:Acme/Widgets#slow', 1);
  add(B, 'issue:Acme/Widgets#2', 2);
  await page.goto(`/t/acme?run=${KNOWN_RUN}`);
  await expect(row(page, A)).toBeVisible();
  const slowRead = readSettled(page, slowGraph);
  await row(page, A).click();
  await expect.poll(() => api.state.lastKeys).toEqual(['issue:Acme/Widgets#slow']);
  await row(page, B).click();
  await selected(page, B, 'issue:Acme/Widgets#2');
  await slowRead();
  await selected(page, B, 'issue:Acme/Widgets#2');
  let pathStarted = false;
  await page.route('**/tenants/acme/path?*', async (route) => {
    pathStarted = true;
    await new Promise((resolve) => setTimeout(resolve, 1200));
    try {
      await route.continue();
    } catch {
      /* Superseded request was aborted. */
    }
  });
  const pathRead = readSettled(page, (url) => url.pathname === '/tenants/acme/path');
  await page.getByRole('button', { name: evidenceName('label:Acme/Widgets/graph') }).click();
  await expect.poll(() => pathStarted).toBe(true);
  await page.locator('#issue-key').fill('issue:Acme/Widgets#manual');
  await page.getByRole('button', { name: 'Load issue graph' }).click();
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
  await pathRead();
  await expect(page.locator('[data-issue-key]')).toHaveText('issue:Acme/Widgets#manual');
  await expect(page.locator('.citation[aria-pressed="true"]')).toHaveCount(0);
});
