# Agent Eval Workbench 缺陷分析报告

> 视角：产品经理 · UI 设计师 · 测试工程师 · 全栈工程师
> 日期：2026-09-19
> 分析对象：自托管 Agent 质量评测平台（Next.js 16 + FastAPI + PostgreSQL + Redis + Celery + OpenTelemetry）

---

## 一、总体结论

项目架构成熟、工程化程度高：后端 325+ 测试通过、前端 10 个 Playwright e2e spec、真实 DeepSeek 端到端验收通过、多用户 + JWT + 项目空间隔离已落地。**核心链路（Dataset→Evaluator→Release→Experiment→Trace→Score→Comparison→Gate）功能完整、数据真实。**

但距离"可直接商用/上线"仍有若干**体验与健壮性缺口**，主要集中在：前端缺全局鉴权失效处理、生产配置缺硬校验、可观测性不完整、部分 UI 交互细节粗糙。以下按四视角分级列出（P0 阻断 / P1 重要 / P2 优化）。

---

## 二、产品经理视角

### P1-1 登录态过期无感知（关键体验缺陷）
**现象**：前端 `api-client.ts` 的 `loadAuth()` 只校验 `token`/`projectId` 是字符串，不解析 JWT 的 `exp`。后端 JWT 过期后（默认 7 天），用户仍停留在"已登录"工作台界面，直到某个 API 调用返回 401 才在对应 view 里报错——**且没有任何 view 会把 401 引导回登录页**。
**影响**：用户被"假登录"状态卡住，刷新数据报错却不知道要重新登录。
**状态**：✅ 已修复——`fetchApi` 加 401 全局拦截（排除 `/auth/login`），`clearAuth()` + 派发 `agent-eval:auth-expired` 事件，`page.tsx` 监听后跳登录；`loadAuth` 增加 JWT `exp` 解析（30s 缓冲提前判定过期并清理本地会话）。

### P1-2 成员被停用后，已登录的旧 token 仍有效
**现象**：~~停用成员后旧 JWT 仍可继续访问~~ —— **此条为误判**。经核实，`get_current_user_from_bearer`（users.py:59）与 `require_project_access`（auth.py:107）均已校验 `user.active`，停用成员后其旧 token 立即在 `/auth/*` 与 `/projects/*` 接口返回 401，权限管控语义正确。上一轮分析只看了 `security.decode_access_token`（它确实只查签名/exp），未追踪到鉴权依赖层的 active 校验。

### P1-3 删除成员是"硬删除"且不可撤销
**现象**：`DELETE /auth/users/{id}` 级联删除该成员的私有 project 及其全部数据（trace/实验/报告），无软删除、无回收站、无二次确认的审计留痕。
**影响**：误删即数据灾难，且无法追溯。
**建议**：改为软删除（标记 `deleted_at` + 保留数据 N 天），或至少提供"导出该成员数据"后再删除的引导。

### P2-1 新手引导缺失
**现象**：空项目时总览有 `overview-empty` 引导，但进入数据集/评估器/Release 等模块后，没有 step-by-step 的上手指引或"首次使用引导"。
**建议**：做一个 onboarding 流程（创建 Project Key → 登记 Release → 建数据集 → 跑第一个 Experiment → 看报告）。

### P2-2 缺少用量/配额与账单概念
**现象**：多用户体系下没有每个成员的 Trace 量、Experiment 量、存储占用等配额展示，管理员无法感知资源消耗。
**建议**：在成员管理或总览增加用量统计（对自托管部署，至少提供总量监控）。

---

## 三、UI 设计师视角

### P1-1 主按钮视觉层级不突出
**现象**：`.primary` 按钮此前是灰 `surface` 背景，与 `.outline-button` 视觉接近，"新建评测""邀请成员"等主 CTA 不够醒目。**本轮已修复**——改为品牌蓝渐变。
**遗留**：侧边栏「新建评测」顶栏按钮与内容区的主按钮需保持一致的视觉权重，避免主 CTA 淹没在工具栏中。

### P1-2 角色信息是纯文字，辨识度低
**现象**：成员列表里"管理员/成员"只是跟在姓名后的灰色小字（`.muted-helper`），一眼难以区分权限等级。**本轮已修复**——引入 `.role-badge`（admin 金色 / member 蓝色徽章）。

