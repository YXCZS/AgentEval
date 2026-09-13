## 1. 已完成基础与 MVP 收口起点

- [x] 1.1 盘点生产运行路径中的 Demo、mock、确定性示例 Agent、Seed、故意回归开关和 `/run` 依赖，记录删除或测试专用迁移位置，并验证仓库搜索无未分类命中。
- [x] 1.2 将旧 `validate-with-real-llm-agent` 方案标记为被本变更取代，并验证 OpenSpec 实施入口选择当前变更。
- [x] 1.3 备份当前 PostgreSQL，完成隔离恢复和全迁移复演，并验证用户 Dataset、Trace、Score、Gate、API Key 与历史运行数据保留。
- [x] 1.4 增加 OpenTelemetry、OpenInference、OpenAI-compatible、AES-GCM 和 SDK 打包依赖/环境占位符，并验证后端、Worker、SDK 在无真实 Key 的干净环境可安装导入。
- [x] 1.5 增加与 HTTP endpoint 解耦的不可变 Agent Release、四种最终执行模式和四种证据策略，并验证 SQLite/PostgreSQL 迁移及 Prompt/Demo/mock 模式拒绝测试。
- [x] 1.6 复核并收口已起草的 Experiment Item Attempt 表，覆盖 Experiment、Case、repetition、attempt、external run ID、结果/错误/Trace/usage/运行元数据和时间；验证位置与外部 ID 唯一约束及终态证据不可修改后再标完成。
- [x] 1.7 复核并收口已起草的命名 Experiment 创建与不可变定义，固定 Dataset Version、完整 Case manifest、Release、Evaluator Version、证据策略和执行参数；验证历史定义不受后续资源变化影响后再标完成。

## 2. MVP 功能一：Experiment Item 生命周期 API

- [x] 2.1 实现按页读取不可变 Case manifest 与对应 Item 状态的 API，并验证排序、分页边界和空 Dataset 行为。
- [x] 2.2 实现带预期当前状态的 Item start API，并验证只有 `queued -> running` 合法、重复 start 幂等且并发竞争只有一个请求成功。
- [x] 2.3 分别实现 Item complete、fail、cancel API，并验证每条合法状态路径、必填结果/错误字段和终态不可逆。
- [x] 2.4 为结果提交增加规范化内容哈希与 external run ID 幂等控制，并验证相同重试返回原 Item、冲突重放不写入任何部分 Trace/Score/计数。
- [x] 2.5 从持久化 Item 和待处理证据/评分派生 Experiment 计数与终态，并验证客户端不能提交聚合 PASS、中断运行保持可恢复或 `INCOMPLETE`。
- [x] 2.6 对 manifest 和全部 Item 写 API 执行 Project 认证、Dataset Case 成员校验和跨 Project 拒绝，并通过集成测试验证越权请求不改变数据库。

## 3. MVP 功能二：最小可安装 Python SDK

- [x] 3.1 完成独立版本的 `agent-eval-sdk` typed Client 配置、Project 认证、超时和脱敏异常，并验证 wheel 构建、干净虚拟环境安装和异常文本不含凭据。
- [x] 3.2 增加服务版本/合同兼容性检查，并验证不兼容服务在创建任何数据前返回可操作错误。
- [x] 3.3 实现 Dataset 查询和明确的 Dataset Version 固定，并验证 SDK 读取的是不可变 Case manifest 而非可变化的当前版本别名。
- [x] 3.4 实现 endpoint-independent Agent Release 注册/选择和 `sdk_task` Experiment 创建，并验证 Release 身份、来源 revision、证据策略和执行参数进入不可变快照。
- [x] 3.5 实现 SDK 的 Dataset、Case、Release、Experiment、Item 类型模型和分页读取，并通过客户端/服务端合同测试验证字段漂移会失败而非静默丢失。

## 4. MVP 功能三：真实 Agent Task 执行

