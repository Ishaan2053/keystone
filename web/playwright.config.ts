import { defineConfig, devices } from "@playwright/test";

/**
 * B3 shell e2e config: Chromium only, a single worker, dev server on :3001.
 * Screenshot and report artifacts land in test-results/ and playwright-report/,
 * both ignored by the repository root .gitignore.
 */
export default defineConfig({
  testDir: "./tests",
  // The API-mode suite has its own config and server (playwright.api.config.ts);
  // keep it out of the default fixture run so the modes never leak.
  testIgnore: ["api-data.spec.ts", "apply.spec.ts", "run-link.spec.ts", "live-runs.spec.ts", "proof.spec.ts", "triage-ask-feedback.spec.ts"],
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
    command: "npm run dev -- --webpack",
    url: "http://localhost:3001",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
