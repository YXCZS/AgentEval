import { expect, test } from "./test-fixture";

const navigation = [
  "总览",
  "Trace",
  "数据集",
  "实验",
  "评估器",
  "回归分析",
  "发布门禁",
  "Release 与接入",
] as const;

test("fresh project shows truthful SDK onboarding without creating data", async ({ page }) => {
  const requests: Array<{ method: string; path: string }> = [];
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    requests.push({ method: request.method(), path: new URL(request.url()).pathname });
    if (request.method() === "GET") {
      const pathname = new URL(request.url()).pathname;
      if (pathname.endsWith("/traces") || pathname.endsWith("/runs")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, offset: 0, limit: 25, next_offset: null }) });
      }
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "从真实 SDK Experiment 开始" })).toBeVisible();
  await expect(page.getByText(/模型 Key 始终留在 Agent 进程中/)).toBeVisible();
  await expect(page.getByRole("button", { name: "配置 Release 与 Key" })).toBeVisible();
  await expect(page.getByRole("button", { name: "创建数据集" })).toBeVisible();
  await expect(page.locator(".metric-tile strong")).toHaveText(["0", "0", "0", "0"]);
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

test("all workbench navigation and shell commands are interactive", async ({ page }, testInfo) => {
  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      const pathname = new URL(request.url()).pathname;
      if (pathname.endsWith("/traces") || pathname.endsWith("/runs") || pathname.endsWith("/experiments")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], total: 0, offset: 0, limit: 25, next_offset: null }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    return route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ detail: "This navigation test does not submit data." }),
    });
  });

  await page.goto("/");
  await expect(page.locator(".page-content")).toBeVisible();

  if (testInfo.project.name === "desktop-chromium") {
    await page.getByRole("button", { name: "切换工作区" }).click();
    await expect(page.locator(".workspace-menu")).toContainText("当前连接 default-project");
    await page.getByRole("button", { name: "打开个人菜单" }).click();
    await expect(page.locator(".profile-menu")).toContainText("本地单人工作区");
  } else {
    await expect(page.getByRole("button", { name: "切换工作区" })).toBeHidden();
    await expect(page.getByRole("button", { name: "打开个人菜单" })).toBeHidden();
  }

  for (const label of navigation) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(page.locator(".breadcrumb strong")).toHaveText(label);
    await expect(page.locator(".page-content")).toBeVisible();
  }

  await page.getByRole("button", { name: "搜索导航" }).click();
  await page.getByRole("textbox", { name: "搜索导航" }).fill("回归分析");
  await page.getByRole("textbox", { name: "搜索导航" }).press("Enter");
  await expect(page.locator(".breadcrumb strong")).toHaveText("回归分析");

  await page.getByRole("button", { name: "新建评测", exact: true }).click();
  await expect(page.locator(".breadcrumb strong")).toHaveText("实验");

  if (testInfo.project.name === "mobile-chromium") {
    const dimensions = await page.evaluate(() => ({
      bodyWidth: document.body.scrollWidth,
      viewportWidth: window.innerWidth,
    }));
    expect(dimensions.bodyWidth).toBeLessThanOrEqual(dimensions.viewportWidth + 1);
    await expect(page.locator(".sidebar nav")).toHaveCSS("overflow-x", "auto");
  }
});