- [x] 4.1 先实现单线程同步 `task(case)` 执行与一个 Case 的 start/complete/fail 上传，并用调用方提供的真实函数验证平台不导入模型 SDK、不读取业务 Agent Key。
- [x] 4.2 扩展为多 Case 顺序执行和进度回调，并验证一个 Case 业务错误被记录后后续 Case 仍继续。
- [x] 4.3 增加异步 task 与有界并发，并验证实际同时运行数不超过配置、结果仍按 Case 正确关联。
- [x] 4.4 增加每 Case 超时和安全错误序列化，并验证超时 Item 失败、其他 Item 成功且凭据/请求头不会进入错误证据。
- [x] 4.5 增加可配置重试、取消和恢复语义，并验证每次尝试拥有独立 attempt、终态证据不被重试覆盖。
- [x] 4.6 增加结果确认、Telemetry flush 确认和 Experiment finalization，并验证进程在任一确认前退出都不会产生虚假完成状态。

## 5. MVP 功能四：SDK Trace 与证据完整性

- [x] 5.1 定义并发布 `agent_eval.*` Experiment/Item OTel 属性，保留 OpenInference 和 OTel GenAI 标准字段，并用 fixture 覆盖 AGENT、CHAIN、LLM、RETRIEVER、TOOL、TOOL_RESULT、错误、模型和 usage。
- [x] 5.2 为每个 SDK Case 创建真实根 task Span，并验证 Experiment、Item、Dataset、Case、Release、origin 和 repetition 属性完整。
- [x] 5.3 保持用户已安装框架/模型 instrumentor 的上下文和父子关系，并验证 LLM、Tool、Tool Result 子 Span 不被 SDK 展平或重写。
- [x] 5.4 实现 SDK 路径的认证 Trace 接收与 Item 关联，并验证伪造 Project/Dataset/Case 属性被拒绝。
- [x] 5.5 实现 source Trace/Span ID 幂等和结果/Telemetry 任意先后到达的最终关联，并验证重复投递不复制 Span 或 Score。
- [x] 5.6 实现 `trace_required`、`llm_required`、`tool_trajectory_required`、`rag_trajectory_required` 的服务端证据检查，并验证缺少模型、usage、工具或检索证据给出明确原因与 `INCOMPLETE`。
- [x] 5.7 将不完整证据传播到评分调度、比较、归因和 Gate，并验证有业务输出但缺少所需 Trace 的 Item 永远不能 PASS。

## 6. MVP 功能五：客观评分、比较与门禁

- [x] 6.1 将现有 Task Success、结构/Schema、Tool Selection、Tool Arguments、延迟和 usage 等客观 Evaluator 接到新 Item 终态，并验证评分输入来自持久化真实输出/Trace。
- [x] 6.2 按 Evaluator Version 聚合 valid/missing/error、平均值和通过率，并验证缺失证据不进入有效分母且不会被当作零分或通过。
- [x] 6.3 在同一 Dataset Version 上实现 baseline/candidate Case 与指标比较，并验证改善、持平、退化均按观测值展示，不预设 candidate 退化。
- [x] 6.4 对可对齐 Tool 轨迹执行保守首错归因，并验证证据不足返回 `INDETERMINATE` 而非编造原因。
- [x] 6.5 让 Release Gate 只消费持久化 Score、比较结果和证据状态，并验证 `INCOMPLETE`/`INDETERMINATE` 不能被规则默认转为 PASS。

## 7. MVP 功能六：最小中文可视化工作台

- [x] 7.1 将首页改为空数据零状态，只展示已经验收的 SDK 接入流程和 Project 凭据说明，并验证不生成成功率、Trace、Score 或 Experiment 假数据。
- [x] 7.2 完成 Dataset 创建、CSV/JSON/JSONL 预览导入、Version 与 Case 浏览，并用真实 API 数据验证所有保存/取消/分页按钮。
- [x] 7.3 完成 endpoint-independent Release 注册、列表和脱敏 SDK 安装/环境片段，并验证复制内容不含任何服务端或模型秘密。
- [x] 7.4 完成 Experiment 列表、进度和详情页，并验证页面可从 SDK 创建的 Experiment 实时读取 Item 状态而不在平台重跑 Agent。
- [x] 7.5 完成 Item → Trace → Span → Score 导航，展示 origin、Release、模型/usage、缺失证据和真实父子轨迹，并验证链接均指向同一持久化链路。
- [x] 7.6 完成 baseline/candidate 比较、归因和 Gate 页面，并验证改善、持平、退化、`INCOMPLETE`、`INDETERMINATE` 状态均有真实数据展示。
- [x] 7.7 针对 MVP 页面和每个主按钮运行桌面 Playwright 测试，并验证不存在无响应按钮或依赖示例 Agent 服务的路由。

