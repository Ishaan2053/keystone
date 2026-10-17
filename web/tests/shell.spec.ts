import { test, expect } from '@playwright/test';
import tokens from '../../design/tokens.json';
import triage from '../../api/fixtures/triage_result.json';

test('trace expands by keyboard and copies exact query', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (text: string) => { (window as unknown as { copied: string }).copied = text; } } }));
  await page.goto('/t/falkordb');
  for (const tool of triage.trace) {
    const row = page.locator('[data-trace-row]').filter({ hasText: tool.tool });
    await row.getByRole('button', { name: `Expand ${tool.tool}` }).focus();
    await page.keyboard.press('Enter');
    await expect(row).toContainText(`${tool.ms} ms`);
    await expect(row.locator('.trace-params')).toHaveText(JSON.stringify(tool.params, null, 2));
    for (const [i, query] of tool.cypher.entries()) {
      await expect(row.locator('.query-code').nth(i)).toHaveText(query);
      await row.getByRole('button', { name: `Copy Cypher ${i + 1}` }).click();
      await expect(row.getByRole('status')).toHaveText('Copied');
      expect(await page.evaluate(() => (window as unknown as { copied: string }).copied)).toBe(query);
    }
  }
});
test('clipboard denial reports failure', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { throw new Error('denied'); } } }));
  await page.goto('/t/falkordb');
  await page.getByRole('button', { name: 'Expand affected_modules' }).click();
  await page.getByRole('button', { name: 'Copy Cypher 1' }).click();
  await expect(page.locator('.copy-status')).toHaveText('Could not copy Cypher');
});
test('selected citation reveals supporting trace', async ({ page }) => {
  await page.goto('/t/falkordb');
  const key = triage.labels[0].citations[0];
  await page.getByRole('button', { name: `Show evidence: ${key}`, exact: true }).first().click();
  await expect(page.locator('[data-trace-row]')).toHaveAttribute('data-supporting', 'true');
  await expect(page.locator('.query-code')).toBeVisible();
  await expect(page.locator('.canvas-selection')).toContainText(key);
});

test('theme dark default and light toggle persists', async ({ page }) => {
  await page.goto('/t/falkordb');
  await expect(page.locator('html')).not.toHaveClass(/light/);
  await page.getByRole('button', { name: 'Toggle color theme' }).click();
  await expect(page.locator('html')).toHaveClass(/light/);
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/light/);
});
test('theme storage failure keeps control usable', async ({ page }) => {
  await page.addInitScript(() => { Storage.prototype.getItem = () => { throw new Error('denied'); }; Storage.prototype.setItem = () => { throw new Error('denied'); }; });
  await page.goto('/t/falkordb');
  await page.getByRole('button', { name: 'Toggle color theme' }).click();
  await expect(page.locator('html')).toHaveClass(/light/);
});
test('dry run preview is safe in both switch states', async ({ page }) => {
  await page.goto('/t/falkordb');
  const writes: string[] = [];
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()); });
  const toggle = page.getByRole('switch', { name: 'Dry run' });
  await expect(toggle).toBeChecked();
  await page.getByRole('button', { name: 'Apply to sandbox (dry run)', exact: true }).click();
  // Scoped to the card's preview status: the canvas owns its own live-region status.
  await expect(page.locator('.preview-status')).toContainText('Fixture preview: no GitHub changes');
  await expect(page.locator('.preview-status')).toContainText('Skipped (dry run)');
  await toggle.click();
  await page.getByRole('button', { name: 'Apply to sandbox', exact: true }).click();
  await expect(page.locator('.preview-status')).toContainText('Refused');
  expect(writes).toEqual([]);
});
test('fixture claims all carry stable citations', async ({ page }) => {
  await page.goto('/t/falkordb');
  for (const section of ['labels', 'reviewers', 'risks', 'decisions'] as const) for (const claim of triage[section]) {
    const row = page.locator(`[data-claim-section="${section}"] li`).filter({ hasText: claim.text });
    await expect(row).toBeVisible();
    for (const key of claim.citations) await expect(row.getByRole('button', { name: `Show evidence: ${key}`, exact: true })).toBeVisible();
  }
});

