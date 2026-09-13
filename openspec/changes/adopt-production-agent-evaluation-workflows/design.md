## Context

See [proposal.md](proposal.md) for motivation and the four capability specs for observable contracts. The current repository already has FastAPI, PostgreSQL, Redis/Celery, a Next.js workbench, Project-scoped authentication, versioned Datasets, Trace/Span persistence, deterministic evaluators, comparison, attribution and Gate logic. Its central execution assumption is nevertheless server-orchestrated per-Case HTTP `/run`, and Compose includes deterministic Agent services plus a Seeder that guarantees a known regression.

The replacement design follows code and workflow verified in the current Phoenix and Langfuse projects:

| Reference behavior | Verified implementation | Decision here |
| --- | --- | --- |
| Phoenix client experiment | `run_experiment(dataset, task, evaluators)` executes the user callback in the client process and stores runs for comparison. The TS client creates an OpenInference task span around `task(example)` and uses bounded concurrency. | Build a small Python SDK whose primary unit is a user-owned task callback; the platform does not host business Agent code. |
| Phoenix/OpenInference tracing | Instrumented model/framework spans are exported through OTLP and linked to experiment task spans. | Use OpenTelemetry context plus OpenInference semantics as the canonical instrumentation route. |
| Langfuse SDK and OTel experiments | SDK runner provides concurrency, automatic tracing, error isolation and Dataset Run creation; non-Python/TS runtimes attach experiment attributes to OTel spans. | Support both a high-level Python runner and protocol-level OTel ingestion so language choice is not a product boundary. |
| Langfuse remote experiment | A Dataset can store a signed webhook configuration; the external service receives a trigger and performs the experiment in its own runtime. | Replace per-Case platform-owned Agent invocation with a signed, run-level remote trigger plus result ingestion. |
| Phoenix/Langfuse provider settings | Server-managed model credentials are used for Playground and LLM Judge, are not the business Agent's credentials, and are not returned to clients. | Add encrypted Project Provider Connections for Judge only; Agent provider keys remain in the user's runtime. |
| TrajectIQ | Consumes real or normalized trajectories and adds case regression, first-error attribution and release gates, but its reproducible default Agent is deterministic. | Retain the diagnostic and Gate layer; remove its mock/deliberate-regression pattern from the product runtime. |

## Goals / Non-Goals

**Goals:**

- Make a real user-owned task/Agent, not a platform sample service, the source of every production Experiment output.
- Support one coherent persistence model across SDK, OTel and remote runtimes.
- Make evidence completeness machine-verifiable and fail closed.
- Offer a usable real Judge by accepting encrypted provider credentials, while keeping business Agent credentials outside the platform.
- Preserve one-machine Docker Compose deployment and a technical story that can be explained and defended in an interview.

**Non-Goals:**

- No execution of uploaded arbitrary Python/JavaScript source on the platform server.
- No Prompt Agent or Prompt Playground; provider connections are initially only for evaluators.
- No claim of full provider coverage. The first provider protocol is non-streaming OpenAI-compatible Chat Completions with structured JSON output; more adapters are added only with real integration verification.
- No fake model in the runtime for offline convenience. Unit tests may replace network transports inside `tests/`, but cannot satisfy live acceptance.
- No guarantee that a candidate release regresses. The system reports observed data, including improvement or indeterminate attribution.

## Decisions

### 1. Product entry is Dataset/Trace, not Agent registration

The workbench presents two real starting points:

```text
Production observation
Real Agent request -> OTel/OpenInference Trace -> inspect/score -> add observation to Dataset

Pre-release experiment
Versioned Dataset -> choose SDK / OTel / Remote Runtime -> execute real Agent
                  -> Item + Trace + Score -> compare -> diagnose -> Gate
```

`Project` remains the tenant/security boundary and a default Project is selected automatically on a single-server installation. `AgentRelease` remains a required immutable identity (Git SHA, image digest, model/prompt/config hash or user release label), but an `AgentConnection` is no longer required for SDK/OTel runs.

**Alternative considered:** keep “create Agent Connection → platform calls `/run`” as the normal wizard. Rejected because it forces deployment shape on the user and differs from the primary Phoenix/Langfuse SDK flow.

### 2. Split experiment control plane from execution plane

The FastAPI service is the control plane: Dataset snapshots, Experiment definitions, credentials, Item state, results and queries. User code is the Agent execution plane.

An Experiment has:

```text
id, project_id, dataset_version_id, name
execution_mode = sdk_task | otel | remote_upload | remote_trigger
agent_release {identity, metadata, source_revision}
evaluator_version_ids
evidence_policy
execution_config {concurrency, repetitions, timeout, retry policy}
status and item counters
```

