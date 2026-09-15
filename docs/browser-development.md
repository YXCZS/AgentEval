# 浏览器开发验收

后续修改前端时，使用 Playwright 的无障碍树定位元素，不使用截图坐标猜测位置。快照中的 `ref` 只在当前页面状态有效；点击或页面刷新后，先重新获取快照，再使用新的元素引用。

## 交互流程

1. 打开正在运行的 Web 地址。
2. 获取 `browser_snapshot`，确认页面标题、按钮名称和输入框名称。
3. 用 `browser_click`、`browser_fill_form` 或 `browser_select_option` 操作快照中的目标。
4. 操作后重新获取快照，确认页面状态或成功提示发生变化。
5. 用 `browser_network_requests` 检查真实 API 请求，用 `browser_console_messages` 检查前端错误。
6. 修改完成后，在桌面和移动端分别重复关键流程。

截图只用于视觉检查，不能作为点击目标。页面结构变化后，不要复用旧的 `ref`。

## 真实 API 浏览器冒烟测试

已有的 E2E 默认使用 Mock API，适合验证页面交互。需要验证真实后端时，先启动 API 和 Web，再运行可选的真实测试：

```powershell
$env:PLAYWRIGHT_REAL_API = "1"
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:18082"
$env:NEXT_PUBLIC_PROJECT_ID = "default-project"
$env:NEXT_PUBLIC_WORKSPACE_SESSION = "dev:default-project:real-api-local-session"
npm run test:e2e -- real-browser-smoke.spec.ts --workers=1
```

该测试会真实打开总览，依次点击全部主导航，检查对应页面标题，并将项目 API 的 5xx 响应视为失败。它不会创建或删除数据，页面中的 404 资源请求需要结合业务语义单独判断。

## 真实数据原则

- 页面展示内容必须来自 API 返回值。
- 按钮必须触发真实状态变化、真实请求或明确的页面导航。
- 没有真实资源时显示空状态，不填充成功数据。
- API 不可用时显示错误和重试入口，不显示空白页面。
