## 1. Audit and migration preparation

- [x] 1.1 Inventory every Prompt Agent, PromptRunner, PromptConfig, model-provider secret, prompt evaluator, API route, UI route, fixture, test and document reference; record the replacement or removal decision in a migration checklist and verify `rg -n "PromptRunner|PromptConfig|prompt" apps packages docs` has an reviewed inventory.
- [x] 1.2 Add a repeatable database backup and local migration rehearsal procedure, including a fixture representing existing `project-1` data, and verify the fixture can be restored before any destructive migration.
- [x] 1.3 Bootstrap a `default-project` for single-project self-hosted installations, migrate existing `project-1` records and credential references without data loss, and verify a fresh install and an upgraded local database both accept an authenticated first trace.

## 2. Domain and external-agent contracts

- [x] 2.1 Replace product-level managed Agent/Prompt concepts with project-scoped `AgentConnection`, immutable `AgentRelease` and user-managed external LLM `EvaluatorConnection` contracts; verify generated API schemas reject the removed `prompt` type and do not expose model-provider settings.
- [x] 2.2 Define and document the external Agent test-endpoint and external LLM Judge request/response contracts, including normalized result, optional usage/tool calls, trace or trace correlation id and score provenance; verify contract tests cover success, timeout, malformed payload and authentication failure.
- [x] 2.3 Implement connection validation and release identity capture (for example Git SHA, image tag or user label) without exposing stored secrets; verify a valid connection can be tested and API/UI responses redact credential values.
- [x] 2.4 Freeze Dataset Version, AgentRelease, EvaluatorVersion set, optional external Judge connection identity, execution options and baseline reference when an Experiment is created; verify an Experiment still shows its original definition after a connection, dataset or evaluator is later edited.

## 3. Trace-first ingestion and evidence

- [x] 3.1 Harden the authenticated HTTP JSON trace ingestion path around one canonical Trace/Span model with parent-child relationships, status, inputs, outputs, errors, usage, cost, attributes and `extensions`; verify API integration tests persist and retrieve a multi-span tool trajectory.
- [x] 3.2 Add an OpenInference-shaped JSON adapter that maps supported semantic fields into the canonical model while preserving unknown fields in `extensions`; verify fixture-based mapping tests cover agent, LLM, tool, tool-result, retrieval, guardrail and evaluator spans.
- [x] 3.3 Add a supported OTLP HTTP ingestion route or documented collector-forwarding adapter, with contract tests against representative OTLP payloads; explicitly defer OTLP gRPC until the HTTP interoperability suite is stable and document that scope limit.
- [x] 3.4 Apply project credential resolution, cross-project authorization checks and configurable sensitive-field redaction before Trace persistence or presentation; verify a cross-project request is rejected and a known secret pattern never appears in stored or returned trace data.
- [x] 3.5 Add project/source-scoped idempotency, request-size, span-count and nesting-depth limits to ingestion; verify duplicate retries return the original Trace and oversized, conflicting or deeply nested requests leave no partial records.

## 4. Online quality loop and versioned datasets

- [x] 4.1 Implement auditable deterministic, LLM-judge and human Scores/Annotations linked to a Trace or Span, including evaluator provenance and immutable score history; verify manual annotations do not overwrite automated scores.
- [x] 4.2 Implement Trace/Observation-to-Dataset conversion with explicit input/expected-output/metadata field mapping and source trace/span references; verify a selected production observation becomes a reusable Dataset Case.
- [x] 4.3 Complete Dataset Case creation through UI/API and CSV import with preview, row-level validation and RAG/Tool extension fields; verify a CSV with valid and invalid rows reports errors without creating partial unreviewed data.
- [x] 4.4 Version Dataset changes and bind historical Experiments to their original case snapshot; verify editing or archiving a Case creates a new Dataset Version while previous Experiment results remain unchanged.

## 5. Offline experiment execution and evaluators

- [x] 5.1 Run each external-agent Dataset Case as an isolated asynchronous job through the existing worker/Redis queue, record execution state, normalized result, Trace, latency, usage, cost and errors, and verify one timed-out Case does not cancel successful Cases.
- [x] 5.2 Enforce bounded concurrency, retries and terminal-state aggregation for Experiment execution; verify a mixed success/failure run reaches a completed-with-errors state and never treats missing scores as passing results.
- [x] 5.3 Implement the initial deterministic evaluator set: RAG reference/context, citation or output-format checks; Tool name/order/argument/state checks; Custom JSON-schema or exact/state assertions; and shared latency, token, cost and error metrics. Verify each evaluator with deterministic unit fixtures.
- [x] 5.4 Implement the user-managed external LLM Judge adapter without model-provider configuration or stored provider API keys; verify a valid Judge response is persisted as normalized Score evidence and unsupported third-party libraries are not advertised as available.
- [x] 5.5 Record external LLM-judge rubric, model/release, prompt-template version, raw structured response and normalized score separately from deterministic gate metrics; verify judge failures are visible as incomplete evidence and cannot silently pass a blocking gate.

