# 回归门禁拦截演练记录（真实 LLM）

> 本演练用**真实 DeepSeek 模型**（非 mock）证明平台的质量门禁能拦截故意注入的回归，并在修复后放行。
> 完整证据见 `artifacts/regression-gate-drill/regression-gate-drill-*.json`。

---

## 一、演练目的

证明「发布门禁」的完整闭环，覆盖评测方法论中最关键的一环：

```
注册 baseline → 注册故意降级的 candidate → 跑实验 → 对比 → YAML 门禁策略 → BLOCK
                                                          ↓
                                    注册修复后的 candidate → 跑实验 → 对比 → 门禁 → PASS
```

核心价值：**门禁不是"跑过了就放行"，而是能稳定拦截真实回归，且修复后可恢复**。

---

## 二、演练方式

### 2.1 三个 Agent 版本

| 版本 | System prompt 差异 | 预期 |
| --- | --- | --- |
| `baseline` | 正常订单客服 prompt | 全绿 |
| `regression`（故意降级） | 追加一条错误规则：「对退款请求也一律调用 `check_cancellation_eligibility`」 | 工具选错 → BLOCK |
| `fixed` | 恢复正常 prompt | 全绿 → PASS |

### 2.2 门禁策略（YAML policy）

```yaml
version: regression-drill-v1
critical_tasks:
  - refund-recent-delivery
  - cancel-shipped-order
gates:
  - metric: live_tool_selection
    operator: gte
    threshold: 1
    severity: block
    scope: all
  - metric: live_tool_arguments
    operator: gte
    threshold: 1
    severity: block
    scope: all
  - metric: live_business_state
    operator: gte
    threshold: 1
    severity: block
    scope: all
```

`severity: block` 使门禁产出 CI 词汇（`BLOCK` / `WARNING` / `PASS`），而非 legacy 的 `passed` / `failed`。

---

## 三、演练结果（2026-09-19）

| 阶段 | 门禁状态 | 三项指标 actual | new failures |
| --- | --- | --- | --- |
| baseline | `completed`（参照） | 1.0 / 1.0 / 1.0 | — |
| **candidate（故意降级）** | **`BLOCK`** | 0.8 / 0.8 / 0.8 | **1** |
| **candidate（修复）** | **`PASS`** | 1.0 / 1.0 / 1.0 | **0** |

- **Provider**：`deepseek-chat`（响应模型 `deepseek-flash`），`challenge_verified: true`（随机 challenge 验证，证明非缓存/假响应）。
- **降级版本被拦截**：门禁返回 `BLOCK`，三项指标 actual 全部降到 0.8（25 条 case 中 1 条退款 case 工具选错）。
- **修复版本放行**：恢复正常 prompt 后门禁返回 `PASS`，三项指标恢复 1.0。

**结论**：`regression blocked AND fixed passed` → 演练 `PASS`。

---

## 四、这个演练证明了什么（面试可讲）

1. **门禁不是摆设**：一个真实的（故意注入的）prompt 回归能被 YAML 策略稳定拦截，产出机器可读的 `BLOCK`。
2. **首错归因**：对比报告能定位到具体 case（`refund-recent-delivery`）和具体指标（`live_tool_selection`），不是笼统的"没过"。
3. **修复闭环**：同一门禁策略下，修复版本恢复 `PASS`，证明门禁有区分度（不会永久锁死）。
4. **全程真实**：无 mock 数据，真实 DeepSeek 调用 + challenge 验证。

---

## 五、运行方式

```bash
# 依赖 .env 里的 LIVE_ACCEPTANCE_*（DeepSeek）+ AGENT_EVAL_*（平台 key）
cd D:\code\develop\project_prepare\agent-eval
C:/ProgramData/anaconda3/python.exe scripts/run_regression_gate_drill.py
```

产物写入 `artifacts/regression-gate-drill/`，控制台输出完整 JSON。

> 说明：`regression` 版本的降级方式（追加错误工具规则）是一种**受控回归注入**，
> 用于演示门禁能力。真实生产中回归来自模型/代码/prompt 的自然变更，门禁机制完全一致。
