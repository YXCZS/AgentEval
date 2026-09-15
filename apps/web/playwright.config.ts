import { defineConfig, devices } from "@playwright/test";

const realApi = process.env.PLAYWRIGHT_REAL_API === "1";
const port = process.env.PLAYWRIGHT_PORT ?? (realApi ? "3002" : "3001");
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  webServer: {
    command: `${realApi ? "npm run dev" : "npm run start"} -- --port ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    env: {
      ...process.env,
      NEXT_PUBLIC_API_URL: realApi
        ? (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:18080")
        : "http://api.test",
      NEXT_PUBLIC_PROJECT_ID: process.env.NEXT_PUBLIC_PROJECT_ID ?? "default-project",
      NEXT_PUBLIC_WORKSPACE_SESSION: process.env.NEXT_PUBLIC_WORKSPACE_SESSION
        ?? "dev:default-project:e2e-session-secret",
    },
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
});