## 8. MVP 功能七：删除所有模拟生产路径

- [x] 8.1 删除 runtime `examples/rag_agent`、`examples/order_agent`、`examples/custom_agent`、故意回归参数和 `seed_regression_demo.py`，并验证生产包不含内置 Agent 或生成答案路径。
- [x] 8.2 从 Compose 删除示例 Agent、Seeder、Demo profile 和生成 artifact 处理，并验证默认及全部生产 profile 只包含平台基础设施。
- [x] 8.3 从主 Worker 删除按 Case 调用 Agent `/run` 的调度，把合法旧 HTTP 配置标记为待 V1.2 迁移，并验证新 `sdk_task` Experiment 完全不需要 Agent endpoint。
- [x] 8.4 将仅用于平台测试的确定性输入/transport 移到 `tests/fixtures` 或测试模块，并验证生产包无法导入它们。
- [x] 8.5 删除 README、运维和用户文档中的 Demo/Seed/预制成功声明，替换为真实 SDK 流程，并逐条执行文档中的 MVP 命令。
- [x] 8.6 重新生成 OpenAPI 和 TypeScript 合同，并验证公共合同不宣传 Prompt Agent、示例 Agent、mock Provider 或尚未验收的入口。

## 9. MVP 功能八：真实 Tool Agent 验收与发布

- [x] 9.1 实现无 mock 参数的 live chat Provider preflight，在创建平台数据前验证 endpoint、Key、模型和上游 usage；验证任一真实依赖不可用时非零退出。
- [x] 9.2 在用户侧 SDK 进程实现真实模型选择工具、执行无副作用工具、接收实际结果并生成最终答案的 Tool Agent 验收任务；验证存储的 AGENT/LLM/TOOL/TOOL_RESULT 来自本次运行。
- [x] 9.3 创建版本化 Tool Dataset 与确定性业务断言，不硬编码模型实际输出，并验证每个 Case 有可解释的预期工具/状态证据。
- [x] 9.4 使用同一 Dataset Version 分别运行 baseline 和 candidate Release，执行评分、比较、归因和 Gate，并验证平台忠实报告改善、持平、退化或不确定结果。
- [x] 9.5 生成脱敏 MVP 验收摘要，包含时间、provider/model、Release、Dataset Version、Experiment ID 与证据检查，并验证页面可按 ID 打开对应持久化数据。
- [x] 9.6 仅在未跟踪 `.env` 配置用户提供的真实值并成功运行 Tool lane；没有真实上游成功和完整证据时本任务保持未完成。
- [x] 9.7 运行后端/SDK/Worker/前端/Playwright 测试、生产 Compose 单机启动、重启恢复和 tracked-file 密钥扫描；全部通过后才允许声明“SDK Tool Agent MVP 完成”。

## 10. V1.1：真实 RAG 与平台托管 LLM Judge