### P2-1 状态色语义未完全统一
**现象**：`success/danger/warning` 状态色已定义，但部分状态标签（如 Trace 的 `queued/running`）用的是中性灰，与"进行中"应有的视觉区分不够；`cancelled` 与 `partial` 共用 warning 色，语义可再细化。
**建议**：给 `running` 一个脉动动画或主题色，让"正在跑"的状态一眼可见。

### P2-2 移动端/小屏适配不完整
**现象**：虽然已有 `@media` 响应式规则，但侧边栏在窄屏下仍是固定 248px 顶栏式布局，成员管理卡片在小屏下操作按钮可能换行拥挤。
**建议**：补充 768px 以下的侧边栏折叠（汉堡菜单）与卡片纵向堆叠验证。

### P2-3 空状态与加载态可更精致
**现象**：`panel-placeholder` 是统一的灰色占位，各模块空状态缺少差异化插图/动作引导。
**建议**：为空状态加图标化的"下一步动作"按钮，与总览的 `overview-empty` 引导一致。

---

## 四、测试工程师视角

### P1-1 前端缺全局 401 与错误边界测试
**现象**：后端集成测试覆盖充分（auth/datasets/runs/reports/traces/users 等 18 个文件），但前端 e2e 未覆盖"token 过期 → 跳登录"这一关键负路径。
**建议**：新增一条 e2e：模拟 401 响应，断言自动登出并跳转登录页。

### P1-2 SDK wheel 过时（已修复，需加回归护栏）
**现象**：`sdk/python/dist/*.whl` 是旧构建，`models.py` 仍含已废弃的 `agent_connection_id`/`endpoint_config` 字段，`runner.py` 缺失 `_finalize`/`_refresh_finalized_items`（服务端评测器等待 + 证据回填）。**本轮已重建 wheel**。
**遗留**：缺一个"wheel 与 src 一致性"的 CI 校验，防止再次漂移。
**建议**：加 CI 步骤——构建 wheel 后逐文件 diff `src/`，不一致即 fail。

### P1-3 临时驱动脚本未清理
**现象**：`tests/live/_drive_tool_acceptance.py` 是验收用的临时驱动脚本（下划线前缀），已混入源码树。
**状态**：✅ 已修复——移动到 `scripts/drive_tool_acceptance.py` 并修正 ROOT 路径，文档同步更新。

### P2-1 前端单元测试缺失
**现象**：前端有 10 个 Playwright e2e spec，但**无组件级单测**（无 vitest/jest）。`members-view`、`login-view` 等纯逻辑（表单校验、禁用自保护逻辑）只能靠 e2e 覆盖，回归成本高。
**建议**：引入 vitest + React Testing Library，为关键组件（登录、成员管理、状态标签）补单测。

### P2-2 缺少性能/压测基线
**现象**：无 Trace 高并发摄入、大数据集评测的压测基准，`trace_max_spans=1000` 等上限未经过压测验证。
**建议**：补一组 locust/k6 压测脚本，验证 Worker 并发与 Trace 摄入吞吐。

---

## 五、全栈工程师视角

### P0-1 生产环境密钥无强制校验
**现象**：`settings.py` 中 `jwt_secret`、`api_key_salt`、`workspace_session_secret` 都有 `development-*-change-me` 弱默认值，且 `app_env` 默认 `development`。**若部署时忘了配 `JWT_SECRET`，系统会静默用弱默认密钥运行**，JWT 可被伪造。
**影响**：安全级别缺陷，生产环境必须阻断。
**状态**：✅ 已修复——`settings.py` 新增 `model_validator`，`app_env=production` 时若 `JWT_SECRET`/`API_KEY_SALT`/`WORKSPACE_SESSION_SECRET` 仍为弱默认值则抛 `ValueError` 拒绝启动（`credential_encryption_key` 已有 `validate_runtime_credential_configuration` 兜底）。新增 3 个单测覆盖（拒绝占位/接受强密钥/开发环境放行）。

