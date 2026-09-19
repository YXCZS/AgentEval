import { expect, test } from "./test-fixture";

// A fake but structurally valid JWT whose `exp` is far in the future. The
// client only decodes `exp` for an early-logout hint; signature verification
// happens server-side, so a well-formed payload is enough to reach the app.
function fakeToken(expInSeconds: number): string {
  const header = { alg: "HS256", typ: "JWT" };
  const payload = {
    sub: "user-1",
    email: "admin@example.com",
    role: "admin",
    exp: Math.floor(Date.now() / 1000) + expInSeconds,
  };
  const enc = (obj: object) =>
    Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${enc(header)}.${enc(payload)}.signature`;
}

async function seedSession(page: import("@playwright/test").Page, token: string): Promise<void> {
  await page.addInitScript(([stored]) => {
    window.localStorage.setItem(
      "agent-eval.auth",
      JSON.stringify({
        token: stored,
        projectId: "default-project",
        userId: "user-1",
        email: "admin@example.com",
        displayName: "Admin",
        role: "admin",
      }),
    );
  }, [token] as const);
}

test("a protected 401 response signs the user out and returns to login", async ({ page }) => {
  await seedSession(page, fakeToken(3600));

  // Every protected request under this project returns 401, simulating an
  // expired or revoked session on the server.
  await page.route("**/projects/default-project/**", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "invalid or expired token" }),
    }),
  );
  await page.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", environment: "test" }),
    }),
  );

  await page.goto("/");

  // The app briefly shows the workbench (auth restored from storage), then the
  // overview's protected requests get 401 and must drop back to the login form.
  await expect(page.getByRole("button", { name: "登录", exact: true })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.locator(".login-card")).toBeVisible();

  // The stale session must be cleared so a reload does not bounce back.
  const stored = await page.evaluate(() => window.localStorage.getItem("agent-eval.auth"));
  expect(stored).toBeNull();
});

test("an unexpired session stays on the workbench when the API returns 200", async ({ page }) => {
  await seedSession(page, fakeToken(3600));

  await page.route("**/projects/default-project/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/traces") || pathname.endsWith("/runs") || pathname.endsWith("/experiments")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0, offset: 0, limit: 25, next_offset: null }),
      });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", environment: "test" }),
    }),
  );

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "评测总览" })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".login-card")).toHaveCount(0);
});
