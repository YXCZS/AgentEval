# 真实 Agent 测试说明

> 本文档回答三个核心问题：**测试用 Agent 的代码在哪里、本地怎么运行、线上怎么测试**。
> 所有数据均为真实数据，无任何模拟（mock）数据。最近一次真实验收：2026-09-18 13:22 UTC，`accepted: true`。

---

## 一、测试 Agent 代码地址

真实（非 fixture）测试 Agent 全部位于 `tests/live/` 目录：

| 文件 | 作用 |
| --- | --- |
| `tests/live/tool_agent.py` | **订单支持 Tool Agent**（真实调用 LLM）。`LiveToolAgent` 类，带 3 个只读工具：`lookup_order_status` / `check_cancellation_eligibility` / `check_refund_eligibility` |
| `tests/live/tool_dataset.py` | Tool Agent 的 25 个订单测试 case（`TOOL_CASES`）、3 个确定性评测器（`EVALUATOR_DEFINITIONS`）、资源准备函数 |
| `tests/live/run_tool_acceptance.py` | **端到端验收编排器**：Dataset → Evaluator → Release → Experiment（baseline/candidate 双跑）→ Trace → Score → Comparison → Gate |
| `tests/live/provider_preflight.py` | 真实 Provider 连通性预检（随机 challenge 验证，证明不是缓存/假响应） |
| `tests/live/rag_agent.py` / `rag_dataset.py` / `run_rag_acceptance.py` | RAG Agent 的对应实现与验收（6 个政策文档 + 6 条 case） |
| `scripts/drive_tool_acceptance.py` | 本地驱动脚本（注入平台+Provider 环境变量后调用验收入口） |

**重要区分**：`tests/fixtures/example_agents.py` **不是**真实 Agent —— 它仅是 HTTP fixture（源码内已注明「不入产品」）。真实 Agent 是 `tests/live/` 下的 `LiveToolAgent`，它通过 OpenAI SDK 真实调用 DeepSeek 模型。

### 真实 Agent 的核心逻辑（`tool_agent.py`）

`LiveToolAgent.run()` 每次对一条 case：
1. 用 OpenAI SDK 调 `deepseek-chat`，要求模型**必须且只能**选择一个工具；
2. 模型返回 tool_call → 本地执行对应只读工具（查订单状态/取消资格/退款资格）；
3. 把工具结果回传模型 → 模型基于工具结果生成最终答案；
4. 全程用 OpenTelemetry 记录 LLM/Tool span，产出真实 trace。

模型调用证据要求（防作弊）：
- 响应必须含 `request id` 和 `model` 字段，且不能把 API key 泄漏到公开元数据；
- `usage` 必须存在且 total_tokens > 0、input+output 与 total 一致；
- 5 个 case 每条都必须产出一条独立持久化的 trace。

---

## 二、本地如何运行

### 前置条件

1. **Docker 环境**：`infra/docker-compose.yml` 拉起 API + web + worker + postgres + redis。
2. **真实 Provider 凭据**（在项目根 `.env`）：
   ```
   LIVE_ACCEPTANCE_BASE_URL=https://api.deepseek.com
   LIVE_ACCEPTANCE_API_KEY=sk-...（真实 key）
   LIVE_ACCEPTANCE_CHAT_MODEL=deepseek-chat
   ```
3. **平台凭据**：API 起来后，用管理员 JWT 创建一个 project API key（`aek_` 前缀）。

### 步骤

**① 启动整套服务**

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
```

**② 登录管理员，拿到 project key**

```bash
# 登录
curl -X POST http://localhost:18080/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"Admin12345"}'
# 响应里的 user.project_id 就是私有 project id（默认 default-project）

# 用返回的 JWT 创建 project key
curl -X POST http://localhost:18080/projects/default-project/api-keys \
  -H 'Authorization: Bearer <JWT>' \
  -H 'Content-Type: application/json' \
  -d '{"name":"live-acceptance"}'
# 响应里的 key 字段即 aek_ 前缀的 project key
```

**③ 运行真实端到端验收**

```bash
# 方式一：用驱动脚本（已封装好环境变量）
cd D:\code\develop\project_prepare\agent-eval
C:/ProgramData/anaconda3/python.exe scripts/drive_tool_acceptance.py

# 方式二：手动设环境变量后直接跑
export AGENT_EVAL_BASE_URL=http://localhost:18080
export AGENT_EVAL_PROJECT_ID=default-project
export AGENT_EVAL_API_KEY=aek_default-project_<...>
python tests/live/run_tool_acceptance.py
```

**④ 查看结果**

验收产物写入 `artifacts/live/tool-acceptance-<时间戳>.json` 和 `.md`。控制台会打印完整 JSON，其中 `accepted` 字段为 `true` 表示整条链路真实跑通。

---

## 三、线上如何测试

线上测试与本地完全同构，唯一区别是 `AGENT_EVAL_BASE_URL` 指向线上 API 域名（HTTPS）。

| 配置项 | 本地 | 线上 |
| --- | --- | --- |
| `AGENT_EVAL_BASE_URL` | `http://localhost:18080` | `https://<线上域名>` |
| `AGENT_EVAL_PROJECT_ID` | `default-project` | 线上对应私有 project id |
| `AGENT_EVAL_API_KEY` | 本地创建的 `aek_` key | 线上通过浏览器界面创建的 `aek_` key |
| Provider 凭据 | `.env` 的 DeepSeek key | 同（或换正式 key） |

