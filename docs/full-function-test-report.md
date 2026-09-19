# Agent Eval Workbench 全功能测试报告

> 测试时间：2026-09-18 18:00–18:50 (GMT+8)
> 测试环境：Docker Compose 运行中的真实容器（API :8000 / Web :3000 / Postgres / Redis / Worker）
> 测试方式：真实 Python SDK + 真实 HTTP 请求 + pytest 自动化基线 + 真实模型调用

---

## 一、测试结论（一句话）

**项目后端功能全面跑通，核心评测链路、回归分析、安全边界全部真实可用；发现 1 个上线阻塞级 bug（SDK 发布产物过时）和 1 个配置漂移隐患（历史遗留）。**

---

## 二、真实端到端功能测试结果

用真实 Python SDK（`ExperimentRunner`）跑通了从 Dataset 到 Gate 的完整评测闭环，**无任何 mock/fallback**：

| # | 功能模块 | 测试方式 | 结果 |
|---|---|---|---|
| 1 | 鉴权边界 | HTTP | ✅ 无凭证 401 / 错误 key 401 / browser session 200 / agent key 200 |
| 2 | SDK 契约兼容 | SDK | ✅ `contract=agent-eval-sdk, major=1` |
| 3 | Dataset 版本化 | SDK+HTTP | ✅ 创建→Version→Cases，case id 唯一性校验 |
| 4 | 确定性 Evaluator | SDK+HTTP | ✅ exact_match / task_success 创建与评分 |
| 5 | Agent Release 注册 | SDK | ✅ endpoint-independent release |
| 6 | **Experiment 完整评测** | **SDK** | ✅ `sdk_task` 模式，2 cases 全完成，evidence=complete |
| 7 | Trace 摄入 + 观测 | SDK | ✅ OTel trace 上报，agent+evaluator span |
| 8 | **评分准确性** | HTTP | ✅ exact_match 正确判 1.0，task_success 正确判 0.0 |
| 9 | Report 聚合 | HTTP | ✅ pass_rate/average 正确，支持 json/csv 导出 |
| 10 | **回归对比 Comparison** | HTTP | ✅ baseline 1.0 vs candidate 0.0，new_failures 标记 critical |
| 11 | **首错归因 Attribution** | HTTP | ✅ 证据不足时诚实返回 `indeterminate` |
| 12 | **Release Gate** | HTTP | ✅ 回归正确返回 `status=failed` + failed_cases |
| 13 | Trace 脱敏 | HTTP | ✅ api_key/token/bearer → redacted，正常字段保留 |
| 14 | **真实模型连接** | HTTP | ✅ DeepSeek `challenge_verified=true`，token 用量真实 |
| 15 | Remote trigger 权限 | HTTP | ✅ X-Project-Key 创建 → 403（需 browser） |

### 关键实测证据

**完整评测链路（确定性 task）**：
```
Dataset created → version_id=c4fda207...
Evaluators: exact_match + task_success
Agent release: version 74
Experiment: status=completed, 2 completed / 0 failed
  ├─ case-1: evidence=complete, trace=22957768...
  └─ case-2: evidence=complete, trace=411f6690...
Report: exact_match pass_rate=1.0, task_success pass_rate=0.0
```

**回归对比链路（baseline vs 故意写错的 candidate）**：
```
Baseline:  pass_rate=1.0 (2/2 正确)
Candidate: pass_rate=0.0 (2/2 写错)
Comparison: new_failures=[r-case-1(critical), r-case-2(critical)]
  └─ first_error: category=indeterminate (证据不足，不猜)
Gate: status=failed, actual=0.0, failed_cases=[r-case-1, r-case-2]
```

**真实 DeepSeek 模型验证**：
```json
{
  "challenge_verified": true,
  "response_model": "deepseek-flash",
  "input_tokens": 35, "output_tokens": 18, "total_tokens": 53
}
```

---

## 三、pytest 自动化测试基线

| 测试层 | 结果 | 耗时 |
|---|---|---|
| 单元测试 (tests/unit) | **211 passed** | 85s |
| 集成测试 (tests/integration) | **103 passed** | 109s |
| SDK 测试 (sdk/python) | **11 passed** | 1.6s |
| **总计** | **325 passed** | — |

> 与 HANDOFF.md 声明的「自动化基线 325 passed」**完全一致**，印证了文档的准确性。

---

## 四、发现的问题（按严重度排序）

### 🔴 P0-1：SDK 发布产物过时（上线阻塞）

**现象**：`sdk/python/dist/agent_eval_sdk-0.1.0-py3-none-any.whl` 是**旧构建**，其 `AgentRelease` 模型包含 `agent_connection_id` 和 `endpoint_config` 两个必填字段。但当前 API 的 `AgentReleaseResponse` 已移除这两个字段（"endpoint-independent Agent Release" 重构的一部分）。

**影响**：任何用户安装这个 wheel 并调用 `register_release()`，都会报 `ValidationError: agent_connection_id Field required`，**SDK 完全不可用**。

**实测**：
```
ValidationError: 2 validation errors for AgentRelease
  agent_connection_id  Field required
  endpoint_config      Field required
```

**修复方案**：重新构建 wheel。源码 `sdk/python/src/agent_eval/models.py` 已经是新版（字段正确），只需 `python -m build` 重新打包并替换 `dist/` 下的旧 wheel。

**附带问题**：`sdk/python/build/` 目录有残留的 `cpython-312` 旧 `.pyc` 产物，导致构建清理步骤 `SHFileOperationW` 失败。需清理该目录后重新构建。

### 🟠 P1-1：端口配置漂移（历史遗留）

`.env`(18080) vs 容器实际(8000) vs `.env.local`(18080) vs Dockerfile ARG 默认(8000) 四个源不一致。当前容器恰好「能连通」（JS 嵌入 8000 == API 8000），但任何人按 `.env` 重建容器就会真的连不上。需统一端口。

### 🟡 轻微提示

- 测试依赖安装时，清华 pip 镜像源缺 `setuptools`/`sqlalchemy`，需用官方 pypi 源（网络偶发超时，加 `--timeout` 重试即可）。
- 少量 `StarletteDeprecationWarning`（`HTTP_422_UNPROCESSABLE_ENTITY` → `CONTENT` 等），非功能性，可后续清理。

---

## 五、安全边界验证（全部通过）

| 安全项 | 验证结果 |
|---|---|
| Project Key 只存 HMAC 哈希 | ✅ 明文只返回一次，DB 存哈希 |
| 业务模型 Key 不入平台 | ✅ 设计确认 |
| Provider 凭据 AES-256-GCM 加密 | ✅ 掩码 `sk-...9acd`，key_id 绑定 |
| Trace 双重脱敏 | ✅ 敏感字段 → `{"__agent_eval_redacted":true}` |
| 证据不足不默认通过 | ✅ Gate 返回 failed/indeterminate |
| 不可变设计 | ✅ 版本/实验/终态 attempt 三层不可变 |

---

## 六、上线前必做清单

1. **【阻塞】重新构建 SDK wheel**，替换 `dist/` 下的过时产物
2. **统一端口配置**，消除 18080/8000 漂移
3. 生产密钥替换 `.env` 里的 `local-compose-*` 占位值
4. 启用 HTTPS + 反向代理
5. `.gitignore` 补 `.workbuddy/`（含本次测试脚本）
6. 更新 README 截图与运行说明
