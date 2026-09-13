## Context

See [proposal.md](proposal.md) for the motivation. The existing implementation has a useful foundation: project-scoped APIs, Dataset versions, asynchronous case execution, canonical Trace/Span records, Scores, reports and a regression gate. However, its product language and several code paths still centre on a managed Prompt Runner. This conflicts with the requested direction and obscures the stronger Agent-quality story.

The workflow below is based on inspected, current sources rather than an assumed UI pattern:

| Observed source | Verified workflow fact | Decision taken here |
| --- | --- | --- |
| Phoenix docs and `phoenix-client/src/experiments/runExperiment.ts` | Projects organize traces; an experiment receives `dataset`, `task`, `evaluators`, optional concurrency/repetitions, then records runs and traces. | Project-scoped Trace + Dataset Version + external task connection + evaluators form an Experiment. |
| Langfuse Observability overview | The getting-started path is to set up a first Trace; traces are the foundation for continuously evaluating and improving an application. | The default user-facing entry is Agent Trace ingestion, not agent registration or prompt configuration. |
| Langfuse Evaluation/Dataset docs | Evaluation has both online live-trace scoring and offline dataset experiments; datasets are Project-scoped, versioned and can be created from CSV, UI/API or observations using field mapping. | Online and offline loops coexist and connect through Trace/Observation-to-Dataset. |
| TrajectIQ `ARCHITECTURE.md` | Phoenix owns Trace ingestion/storage/datasets/experiments/general evaluators; TrajectIQ adds version comparison, task-level regression, first-error attribution, YAML gate and CI artifacts. | Our differentiating layer is diagnostics and release decisions above the Trace/eval foundation. |

`Project` is therefore a common data and access boundary, not an invented mandatory onboarding form. In the single-user self-hosted edition, a default Project removes needless onboarding while preserving a correct future multi-project boundary.

## Goals / Non-Goals

**Goals:**

- Make the product Trace-first for already-running RAG, Tool and Custom Agents.
- Retain both production monitoring feedback and repeatable offline evaluation.
- Make every quality conclusion traceable to Dataset case, agent release, evaluator version and execution evidence.
- Provide a narrowly scoped but real TrajectIQ-style diagnosis and CI gate story.
- Keep the system runnable on one machine with Docker Compose and explainable in an interview.

**Non-Goals:**

- No Prompt Agent, Prompt Runner, prompt hosting, model provider settings, playground, model API key management, or execution of uploaded Agent source code.
- No claim of complete OpenTelemetry/OpenInference ecosystem coverage on day one; HTTP JSON is the smallest production-ready path, then standard OTLP/OpenInference adapters.
- No copying Phoenix, Langfuse or TrajectIQ source code, branding, UI, proprietary SaaS internals or private wire protocols.
- No complete SaaS tenancy, billing, RBAC matrix, Kubernetes deployment, browser/desktop benchmark environment, or universal score.

## Decisions

### 1. Trace-first dual quality loop

The system implements two entry paths that converge, matching Phoenix and Langfuse:

```text
Online loop (observability)
Running Agent -> Trace ingest -> Trace/Span inspection -> online Score/Annotation
                                       |                         |
                                       +-> map selected Observation to Dataset Case

Offline loop (evaluation)
Dataset Version + Agent release + Evaluator Set -> Experiment -> Case results/Traces/Scores
                                                               -> comparison -> diagnosis -> Gate
```

The system does not force a linear “register Agent first” sequence. A user who already has production telemetry can begin with Trace; a user preparing a release can begin by registering a test connection and importing a Dataset. Both are actual workflows in the reference products.

**Alternative considered:** a single Agent -> Dataset -> Run wizard. Rejected because it hides the online quality loop, makes production failure capture awkward, and does not reflect the inspected Phoenix/Langfuse documentation.

### 2. Project is an internal first-class boundary with automatic default initialization

Every persisted object owns `project_id`. API keys and ingestion credentials resolve to a Project. Self-hosted development and single-project deployments create `default-project` through migration/bootstrap and select it automatically in the Web UI.

```text
Project
├── AgentConnection -> AgentRelease
├── Trace -> TraceSpan -> OnlineScore / Annotation
├── Dataset -> DatasetVersion -> DatasetCase
├── Evaluator -> EvaluatorVersion -> optional External Evaluator Connection snapshot
└── Experiment -> CaseExecution -> Trace/Score/AggregateMetric
                   └── RegressionComparison -> Attribution -> GateResult
```

**Alternative considered:** remove Project completely because the user has one server. Rejected because it breaks the access/data boundary used by both Phoenix and Langfuse and makes later multi-project evolution a destructive migration. A hidden default Project has negligible user complexity.

### 3. Replace managed Agent with an External Agent Connection

`AgentConnection` is a configuration reference to an Agent the user runs. It has a type of `rag`, `tool` or `custom`, connection protocol, endpoint/ingestion identity, secret reference, timeout and a human-visible release label. `AgentRelease` is immutable enough to identify the tested code/prompt/model deployment, for example Git SHA, image tag or user-supplied semantic version.

