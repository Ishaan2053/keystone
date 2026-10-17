import { defineConfig, devices } from '@playwright/test';
import { FAKE_API_PORT } from './tests/fake-api';

/**
 * API-mode e2e config (B4 Task 2). A second, separate configuration so the
 * default fixture suite cannot leak fixture data into an API run.
 *
 * The Next dev server runs on :3002 (the fixture suite owns :3001) and reads
 * from the deterministic fake graph API started inside `api-data.spec.ts` on
 * `FAKE_API_PORT`. `NEXT_PUBLIC_API_BASE_URL` is a location, not a mode flag.
 */
export default defineConfig({
  testDir: './tests',
  testMatch: ['api-data.spec.ts', 'apply.spec.ts', 'run-link.spec.ts', 'live-runs.spec.ts'],
  workers: 1,
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:3002',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    // Webpack supports the linked dependency directory used by isolated worktrees.
    command: `npx next dev --webpack -p 3002`,
    url: 'http://localhost:3002',
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_DATA_SOURCE: 'api',
      NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${FAKE_API_PORT}`,
    },
  },
});
