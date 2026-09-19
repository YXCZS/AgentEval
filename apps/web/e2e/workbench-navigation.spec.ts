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
  await page.route("**/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", environment: "test" }),
  }));
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
  await expect(page.locator(".system-status")).toContainText("API 已连接");
  await expect(page.getByRole("heading", { name: "从真实 SDK Experiment 开始" })).toBeVisible();
  await expect(page.getByText(/模型 Key 始终留在 Agent 进程中/)).toBeVisible();
  await expect(page.getByRole("button", { name: "配置 Release 与 Key" })).toBeVisible();
  await expect(page.getByRole("button", { name: "创建数据集" })).toBeVisible();
  await expect(page.locator(".metric-tile strong")).toHaveText(["0", "0", "0", "0"]);
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

test("all workbench navigation and shell commands are interactive", async ({ page }) => {
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

  await expect(page.getByLabel("当前项目")).toContainText("default-project");
  await expect(page.getByLabel("当前运行模式")).toContainText("单项目模式");

  for (const label of navigation) {
    await page.locator(".sidebar nav").getByRole("button", { name: label, exact: true }).click();
    await expect(page.locator(".breadcrumb strong")).toHaveText(label);
    await expect(page.locator(".page-content")).toBeVisible();
  }

  await page.locator(".sidebar nav").getByRole("button", { name: "发布门禁", exact: true }).click();
  await expect(page.getByRole("tab", { name: "发布门禁" })).toHaveAttribute("aria-selected", "true");

  await page.getByRole("button", { name: "搜索导航" }).click();
  await page.getByRole("textbox", { name: "搜索导航" }).fill("回归分析");
  await page.getByRole("textbox", { name: "搜索导航" }).press("Enter");
  await expect(page.locator(".breadcrumb strong")).toHaveText("回归分析");

  await page.getByRole("button", { name: "新建评测", exact: true }).click();
  await expect(page.locator(".breadcrumb strong")).toHaveText("实验");
});