### P1-1 前端全局可变状态（模块级 let）
**现象**：`api-client.ts` 里 `PROJECT_ID`、`SESSION` 是模块级 `let` 可变绑定，登录时被改写。所有 view 直接 import 读取。这是隐式全局状态，多用户切换/SSR/hot-reload 时存在时序与并发隐患。
**建议**：改为 React Context（AuthProvider）或至少提供 `getSession()` 访问器，避免直接读裸变量。

### P1-2 缺少统一的错误监控与告警
**现象**：后端有 OpenTelemetry trace 采集，但无错误聚合（如 Sentry）、无日志结构化检索、无 Worker 任务失败告警。Celery worker 任务失败后难以及时发现。
**建议**：接入 Sentry/自建错误采集 + worker 失败队列监控 + 健康检查告警（已有 `/health`，补告警规则即可）。

### P1-3 数据删除是物理级联，无备份触发
**现象**：删除成员级联删 project 数据是直接物理删除（见产品视角 P1-3），且未与 `backups/` 机制联动。
**建议**：删除前自动落一份归档备份（已有 `backups/` 目录结构，可复用）。

### P2-1 端口/环境变量文档化不足
**现象**：`.env` 中 `18080` 与容器内 `8000` 的映射关系、`NEXT_PUBLIC_API_URL` 的前后端拼接，散落在各处，易漂移（上轮已确认当前一致）。
**建议**：在 `docs/operations.md` 统一一张"环境变量 + 端口映射"对照表，并加 CI 校验。

### P2-2 数据库迁移回滚演练未自动化
**现象**：`migrations/` 用 Alembic，有 `database-migration-rehearsal.md` 文档，但迁移的升级/回滚演练是手工的。
**建议**：CI 中加"空库 upgrade + downgrade 往返"自动化演练。

### P2-3 `.workbuddy/` 未纳入版本控制忽略（已修复）
**现象**：项目内存目录 `.workbuddy/` 未被 `.gitignore` 排除，可能误提交。**本轮已补充**。

---

## 六、优先级速览

| 级别 | 编号 | 问题 | 状态 |
| --- | --- | --- | --- |
| P0 | 全栈-1 | 生产密钥弱默认值无强制校验 | ✅ 已修复（本轮） |
| P1 | 产品-1 | 登录态过期无感知、无 401 全局处理 | ✅ 已修复（本轮） |
| P1 | 产品-2 | 停用成员后旧 token 仍有效 | ✅ 误判，已核实早已正确 |
| P1 | 产品-3 | 删除成员硬删除不可撤销 | 待修 |
| P1 | 测试-1 | 前端缺 401 负路径 e2e | 待修（可补） |
| P1 | 测试-2 | SDK wheel 过时（缺 CI 护栏） | ✅ 已重建，护栏待加 |
| P1 | UI-1/2 | 主按钮层级 + 角色徽章 | ✅ 本轮已美化 |
| P2 | 多项 | 引导/配额/单测/压测/监控/文档 | 排期优化 |

---

## 七、本轮已完成的改进

1. **UI 美化**：主按钮品牌蓝渐变、品牌标记蓝青渐变、登录页沉浸式背景光晕、侧边栏活跃项高亮条、面板/指标卡悬浮微交互、成员角色徽章（admin 金/member 蓝）、输入框聚焦态、按钮按压反馈、空状态柔和化。
2. **SDK wheel 重建**：`sdk/python/dist/agent_eval_sdk-0.1.0-py3-none-any.whl` 已刷新，`models.py`（移除废弃字段）+ `runner.py`（补 `_finalize`/`_refresh_finalized_items`）与 src 一致，并新增 `scripts/rebuild_wheel.py` 可复现脚本。
3. **`.gitignore`** 补充 `.workbuddy/`。
4. **端口漂移确认**：当前 `18080→8000` 映射一致，无漂移。
5. **生产密钥强校验（P0）**：`settings.py` 增加 `model_validator`，production 下拒绝弱默认 JWT/salt/session secret，3 个单测覆盖。
6. **前端全局 401 处理（P1）**：`fetchApi` 拦截 401（排除登录接口）→ 清会话 + 派发事件 → 跳登录；`loadAuth` 解析 JWT `exp` 提前判定过期。

