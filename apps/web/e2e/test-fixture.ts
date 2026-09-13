import { chromium, expect, test as base } from "@playwright/test";

const cdpEndpoint = process.env.PLAYWRIGHT_CDP_ENDPOINT;

const cdpTest = base.extend({
  page: async ({}, use, testInfo) => {
    if (!cdpEndpoint) throw new Error("PLAYWRIGHT_CDP_ENDPOINT is required");
    const browser = await chromium.connectOverCDP(cdpEndpoint);
    const context = browser.contexts()[0];
    if (!context) throw new Error("CDP browser has no default context");
    const page = await context.newPage();
    const viewport = testInfo.project.use.viewport;
    if (viewport) await page.setViewportSize(viewport);
    const baseURL = testInfo.project.use.baseURL;
    if (baseURL) {
      const goto = page.goto.bind(page);
      page.goto = ((url, options) =>
        goto(new URL(url, baseURL).toString(), options)) as typeof page.goto;
    }
    try {
      await use(page);
    } finally {
      await page.close();
    }
  },
});

const test = cdpEndpoint ? cdpTest : base;

export { expect, test };