Each immutable attempt is keyed by `(experiment_id, case_id, repetition, attempt, external_run_id)`. It records state, output/error, Trace ID, usage, client/runtime metadata and timestamps. The server derives Experiment state from Item state; clients cannot submit an aggregate PASS directly.

**Alternative considered:** reuse current `AgentVersionRecord` as an always-connected HTTP endpoint. Rejected because SDK and OTel releases have identity but no endpoint. Migration separates release identity from optional trigger configuration.

### 3. Python SDK runs the actual task in the user's process

A new `agent-eval-sdk` package exposes a provider-neutral interface:

```python
from agent_eval import Client

client = Client()
dataset = client.datasets.get("support-regression", version="...")

result = client.experiments.run(
    dataset=dataset,
    task=run_my_real_agent,
    release={"git_sha": "...", "name": "candidate"},
    evaluators=[local_business_assertion],
    max_concurrency=5,
)
```

The SDK creates the Experiment, opens one OTel root Span per Item, invokes the sync or async callback, captures errors, uploads Item transitions/results, flushes telemetry and finalizes. It never imports a model provider or asks for the business Agent API Key. Framework-specific OpenInference instrumentors installed by the user attach their LLM/Retriever/Tool spans through normal OTel context.

The initial implementation uses Python 3.12, `httpx`, `pydantic`, `opentelemetry-sdk`, OTLP HTTP exporter and OpenInference semantic conventions. The SDK is independently packageable and versioned even while living in the monorepo.

**Alternative considered:** add LangGraph as a required Agent runtime. Rejected because the platform must evaluate LangGraph, LlamaIndex, custom loops and remote services equally.

### 4. OTel attributes are the language-neutral experiment protocol

In addition to standard OpenInference/OTel GenAI fields, the root task Span carries namespaced attributes:

```text
agent_eval.project.id
agent_eval.experiment.id
agent_eval.experiment.item.id
agent_eval.dataset.id
agent_eval.dataset.version.id
agent_eval.case.id
agent_eval.agent.release
agent_eval.execution.origin
agent_eval.repetition
```

The ingest service validates Project ownership, upserts by source Trace/Span ID, correlates the Item, and preserves unknown attributes in extensions. A Trace without experiment attributes remains a production observability Trace and can later be converted to a Dataset Case. A Dataset-backed Experiment requires an existing Experiment ID and Case membership; arbitrary Span attributes cannot smuggle results into another Project.

**Alternative considered:** copy Langfuse attribute names and internal APIs exactly. Rejected to avoid coupling to a private product protocol; only OTel/OpenInference standards are reused, with a documented project namespace for our own public contract.

### 5. Remote runtimes upload results; the platform does not reconstruct them

Remote Upload supports any language through REST. The flow is:

```text
create Experiment -> list immutable Case manifest
-> user executes real Agent anywhere
-> start/complete/fail Item attempt + upload/associate Trace
-> server evaluates/finalizes
```

Remote Trigger is an optional convenience stored on the Dataset. Starting it sends one signed webhook containing Experiment ID, Dataset Version, API base URL, non-secret run configuration and callback instructions. The receiver pulls Cases and uses the same Remote Upload or SDK contract. Signing uses HMAC-SHA256 with timestamp and delivery ID, rejects stale/replayed messages, and displays the generated signing secret only once.

The existing per-Case HTTP Agent runner is removed from the primary worker. A compatibility period MAY expose a migration endpoint, but production UI and documentation will not present it as the normal path.

**Alternative considered:** have Celery send every Case to `/run`. Rejected because it makes the platform own execution semantics, scaling and credentials while losing the flexibility of client runners.

### 6. Provider credentials are envelope-encrypted and Judge execution is asynchronous

Provider records contain non-secret routing metadata and encrypted credential material. Encryption uses AES-256-GCM from `cryptography`, with a random nonce, record-bound associated data and key identifier. The deployment supplies `AGENT_EVAL_CREDENTIAL_ENCRYPTION_KEY` as a base64 32-byte key through an untracked `.env` or container secret. API and Worker both require the key because API validates/saves connections and Worker executes Judge jobs. Production mode rejects the current development defaults.

Connection test invokes the configured real model with a minimal structured response request. Judge execution runs in Celery with timeout, retry/backoff for retryable responses, bounded concurrency and cost/usage capture. The first adapter uses the official `openai` client with configurable OpenAI-compatible `base_url`; it validates the JSON response against the Evaluator output schema. The key is decrypted only immediately before the call and never placed in Celery arguments.

`.env.example` contains placeholders such as:

