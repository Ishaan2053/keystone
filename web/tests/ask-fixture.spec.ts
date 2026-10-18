import { test, expect } from '@playwright/test';
import { evidenceName } from './evidence-name';
import ask from '../../api/fixtures/ask.json';

test('fixture Ask answers with the ask fixture and its citation chips', async ({ page }) => {
  await page.goto('/t/falkordb');
  // Ask is the third tab of the issue panel.
  await page.getByRole('tab', { name: 'Ask' }).click();
  await page.locator('#ask-question').fill('Why does GraphRAG-SDK do X?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  const answer = page.locator('[data-ask-status]');
  await expect(answer).toContainText(ask.answer[0].text);
  await expect(page.locator('[data-ask]')).toContainText('Fixture answer');
  const chip = answer.getByRole('button', { name: evidenceName(ask.answer[0].citations[0]) });
  await chip.click();
  await expect(chip).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.trace-inline [data-ask-trace] [data-trace-row]')).toHaveAttribute('data-supporting', 'true');
  await expect(page.locator('.triage-panel .button-primary')).toHaveCount(1);
});