test('fixture route renders', async ({ page }) => {
  await page.goto('/t/falkordb');
  await expect(page.getByText('Fixture preview', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Suggested reviewers' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tool trace' })).toBeVisible();
});
test('fixture Proof stays explicitly illustrative and never polls live stats', async ({ page }) => {
  const statsReads: string[] = [];
  page.on('request', request => {
    if (request.method() === 'GET' && /\/tenants\/[^/]+\/stats$/.test(new URL(request.url()).pathname)) statsReads.push(request.url());
  });
  await page.goto('/t/falkordb');
  await expect(page.getByText('Fixture preview', { exact: true })).toBeVisible();
  await page.clock.install({ time: new Date('2026-01-01T00:00:00Z') });
  await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
  const trigger = page.getByRole('button', { name: /Proof/ });
  const panel = page.getByRole('dialog', { name: 'Proof' });
  await trigger.click();
  await expect(panel.getByText('Fixture Proof · illustrative data, not live measurements', { exact: true })).toBeVisible();
  await expect(panel.getByText('Proof not measured', { exact: true })).toBeVisible();
  await expect(panel.getByRole('row', { name: 'affected_modules 18 ms 32 ms', exact: true })).toBeVisible();
  expect(statsReads).toEqual([]);
  // Exercise three live polling intervals without a wall-clock sleep.
  await page.clock.runFor(15000);
  await expect(panel.getByText('Fixture Proof · illustrative data, not live measurements', { exact: true })).toBeVisible();
  expect(statsReads).toEqual([]);
  await panel.getByRole('button', { name: 'Close Proof' }).click();
  await expect(panel).toBeHidden();
  await trigger.click();
  await page.clock.runFor(5000);
  await expect(panel.getByText('Fixture Proof · illustrative data, not live measurements', { exact: true })).toBeVisible();
  expect(statsReads).toEqual([]);
});
test('unknown tenant is not found', async ({ page }) => {
  expect((await page.goto('/t/unknown'))?.status()).toBe(404);
});
test('token mapping preserves every source token', async ({ page }) => {
  await page.goto('/t/falkordb');
  for (const theme of ['dark', 'light'] as const) {
    await page.evaluate(t => document.documentElement.classList.toggle('light', t === 'light'), theme);
    const actual = await page.evaluate(() => {
      const styles = getComputedStyle(document.documentElement);
      return Object.fromEntries(Array.from(styles).filter(k => k.startsWith('--')).map(k => [k.slice(2), styles.getPropertyValue(k).trim()]));
    });
    const colors = Object.fromEntries(tokens.color.tokens.map(t => [t.name, t.value]));
    const resolve = (v: string | { dark: string; light: string }): string => typeof v === 'object' ? v[theme] : v.startsWith('{') ? resolve(colors[v.slice(1, -1)]) : v;
    const normalize = (s: string) => s.replace(/#([a-f0-9]{3})(?![a-f0-9])/gi, (_, h: string) => '#' + [...h].map(c => c + c).join('')).replace(/\s+/g, ' ');
    for (const token of [...tokens.color.tokens, ...tokens.spacing.tokens, ...tokens.radius.tokens, ...tokens.shadow.tokens, ...tokens.graph.tokens]) expect(normalize(actual[token.name])).toBe(normalize(resolve(token.value)));
    for (const token of tokens.opacity.tokens) expect(Number(actual[token.name])).toBe(Number(token.value));
    for (const group of tokens.type.groups) for (const style of group.styles) {
      expect(actual[`${style.name}-font-size`]).toBe(style.fontSize);
      expect(actual[`${style.name}-line-height`]).toBe(style.lineHeight);
      expect(actual[`${style.name}-font-weight`]).toBe(String(style.fontWeight));
      if ('letterSpacing' in style) expect(parseFloat(actual[`${style.name}-letter-spacing`])).toBe(parseFloat(style.letterSpacing!));
      else expect(actual[`${style.name}-letter-spacing`]).toBe('normal');
    }
    await expect(page.locator('body')).toHaveCSS('font-size', '14px');
    await expect(page.locator('body')).toHaveCSS('background-color', theme === 'dark' ? 'rgb(15, 17, 19)' : 'rgb(246, 247, 247)');
  }
});

test('desktop panes are exactly 360 and 400px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/t/falkordb');
  const boxes = await page.locator('.workspace > *').evaluateAll(els => els.map(e => e.getBoundingClientRect()));
  expect(Math.round(boxes[0].width)).toBe(360);
  expect(Math.round(boxes[1].width)).toBeGreaterThanOrEqual(360);
  expect(Math.round(boxes[2].width)).toBe(400);
});

test('tablet shows drawer and returns focus', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await page.goto('/t/falkordb');
  const card = await page.locator('.triage-panel').boundingBox();
  const canvas = await page.locator('.canvas-panel').boundingBox();
  expect(canvas!.y).toBeGreaterThan(card!.y);
  const trigger = page.getByRole('button', { name: 'Open tool trace' });
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(trigger).toBeFocused();
});

test('phone stacks in reading order without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/t/falkordb');
  const card = await page.locator('.triage-panel').boundingBox();
  const canvas = await page.locator('.canvas-panel').boundingBox();
  const trace = await page.locator('.trace-inline').boundingBox();
  expect(card!.y).toBeLessThan(canvas!.y);
  expect(canvas!.y).toBeLessThan(trace!.y);
  expect(Math.round(canvas!.height)).toBe(320);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(overflow).toBe(true);
});

test('long content does not overflow at phone width', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/t/falkordb');
  await page.evaluate(() => {
    const pre = document.querySelector('.query-code');
    if (pre) pre.textContent = 'A'.repeat(2000);
  });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(overflow).toBe(true);
});

test('phone canvas keeps selected evidence within fixed 320px panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/t/falkordb');
  const key = triage.labels[0].citations[0];
  await page.getByRole('button', { name: `Show evidence: ${key}`, exact: true }).first().click();
  const panelBox = await page.locator('.canvas-panel').evaluate(el => el.getBoundingClientRect().toJSON());
  const selectionBox = await page.locator('.canvas-selection').evaluate(el => el.getBoundingClientRect().toJSON());
  expect(selectionBox.bottom).toBeLessThanOrEqual(panelBox.bottom + 1);
  const pageOverflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(pageOverflow).toBe(true);
});

test('phone canvas legend stays inside the fixed 320px panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/t/falkordb');

  async function checkLegendContainment() {
    const panel = page.locator('.canvas-panel');
    const legend = page.locator('[data-graph-legend]');
    await expect(legend).toBeVisible();
    const panelBox = (await panel.boundingBox())!;
    const legendBox = (await legend.boundingBox())!;
    expect(Math.round(panelBox.height)).toBe(320);
    // The real canvas legend is docked inside the canvas, not a scrolled placeholder.
    expect(legendBox.y).toBeGreaterThanOrEqual(panelBox.y - 1);
    expect(legendBox.y + legendBox.height).toBeLessThanOrEqual(panelBox.y + panelBox.height + 1);
    const viewportOverflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(viewportOverflow).toBe(true);
  }

  await checkLegendContainment();
  const key = triage.labels[0].citations[0];
  await page.getByRole('button', { name: `Show evidence: ${key}`, exact: true }).first().click();
  await checkLegendContainment();
});
