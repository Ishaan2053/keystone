import { defineConfig, devices } from "@playwright/test";

/**
 * B3 shell e2e config: Chromium only, a single worker, dev server on :3001.
 * Screenshot and report artifacts land in test-results/ and playwright-report/,
 * both ignored by the repository root .gitignore.
 */
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3001",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3001",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
