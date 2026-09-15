import { expect, test } from "./test-fixture";

test.describe("real API browser smoke test", () => {
  test.skip(() => process.env.PLAYWRIGHT_REAL_API !== "1", "set PLAYWRIGHT_REAL_API=1 to run");

  test("opens every primary workbench view through the live API", async ({ page }) => {
    const serverErrors: string[] = [];
    const preflightErrors: string[] = [];
    page.on("response", (response) => {
      if (response.url().includes("/projects/") && response.status() >= 500) {
        serverErrors.push(`${response.status()} ${response.request().method()} ${response.url()}`);
      }
      if (response.request().method() === "OPTIONS" && response.status() >= 400) {
        preflightErrors.push(`${response.status()} ${response.url()}`);
      }
    });

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "评测总览" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Trace", exact: true })).toBeEnabled();

    const navigation = [
      ["Trace", "Trace 追踪"],
      ["数据集", "数据集"],
      ["实验", "实验"],
      ["评估器", "评估器"],
      ["人工评审", "人工评审"],
      ["回归分析", "评测报告"],
      ["发布门禁", "评测报告"],
      ["Release 与接入", "Release 与接入"],
    ] as const;

    for (const [buttonName, headingName] of navigation) {
      const button = page.getByRole("button", { name: buttonName, exact: true });
      await expect(button).toBeVisible();
      await expect(button).toBeEnabled();
      await button.click();
      await expect(page.locator(".breadcrumb strong")).toHaveText(buttonName);
      await expect(page.getByRole("heading", { name: headingName }).first()).toBeVisible();
    }

    expect(serverErrors).toEqual([]);
    expect(preflightErrors).toEqual([]);
  });

  test("creates a Dataset and reads its persisted Version and Case", async ({ page }) => {
    const name = `browser-live-${Date.now()}`;
    const serverErrors: string[] = [];
    page.on("response", (response) => {
      if (response.url().includes("/projects/") && response.status() >= 500) {
        serverErrors.push(`${response.status()} ${response.request().method()} ${response.url()}`);
      }
    });

    await page.goto("/");
    await page.getByRole("button", { name: "数据集", exact: true }).click();
    await page.getByRole("button", { name: "新建数据集", exact: true }).click();
    await page.getByLabel("Dataset 名称").fill(name);
    await page.getByPlaceholder("问题、消息或 JSON 输入").fill("真实浏览器工作流校验");
    await page.getByRole("button", { name: "创建 Dataset", exact: true }).click();

    await expect(page.locator(".dataset-notice")).toContainText("Dataset 已创建，包含 1 条用例");
    await expect(page.getByRole("button", { name: `${name}无描述`, exact: true })).toBeVisible();
    await expect(page.locator(".dataset-detail-heading").getByText(name, { exact: true })).toBeVisible();
    expect(serverErrors).toEqual([]);
  });
});
