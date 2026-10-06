import { defineConfig, devices } from "@playwright/test";

// Browser tests of the built dashboard (`npm run build` first: they open what
// `book-agent ui` serves, not the dev server). e2e/serve.py builds the jobs
// with the test suite's stand-in models and serves them; no Ollama is needed.
const PORT = 8798;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  // One server and one set of jobs: a test that changes a job has its own job,
  // and the tests run one at a time.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `python e2e/serve.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: { PYTHONIOENCODING: "utf-8" },
  },
});