**线上验证要点**：
1. **Provider 预检**：`run_preflight()` 会发一个随机 challenge，验证返回内容确实来自 DeepSeek（防止缓存/假响应）。`challenge_verified: true` 是硬门槛。
2. **平台资源校验**：验收脚本会逐一 GET 校验 dataset 版本、两个 experiment、10 条 trace 的 API 路径都真实可读。
3. **Gate 门禁**：候选 run 的 `regression-gate` 必须 `status: passed`，且 3 个评测器 `pass_rate=1.0`。

---

## 四、最近一次真实验收结果（2026-09-18）

```
accepted: true
provider: endpoint_origin=https://api.deepseek.com, configured_model=deepseek-chat,
          response_model=deepseek-flash, challenge_verified=true, usage_present=true
dataset:   5 个订单 case（1 个 dataset version）
experiments: baseline(completed) + candidate(completed)，evidence_complete=true
comparison: new_failure=0, recovered=0, missing_evidence=0
gate:       status=passed（3 条规则全 passed，pass_rate=1.0）
traces:     10 条真实 trace（baseline 5 + candidate 5）
```

产物文件：
- `artifacts/live/tool-acceptance-20260918T132204Z.json`
- `artifacts/live/tool-acceptance-20260918T132204Z.md`

**结论**：整条「Dataset → Evaluator → Release → Experiment → Trace → Score → Comparison → Gate」链路用真实 DeepSeek 模型 + 真实订单数据 + 真实 project key 完整跑通，无任何 mock 数据。

---

## 五、界面真实验证记录（2026-09-18，真实浏览器）

用真实 Chromium（非模拟）逐项验证，截图存于 `artifacts/ui-verification/`：

| 验证项 | 结果 | 证据 |
| --- | --- | --- |
| 登录页渲染 | 通过 | `login-page.png` |
| 管理员登录 | 通过 | 登录后进入总览（修复了前端误读登录响应字段的 bug） |
| 总览真实数据 | 通过 | Trace 194 / 数据集 27 / Experiment 84；最近 Experiment 即真实验收产生的 `3671e287`（candidate）与 `fa59bba7`（baseline） |
| 成员管理界面 | 通过 | `members-view.png` |
| 邀请成员（UI 全流程） | 通过 | `member-created.png`：真实创建 `member1@example.com`（独立空间 `02e08bee-…`），API 交叉验证一致 |
| 新成员真实登录 | 通过 | API 登录 200，role=member，独立 project 隔离 |
| 停用成员 | 通过 | `member-disabled.png`：停用后该成员登录返回 401 |
| 启用成员 | 通过 | 启用后登录恢复 200 |
| 自保护逻辑 | 通过 | admin 对自己的「停用/删除」按钮正确禁用 |

> 说明：验证过程中发现并修复了一个真实缺陷——前端登录表单期望响应字段 `token`，而后端返回 `access_token`，导致此前浏览器登录必然失败（curl 直连 API 无法暴露此问题）。

---

## 六、清理说明

- 驱动脚本 `scripts/drive_tool_acceptance.py` 中的 project key 仅供本地开发验收使用；线上应通过浏览器「连接/API Keys」界面创建，不要硬编码到仓库。
- 验收产物 `artifacts/live/*.json` 不含 API key、prompt 原文或原始 provider 响应（脚本已主动脱敏）。

---

## 七、评测集扩充（2026-09-19）

评测集规模已从最初的 5+2 条扩充到 **31 条**，覆盖更完整的业务边界：

| 数据集 | 扩充前 | 扩充后 | 覆盖维度 |
| --- | --- | --- | --- |
| Tool Agent（`TOOL_CASES`） | 5 条 | **25 条** | 状态查询、取消资格、退款资格、未知订单、退款窗口边界（29/31/90 天） |
| RAG Agent（`RAG_CASES`） | 2 条 | **6 条** | 退款窗口、取消、物流追踪、退货地址、账号注销、价格调整 |

**关键扩充点**：
- **订单库**从 3 个扩到 11 个（`ORDER-1001` ~ `ORDER-1011`），覆盖 processing/shipped/delivered 三态 × 已付/未付 × 不同交付天数。
- **退款窗口边界**：新增 29 天（临界内）、31 天（临界外）、90 天（远超）三条，验证 `<= 30` 的边界判定。
- **未知订单**：新增取消/查询两类 not_found 场景，验证 Agent 不编造状态。
- **RAG 政策文档**从 3 个扩到 6 个，语义互斥（退款/取消/物流/退货/注销/调价），保证真实 embedding 检索稳定命中。

**验证**：扩充后 Tool 验收（50 trace = 25×2）与 RAG 验收（6 trace）均 `accepted: true`，单元测试 20 passed。

> 顺带修复一个真实缺陷：阿里云百炼 embedding 响应 `usage.prompt_tokens` 为 `None`（仅返回 `total_tokens`），
> 原 `_embedding_usage` 强校验会导致 RAG 预检失败。已在 `rag_agent.py` 与 `provider_preflight.py` 增加
> `total_tokens` 回退逻辑。