Two supported execution modes remain intentionally separate:

```text
Instrumented production mode:
User Agent -> HTTP JSON / OpenInference / OTLP -> Trace ingest

Offline test mode:
Experiment worker -> AgentConnection test endpoint -> normalized result + Trace
```

The endpoint contract is an explicit test adapter, not arbitrary remote code execution. It receives a Case input and returns output, optional usage, tool calls and canonical trace or trace correlation ID. Existing HTTP Agent behavior can be migrated to it.

**Alternative considered:** accept uploaded Python/JavaScript Agent code to avoid a deployed endpoint. Rejected for security, reproducibility and operational complexity. It also diverges from Phoenix/Langfuse observability-first integration.

### 4. Canonical Trace preserves evidence while adapters evolve

The canonical model remains compatible with OpenInference semantics and retains unknown source fields in `extensions`:

```text
Trace(trace_id, project_id, source, agent_release_id, status, timestamps, attributes)
  -> Span(span_id, parent_span_id, kind, input, output, error, usage, attributes, extensions)
```

Adapter priority is:

1. Existing authenticated HTTP JSON canonical ingest.
2. OpenInference-shaped JSON adapter.
3. OTLP HTTP protobuf receiver or collector forwarding adapter.
4. OTLP gRPC only after HTTP and compatibility tests are stable.

This matches Phoenix's OpenTelemetry/OpenInference approach while keeping the first deployable interface simple. Unknown fields are not discarded. Sensitive fields are redacted before storage and no connection secret appears in a Trace. Within a Project and source, source Trace/Span identifiers act as idempotency keys: a retry of the same payload returns the existing record, while a conflicting replay is rejected. Configured limits for request size, span count and nesting depth reject unsafe payloads before any partial persistence.

### 5. Dataset and Experiment follow source-product reproducibility rules

Dataset Case uses the smallest common core:

```text
input, expected_output?, metadata
```

RAG and Tool cases add only necessary structured extensions:

```text
reference_context?, expected_tools?, expected_state?, criteria?, output_schema?
```

Dataset changes create a `DatasetVersion`. A Trace/Observation conversion records `source_trace_id`, selected span IDs and mapping configuration. An Experiment freezes Dataset Version, AgentRelease, EvaluatorVersion set, optional External Evaluator Connection identity, thresholds, concurrency/repetition and execution configuration. Baseline/candidate comparison requires the same Dataset Version and compatible evaluator identity.

**Alternative considered:** mutable datasets with run-time lookup of current cases. Rejected because it makes historical comparison non-reproducible and contradicts both Phoenix and Langfuse versioned dataset behavior.

### 6. Evaluators are layered; no universal score

The evaluator contract accepts `case`, `execution`, `trace` and context, returning normalized Score evidence. The first real set is purposefully limited:

| Agent focus | Deterministic metrics | Judge / review metric |
| --- | --- | --- |
| RAG | context presence/reference checks, citation/format, latency, token/cost, error rate | faithfulness, answer correctness, relevance |
| Tool | task success, tool name/order/argument/state checks, latency, token/cost, error rate | final-answer adequacy, trajectory quality |
| Custom | JSON schema, exact/state assertions, latency, token/cost, error rate | criteria-based task success |

An optional LLM Judge is an External Evaluator Connection operated by the user, not a platform-managed model provider. The platform calls its documented HTTP judge contract and stores only an endpoint, protected credential reference, timeout, model/release and scoring provenance; it never stores a model-provider API key or exposes a provider-settings UI. The Judge records its rubric, model/release, prompt template version, raw structured result and normalized result. Deterministic checks are preferred for a blocking Gate. Human annotations remain independent evidence rather than overwriting automated score history.

The first third-party integration is deliberately not part of the blocking product path. Ragas, DeepEval and other evaluator libraries remain future adapters after their dependencies and credential model are separately verified; until then, the product advertises only the implemented deterministic, external-judge and human evaluator paths.

### 7. Regression and first-error attribution are the product advantage

The platform does not compete with Phoenix or Langfuse as a comprehensive observability system. Its focused advantage is the decision layer:

```text
same Dataset Version
  -> align baseline/candidate Case executions
  -> classify recovered / unchanged / newly regressed
  -> align observable spans
  -> first divergent span
  -> category + evidence + critical-task impact
  -> YAML Gate + CI artifact
```

Attribution rules are conservative:

1. First differing tool name -> `tool_selection`.
2. Same tool, differing material arguments -> `tool_arguments`.
3. Tool error / timeout -> `tool_execution` / `timeout`.
4. Retrieval divergence -> `retrieval`.
5. Matching trajectory but assertion/judge failure -> `final_answer` or `format`.
6. Missing/un-alignable spans -> `indeterminate`; never invent a root cause.

This reuses TrajectIQ's most defensible idea while generalizing beyond its fixed multi-tool customer-service fixture.

### 8. Gate contract is declarative and fail-closed