```text
AGENT_EVAL_CREDENTIAL_ENCRYPTION_KEY=
LIVE_ACCEPTANCE_BASE_URL=https://api.openai.com/v1
LIVE_ACCEPTANCE_API_KEY=
LIVE_ACCEPTANCE_CHAT_MODEL=
LIVE_ACCEPTANCE_EMBEDDING_MODEL=
```

The `LIVE_ACCEPTANCE_*` variables are consumed only by an explicit live acceptance process. Normal business Agent keys stay in the user's own Agent environment. The UI-created Judge connection stores its submitted key encrypted in PostgreSQL.

**Alternative considered:** require users to deploy an external Judge HTTP service. Retained as an adapter but rejected as the only route because it creates unnecessary operational work and is unlike Phoenix/Langfuse self-hosted workflows.

### 7. Evidence policy is explicit and fail-closed

Each Experiment selects an evidence policy:

- `trace_required`: at least a linked root Trace.
- `llm_required`: LLM Span, model identity, usage presence marker and timing.
- `tool_trajectory_required`: all above plus Tool execution evidence for Cases marked tool-required.
- `rag_trajectory_required`: all above plus Retriever/document evidence for Cases marked retrieval-required.

Usage may be zero for a provider-cached request, but the Span must state that the provider returned usage; “missing” and numeric zero are distinct. Evidence validation is server-derived. `INCOMPLETE` propagates to comparison and Gate and can never silently become PASS.

**Alternative considered:** use `provider_request_id` alone as proof. Rejected because some real/local providers omit it and it can be fabricated more easily than a coherent instrumented trajectory. Evidence is strong provenance, not cryptographic proof that a third party is honest.

### 8. Real Tool and RAG acceptance is an explicit non-CI test lane

Normal CI validates contracts, state machines, security and failure handling using test-only transports and fixtures. A separate `tests/live` lane requires real credentials and never provides a mock flag. It runs:

```text
real provider preflight
-> real Tool Agent task with actual model-selected tool calls
-> real RAG task with actual embedding retrieval + answer generation
-> Dataset/Experiment/Trace/Score
-> baseline/candidate comparison of observed outputs
-> attribution/Gate
-> secret scan and redacted acceptance summary
```

The Tool tools are side-effect-safe but actually execute. The RAG path actually embeds, retrieves documents and calls the chat model. Both use non-sensitive controlled business data so results are reproducible without customer data. Test code cannot be started by production Compose and is not shown as a product capability.

Real acceptance remains unchecked until the user supplies a compatible endpoint/key/model names or another reachable Agent service. A failed or non-regressing candidate is reported honestly.

### 9. UI mirrors resource flow used by Phoenix and Langfuse

Navigation remains Project-scoped:

```text
Overview | Traces | Datasets & Experiments | Evaluators | Regression | Release Gates | Settings
```

The empty state starts with “接入真实 Agent” and offers SDK, OTel and Remote Runtime tabs. Dataset detail owns its Cases, Versions, Evaluators and Experiments. “Start Experiment” asks for execution mode rather than an Agent endpoint. SDK/OTel modes display generated commands and wait for the external runner; Remote Trigger starts the signed webhook. Provider setup lives under Settings and Judge Evaluator creation.

All counts, rows and charts come from APIs. There is no client-side seeded success state. UI tests use isolated test databases, not product demo endpoints.

### 10. Deliver one vertical slice before adding more integration modes

The architecture remains multi-entry, but implementation is deliberately incremental. The MVP officially supports one execution path:

```text
UI/API creates versioned Dataset and Project credential
-> user installs agent-eval-sdk
-> user-owned task calls a real Tool/RAG/Custom Agent with its own model key
-> SDK creates a named sdk_task Experiment and reads its immutable Case manifest
-> SDK executes Cases with bounded concurrency and uploads Item attempts
-> SDK/OpenTelemetry sends the real root Trace and available LLM/Tool child spans
-> server validates evidence and runs deterministic business evaluators
-> UI shows Case output, Trace, Score and aggregate metrics
-> a second Release runs the same Dataset Version
-> Comparison and Gate report the observed result
```

The MVP does not require a platform Provider credential: the business Agent key remains in the user's process, while objective evaluators score actual persisted outputs and trajectories. This produces a useful evaluation product without blocking the first usable release on credential encryption and a second model call. Managed LLM Judge remains required for V1.1, not silently omitted.

MVP completion requires all of the following:

