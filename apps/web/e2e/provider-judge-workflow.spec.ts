import { expect, test } from "./test-fixture";

const secret = "browser-provider-key-must-not-persist";

type Provider = {
  id: string;
  project_id: string;
  name: string;
  provider: "openai_compatible";
  base_url: string;
  model: string;
  default_parameters: Record<string, unknown>;
  credential_mask: string;
  credential_key_id: string;
  status: "active" | "disabled";
  enabled: boolean;
  created_at: string;
  updated_at: string;
  tested_at: string;
};

const emptyList: never[] = [];
const timestamp = "2026-09-12T00:00:00Z";

function provider(id: string, name: string): Provider {
  return {
    id,
    project_id: "default-project",
    name,
    provider: "openai_compatible",
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
    default_parameters: { temperature: 0 },
    credential_mask: "****last4",
    credential_key_id: "test-key",
    status: "active",
    enabled: true,
    created_at: timestamp,
    updated_at: timestamp,
    tested_at: timestamp,
  };
}

test("Provider lifecycle and managed Judge binding keep the browser free of Provider keys", async ({ page }) => {
  const providers: Provider[] = [];
  let providerSequence = 0;
  let testPayload: Record<string, unknown> | null = null;
  let createPayload: Record<string, unknown> | null = null;
  let rotatePayload: Record<string, unknown> | null = null;
  let judgePayload: Record<string, unknown> | null = null;

  await page.route("**/projects/default-project/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

    if (method === "GET" && path.endsWith("/agent-releases")) return json(emptyList);
    if (method === "GET" && path.endsWith("/api-keys")) return json(emptyList);
    if (method === "GET" && path.endsWith("/provider-connections")) return json(providers);
    if (method === "POST" && path.endsWith("/provider-connections/test")) {
      testPayload = request.postDataJSON() as Record<string, unknown>;
      return json({
        provider: "openai_compatible",
        configured_model: "deepseek-chat",
        response_model: "deepseek-chat",
        upstream_request_id: "chatcmpl-browser-test",
        input_tokens: 4,
        output_tokens: 1,
        total_tokens: 5,
        challenge_verified: true,
      });
    }
    if (method === "POST" && path.endsWith("/provider-connections")) {
      createPayload = request.postDataJSON() as Record<string, unknown>;
      const created = provider(`provider-${++providerSequence}`, String(createPayload.name));
      providers.unshift(created);
      return json(created, 201);
    }
    const providerMatch = path.match(/\/provider-connections\/(provider-\d+)(?:\/(enabled|rotate))?$/);
    if (providerMatch) {
      const [, providerId, action] = providerMatch;
      const current = providers.find((item) => item.id === providerId);
      if (!current) return json({ detail: "not found" }, 404);
      if (method === "PATCH" && action === "enabled") {
        current.enabled = url.searchParams.get("enabled") === "true";
        current.status = current.enabled ? "active" : "disabled";
        return json(current);
      }
      if (method === "POST" && action === "rotate") {
        rotatePayload = request.postDataJSON() as Record<string, unknown>;
        current.enabled = true;
        current.status = "active";
        current.updated_at = "2026-09-12T00:01:00Z";
        return json(current);
      }
      if (method === "DELETE") {
        providers.splice(providers.indexOf(current), 1);
        return route.fulfill({ status: 204 });
      }
    }
    if (method === "GET" && path.endsWith("/evaluators")) return json(emptyList);
    if (method === "POST" && path.endsWith("/evaluators")) {
      judgePayload = request.postDataJSON() as Record<string, unknown>;
      return json({
        id: "judge-1",
        ...judgePayload,
        evaluator_connection_id: null,
        enabled: true,
      }, 201);
    }
    return json({ detail: `Unhandled ${method} ${path}` }, 404);
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Release 与接入", exact: true }).click();
  await page.getByRole("button", { name: "添加 Provider", exact: true }).click();
  await page.getByLabel("Provider 连接名称").fill("Disposable Judge");
  await page.getByLabel("Provider API Key").fill(secret);
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(page.getByLabel("Provider API Key")).toHaveValue("");
  expect(testPayload).toMatchObject({ api_key: secret, model: "deepseek-chat" });

  await page.getByLabel("Provider API Key").fill(secret);
  await page.getByRole("button", { name: "加密保存", exact: true }).click();
  expect(createPayload).toMatchObject({ name: "Disposable Judge", api_key: secret });
  await expect(page.getByText(secret)).toHaveCount(0);

  await page.getByRole("button", { name: "轮换 Disposable Judge 的 Key", exact: true }).click();
  await page.getByLabel("新的 Provider API Key").fill(`${secret}-rotated`);
  await page.getByRole("button", { name: "验证并轮换", exact: true }).click();
  expect(rotatePayload).toEqual({ api_key: `${secret}-rotated` });
  await expect(page.getByLabel("新的 Provider API Key")).toHaveCount(0);

  await page.getByRole("button", { name: "停用", exact: true }).click();
  await page.getByRole("button", { name: "启用", exact: true }).click();
  await page.getByRole("button", { name: "删除 Disposable Judge", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "删除 Provider？" })).toBeVisible();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByText("Disposable Judge", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "添加 Provider", exact: true }).click();
  await page.getByLabel("Provider 连接名称").fill("Judge Provider");
  await page.getByLabel("Provider API Key").fill(secret);
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await page.getByLabel("Provider API Key").fill(secret);
  await page.getByRole("button", { name: "加密保存", exact: true }).click();

  await page.getByRole("button", { name: "评估器", exact: true }).click();
  await page.getByRole("button", { name: "新建评估器", exact: true }).click();
  await page.locator(".evaluator-form input").nth(0).fill("answer_quality");
  await page.locator(".evaluator-form input").nth(1).fill("1.0.0");
  await page.locator(".evaluator-form select").first().selectOption("llm_judge");
  await page.getByLabel("Judge Provider 连接").selectOption("provider-2");
  await page.getByLabel("Judge 模型").fill("deepseek-chat");
  await page.locator(".evaluator-form textarea").first().fill("Return a score for the actual agent result.");
  await page.getByRole("button", { name: "创建版本", exact: true }).click();

  expect(judgePayload).toMatchObject({
    evaluator_type: "llm_judge",
    provider_connection_id: "provider-2",
    judge_model: "deepseek-chat",
  });
  expect(judgePayload).not.toHaveProperty("api_key");
  expect(JSON.stringify(judgePayload)).not.toContain(secret);
});
