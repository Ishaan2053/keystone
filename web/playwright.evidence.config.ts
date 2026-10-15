import { defineConfig } from '@playwright/test';

/** Production evidence orchestration tests need neither Next nor a browser. */
export default defineConfig({
  testDir: './tests',
  testMatch: 'evidence-context.spec.ts',
  workers: 1,
  reporter: 'list',
});