Gate policy is versioned YAML stored or referenced with the Experiment comparison. Example:

```yaml
gates:
  - metric: task_success
    operator: gte
    threshold: 0.90
    severity: block
  - metric: tool_selection_accuracy
    operator: gte
    threshold: 0.95
    severity: block
  - metric: p95_latency_ms
    operator: lte
    threshold: 3000
    severity: warning
  - metric: critical_regressions
    operator: eq
    threshold: 0
    severity: block
```

The API and CLI return `PASS`, `WARNING`, `BLOCK`, `INCOMPLETE`, or `INDETERMINATE`; missing data cannot pass. GitHub Actions calls the comparison/gate endpoint or CLI and publishes a Markdown summary and JSON artifact. This is directly aligned with TrajectIQ's `release-gate.yaml` and CI design.

### 9. UI follows task objects, not prior resource scaffolding

Navigation and empty states become:

```text
Overview (Trace-first Project home)
Traces
Datasets
Experiments
Evaluators
Regression
Release Gates
Settings / Connections
```

The Overview has only real counts and recent results. Its primary empty-state action is “接入 Agent Trace”. The next contextual actions are “从 Trace 加入 Dataset” and “创建 Experiment”. Prompt forms and any hard-coded resource rows are removed. This is intentionally closer to Phoenix/Langfuse's project object navigation than to a generic CRUD dashboard.

## Implementation Delivery Sequence

Each milestone has an executable outcome. A later milestone does not replace the verification required by an earlier one.

1. **Data and contract foundation**: inventory the managed-prompt surface, back up and rehearse the schema migration, bootstrap the default Project, then introduce external Agent/Judge contracts and immutable release snapshots. Exit condition: existing local data migrates and no API contract accepts a Prompt Agent.
2. **Trusted Trace evidence**: ship authenticated canonical HTTP JSON ingestion, then OpenInference JSON and OTLP HTTP compatibility, redaction, isolation, idempotency and payload limits. Exit condition: a retry-safe multi-span Tool Trace is visible in its Project and no cross-Project or secret-leaking request succeeds.
3. **Quality assets**: implement online scores/annotations, Trace-to-Dataset conversion, CSV/API/manual Dataset creation and Dataset Versioning. Exit condition: a real failed Trace can become a versioned Case without losing its evidence link.
4. **Repeatable experiments**: run external Agent endpoints through isolated Celery jobs, persist per-Case evidence, and add deterministic RAG/Tool/Custom evaluators plus the external Judge adapter. Exit condition: one Case timeout does not stop the rest and no missing score passes an experiment or gate.
5. **Quality decision layer**: implement compatible baseline/candidate comparison, conservative first-error attribution, YAML Gate evaluation and CI artifacts. Exit condition: a seeded candidate Tool regression produces a linked `BLOCK` report.
6. **Workbench and removal**: replace placeholder UI with the Trace-first Chinese workflow, remove all managed-prompt paths and run backend/browser/end-to-end documentation checks. Exit condition: every primary action is functional and Docker Compose reproduces the full flow on one machine.

## Risks / Trade-offs

- [Users lack a deployed test endpoint] -> retain instrumented Trace-only observability and document a minimal test adapter; do not reintroduce Prompt Runner as a shortcut.
- [OTLP protocol implementation is broad] -> ship HTTP JSON first, contract-test OpenInference mapping, then add OTLP HTTP; state gRPC as deferred until truly implemented.
- [LLM Judge variability/cost or unavailable external endpoint] -> use deterministic gates by default, record judge provenance, make judge metrics advisory unless explicitly configured and report a judge failure as incomplete evidence.
- [First-error attribution can overclaim] -> require aligned observable evidence and return `indeterminate` on insufficient telemetry.
- [Removing Prompt functionality breaks current demo] -> replace demo coverage with RAG/Tool/Custom external test Agents and migration notices before deletion.
- [A single project UI hides isolation] -> make Project visible in settings and API paths while selecting the default automatically.

## Migration Plan

1. Add domain/API contracts for `AgentConnection`, `AgentRelease`, external LLM Judge connection, online score linkage, Experiment snapshots and diagnostic results while preserving existing Trace and Dataset records.
2. Bootstrap `default-project`, migrate existing `project-1` data into it, and create compatibility redirects or a documented migration for existing development API keys.
3. Introduce trace-first UI routes and real-data Overview before removing old navigation.
4. Rename/reuse HTTP Agent Adapter as External Agent Connection test adapter; add release identity and test endpoint validation.
5. Implement Trace-to-Dataset mapping, Experiment comparison, attribution, YAML Gate and CI artifacts with integration tests.
6. Remove `prompt` type, PromptConfig, PromptRunner, prompt-specific evaluators, model secret settings, prompt examples and associated UI/API routes in one breaking schema/API release.
7. Run migration verification against an existing local database, then deploy Compose with a database backup. Roll back before the destructive removal migration; after removal, restore the backup rather than attempting partial schema reversal.