## 6. Regression diagnostics and release decisions

- [x] 6.1 Compare baseline and candidate only when they share a Dataset Version and compatible evaluator identities, align Case executions, and calculate aggregate deltas, newly regressed cases, recovered cases, missing evidence and critical-task impact; verify incompatible comparisons are rejected with a reason.
- [x] 6.2 Implement conservative first-error attribution over aligned traces with `tool_selection`, `tool_arguments`, `tool_execution`, `retrieval`, `final_answer`, `format`, `timeout`, `cost_or_latency` and `indeterminate` outcomes; verify fixtures locate the first divergent tool span and return `indeterminate` when telemetry cannot be aligned.
- [x] 6.3 Parse and version declarative YAML Gate policies for metric thresholds, comparison directions, severity and critical-task rules; verify invalid YAML and unknown metrics fail validation before a release decision is made.
- [x] 6.4 Evaluate Gate outcomes as `PASS`, `WARNING`, `BLOCK`, `INCOMPLETE` or `INDETERMINATE` with fail-closed missing-data behavior; verify a critical regression blocks and incomplete evaluator data cannot yield PASS.
- [x] 6.5 Produce CI-consumable Markdown and JSON comparison artifacts and add a GitHub Actions example that fails on `BLOCK`; verify the artifact links failed Cases to their baseline/candidate evidence and first-error diagnosis.

## 7. Trace-first workbench UI

- [x] 7.1 Replace the existing landing/dashboard scaffolding with a Chinese Trace-first Project overview driven exclusively by live API data for Trace, Dataset, Experiment, failure and Gate counts; verify an empty Project leads with trace ingestion and an active Project shows recent quality work without hard-coded demo results.
- [x] 7.2 Rework primary navigation to `Traces`, `Datasets`, `Experiments`, `Evaluators`, `Regression`, `Release Gates` and `Settings / Connections`; verify every navigation action has a working route, loading state, empty state and error state.
- [x] 7.3 Build Trace inspection for span hierarchy, inputs/outputs, scores, annotations and redaction indicators, then add the UI flow to map a selected Observation into a Dataset Case; verify the created Case links back to its trace evidence.
- [x] 7.4 Build Dataset version, CSV import, Agent Connection/Release and immutable Experiment creation views around the external-agent contract; verify a user can configure and start an Experiment without entering a model-provider API key or Prompt configuration.
- [x] 7.5 Build Experiment result, baseline/candidate comparison, first-error diagnosis and Gate detail views with links between failed cases, traces and CI artifacts; verify a `BLOCK` result opens its diagnosis from the Gate page.

## 8. Remove managed-prompt functionality

- [x] 8.1 Remove Prompt Agent, PromptRunner, PromptConfig, platform-managed LLM execution, model-provider secret settings and prompt-specific API/client contracts in one documented breaking migration; verify API contract and type checks expose no supported prompt execution path.
- [x] 8.2 Remove prompt-specific worker jobs, UI forms/routes, sample data, fixtures and evaluators, replacing the demonstration flow with RAG, Tool and Custom external Agent examples; verify no primary user journey refers to Prompt Agent.
- [x] 8.3 Update environment templates and secret handling so only platform infrastructure and external-agent connection references are configured; verify tracked files contain no real credentials with a repository secret scan.

## 9. End-to-end verification and documentation

- [x] 9.1 Add unit and integration coverage for ingestion adapters, project isolation/redaction, Dataset versions, asynchronous execution, evaluator evidence, regression comparison, attribution and Gate outcomes; verify the relevant backend and worker test suites pass.
- [x] 9.2 Add browser-level tests for every interactive navigation item and command in the Trace-first UI; verify no button is inert and the critical pages render correctly at desktop and mobile viewports.
- [x] 9.3 Add a reproducible end-to-end fixture flow: external Tool/RAG Agent trace -> Dataset Case -> baseline Experiment -> candidate regression -> first-error attribution -> `BLOCK` Gate -> CI Markdown/JSON artifact; verify it runs from Docker Compose on one machine.
- [x] 9.4 Write Chinese beginner-oriented documentation covering product boundaries, HTTP JSON and stated OTLP/OpenInference scope, external-agent integration, two quality loops, Dataset/Experiment reproducibility, evaluation evidence, regression/Gate workflow and one-machine deployment; verify all commands and links work from a clean checkout.
- [x] 9.5 Add an architecture/data-flow diagram and a source-attributed comparison with Phoenix, Langfuse and TrajectIQ that distinguishes copied workflow logic, adapted design and deliberately excluded scope; verify documentation makes no unsupported compatibility or feature claims.
