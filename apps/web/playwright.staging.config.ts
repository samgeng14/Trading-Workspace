import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

// The dashboard in paper mode (no MSW), on the staging API: the real app over a made-up copy of
// the paper account (services/api/dv02_api/staging.py), so every page meets real labels, 37
// holdings and a one-day NAV history. Its own ports, so it runs beside `npm run dev`, `bin/tradeit
// staging` and the default e2e suite (5199). `npm run e2e:staging`.
const API_PORT = 8197;
const WEB_PORT = 5297;
// Monday Oct 12 2026, 15:50 ET: the market is open, and it is past 15:35 ET, when the daily run
// should have happened: the fresh scenario's schedule shows it missed.
const STAGING_NOW = "2026-10-12T19:50:00Z";
const VENV_PYTHON = fileURLToPath(new URL("../../.venv/bin/python", import.meta.url));
const PYTHON = process.env.DV02_PYTHON ?? (existsSync(VENV_PYTHON) ? VENV_PYTHON : "python3");

// e2e/staging/*.spec.ts define their tests only under this config; the default config finds the files too.
process.env.DV02_STAGING_E2E = "1";

export default defineConfig({
  testDir: "./e2e/staging",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `"${PYTHON}" -m dv02_api.staging --scenario fresh --port ${API_PORT} --now ${STAGING_NOW}`,
      cwd: "../../services/api",
      url: `http://127.0.0.1:${API_PORT}/api/v1/status`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      env: { API_URL: `http://127.0.0.1:${API_PORT}` },
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      // Its proxy logs an EPIPE stack trace each time a test's page closes with the stream open.
      // A dev server that can't start still fails the run: its url never answers.
      stderr: "ignore",
    },
  ],
});