- The SDK runs a caller-supplied real task; the repository provides no production Agent implementation or fallback answer.
- One real model-driven Tool Agent lane completes end to end and stores Item, Trace, usage, Tool arguments/results, Scores, comparison and Gate evidence.
- The workbench can create/import the Dataset and inspect every persisted object in that chain.
- Runtime Demo services and seeded outcomes are removed before the MVP is described as complete.
- Missing credentials, network, model or required evidence produces an honest failure or `INCOMPLETE` state.

Capabilities are added after the vertical slice in dependency order:

| Milestone | Added capability | Explicitly deferred |
| --- | --- | --- |
| MVP | Python SDK task execution, SDK-linked Trace, deterministic evaluation, minimal workbench, comparison/Gate, real Tool acceptance | Managed Judge, standalone OTel, Remote Trigger, full RAG acceptance |
| V1.1 | Real RAG acceptance, encrypted OpenAI-compatible Provider, asynchronous LLM Judge and Provider UI | Language-neutral remote orchestration |
| V1.2 | Standalone OTel/OpenInference, REST Remote Upload documentation/client example, signed run-level Remote Trigger | Advanced review and UI polish |
| V1.3 | Annotation/human review refinement, advanced evidence UX, full responsive and interaction coverage | Additional provider/framework adapters not backed by real verification |

Each implementation task has one observable exit condition. A milestone does not begin merely because code exists: its preceding feature must pass focused tests and, where required, a real run. This prevents partially connected modules from being counted as product functionality.

**Alternative considered:** build all control-plane, Provider, remote and UI surfaces in parallel. Rejected because it delays the first truthful end-to-end run and makes failures harder to attribute.

## Risks / Trade-offs

- [SDK implementation broadens the public API surface] -> Keep the first SDK small, version all payloads, publish compatibility checks and test against the server OpenAPI schema.
- [OTel delivery is eventually consistent] -> Use explicit SDK result acknowledgement plus trace correlation and a finalization grace period; display “waiting for Trace” rather than declaring failure immediately.
- [Users can falsely label synthetic spans as real] -> Validate coherent evidence and provenance, but document that absolute attestation requires trusted runtime/collector infrastructure outside this project's scope.
- [Real model tests cost money and can be nondeterministic] -> Show call estimates, use a small Dataset and bounded repetitions, never run the live lane implicitly, and report observed outcomes instead of forcing a regression.
- [Encryption key loss makes provider credentials unreadable] -> Document backup/rotation, store key IDs, support controlled re-encryption, and fail closed after key mismatch.
- [Removing example services makes first use less instant] -> Provide exact SDK/OTel integration snippets and diagnostics while keeping the project empty and honest.
- [Current dirty worktree contains completed but uncommitted prior work] -> Apply this migration incrementally, avoid resetting user changes, and make deletion targets explicit before removal.

## Migration Plan

1. Preserve the verified database backup, then finish and re-verify endpoint-independent Release, Experiment definition and attempt schema migrations against SQLite rehearsal and the real PostgreSQL instance.
2. Complete the authenticated Experiment Case-manifest and Item lifecycle API, followed by idempotent result ingestion and server-derived terminal state. Do not begin SDK orchestration until this contract passes independently.
3. Finish the installable Python client, then add Dataset/version lookup and Experiment creation. Verify each operation against the live FastAPI contract before adding task execution.
4. Add synchronous task execution first; after it passes, add asynchronous bounded concurrency, timeout, retry, cancellation and error isolation one behavior at a time.
5. Add the SDK root task Span, Experiment attributes, result/Trace acknowledgement and evidence validation for the SDK path. Preserve nested user instrumentation when it is present.
6. Connect existing deterministic evaluators, aggregation, comparison and Gate to the new Item model and verify missing evidence cannot pass.
7. Expose only the MVP resource flow in the workbench: onboarding, Dataset, Release, Experiment, Item, Trace, Score, comparison and Gate.
8. Once the real SDK path is green, delete production Demo Agents, Seeder, intentional regression controls and the worker-owned per-Case `/run` path; regenerate public contracts and documentation.
9. Use untracked user-supplied credentials to run the real Tool Agent acceptance lane, scan secrets and record a redacted acceptance summary. This is the MVP release gate.
10. Add V1.1 RAG and managed Provider/Judge functionality, including AES-GCM encryption and asynchronous real Judge execution; then run the real RAG acceptance lane.
11. Add V1.2 standalone OTel, language-neutral Remote Upload guidance and signed Remote Trigger in that order, reusing the already verified Item contract.
12. Complete V1.3 workbench, review and full interaction coverage, then run the complete Compose, security and documentation reconciliation.

Before destructive Demo cleanup, rollback uses the existing database backup and prior image. After Provider credentials exist, rollback must restore the matching encryption key together with the database. No rollback may re-enable mock or seeded production behavior as a claimed product mode.