- [x] 10.1 增加 Project-scoped Provider Connection 的 provider、base URL、model、参数、掩码、状态、key ID 和密文记录，并验证全部读取/列表/导出响应不含明文凭据。
- [x] 10.2 实现 AES-256-GCM 随机 nonce、记录身份关联数据、密钥校验和轮换 key ID，并验证篡改、错误密钥和生产环境缺失密钥均 fail closed。
- [x] 10.3 更新 `.env.example` 与 Compose，使仅 API/Worker 接收平台加密密钥，Web/PostgreSQL/Redis 不接收模型 Key，并通过 Compose 配置检查验证。
- [x] 10.4 通过真实最小 OpenAI-compatible 请求实现 Provider 测试，并验证无效 Key、模型、URL、超时和畸形响应不会保存启用连接或调用 mock。
- [x] 10.5 扩展版本化 Judge Evaluator，绑定 Provider、模型、模板、rubric、结构化 Schema、采样和阈值，并验证后续 Provider/Evaluator 编辑不改变 Experiment 快照。
- [x] 10.6 在 Celery 中执行真实 Judge，加入有界并发、可重试错误退避、真实 usage/cost 和结构化响应校验，并验证失败 Score 不能通过 Gate。
- [x] 10.7 保留签名外部 Evaluator 适配器并统一 Provider/外部 Judge provenance，验证正常 LLM Judge 不要求用户另行部署服务。
- [x] 10.8 完成 Provider 创建/测试/轮换/禁用/删除和 Judge 绑定页面，并验证提交后明文 Key 从浏览器状态和响应中消失。
- [x] 10.9 实现真实 embedding、向量相似度检索和真实回答生成的 RAG live task，并验证 RETRIEVER/LLM Span、文档 ID 和上游 usage 入库。
- [x] 10.10 创建版本化 RAG Dataset、客观断言和真实 Provider Judge，运行 baseline/candidate、Comparison 与 Gate，并验证没有硬编码实际答案或强制 BLOCK。
- [x] 10.11 使用未跟踪真实 Key 成功运行 Tool 与 RAG 两条 lane 并生成脱敏摘要；两条真实链路均完整前不得声明“完整真实业务验收”。

## 11. V1.2：独立 OTel、语言无关 Remote 与签名 Trigger

- [x] 11.1 开放独立 OTel/OpenInference Experiment 模式，让已运行 Agent 通过标准 Span 属性关联 Item，并用语言无关 fixture 验证父子轨迹和 Project 校验。
- [x] 11.2 完成独立 OTel 的重复投递、晚到 Trace 和结果先后乱序关联，并验证不重复 Span/Score且“等待 Trace”最终消解。
- [x] 11.3 发布语言无关 REST 流程：创建 Experiment、读取 manifest、上传结果/Trace/可选 Score，并从非 Python HTTP 合同测试跑通。
- [x] 11.4 增加 Dataset-scoped Remote Trigger URL、非秘密 payload、受保护 header、启用状态和一次性签名 secret，并验证 secret 创建后不回显。
- [x] 11.5 实现带 delivery ID、时间、Experiment、Dataset Version 和 callback 的 HMAC-SHA256 run-level Trigger，并验证过期、重放和篡改请求被拒绝。
- [x] 11.6 增加 Trigger delivery 状态、有界重试/退避和可观测性，并验证 webhook 接受与 Agent Experiment 完成是两个独立状态。
- [x] 11.7 把合法旧 HTTP Agent 配置迁移为可选 Trigger/Remote Upload，彻底移除正式 per-Case `/run` 合同，并验证生产流程不再依赖同步 Agent 响应。
- [x] 11.8 在 Dataset/Experiment UI 中只开放已验收的 OTel、Remote Upload、Remote Trigger 入口和可复制配置，并验证命令可执行且不嵌入秘密。

## 12. V1.3：完整工作台与最终质量

- [x] 12.1 完善 Annotation Queue、人工评分审计与外部 Judge 证据导航，并验证所有人工修改保留 reviewer、前后值和时间。
- [x] 12.2 完善 Experiment/Trace/Comparison/Gate 的筛选、分页、缺失证据和高级归因体验，并验证所有统计均来自 API 持久化数据。
- [x] 12.3 对桌面和移动尺寸运行全部主按钮、对话框和导航 Playwright 覆盖，并验证不存在无响应控件或 Demo 服务依赖。
- [x] 12.4 从干净 checkout 启动生产 Compose，依次验证 SDK、OTel、Remote Upload、Trigger、Provider Judge、Comparison 和 Gate，重启后状态保留且单 Case 失败隔离。
- [x] 12.5 运行完整后端/SDK/Worker/前端测试和迁移兼容测试，修复现存静态类型问题，并验证非 live 测试不要求也不替换真实模型。
- [x] 12.6 执行 tracked-file、Git-history-aware 密钥扫描并人工检查 `.env.example`、日志和生成物，验证没有真实 Key、加密主密钥、授权头、私有响应或 `.env` 被跟踪。
- [x] 12.7 对齐 README 功能声明、OpenSpec 勾选和实际执行证据；任何未运行的 live 要求保持未完成，最终完整验收通过后再归档变更。
