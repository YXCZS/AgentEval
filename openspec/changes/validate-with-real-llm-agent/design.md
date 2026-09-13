## Context

See [proposal.md](proposal.md) for the motivation and [real-llm-agent-validation spec](specs/real-llm-agent-validation/spec.md) for observable requirements. The current platform already has the external `/run` contract, immutable Agent Releases, versioned Datasets, Celery execution, canonical Trace storage, deterministic evaluators, comparison, attribution and Gate APIs. The gap is the tested system at the far side of `/run`: all repository examples currently use fixed Python decisions.

The development machine currently has no configured model-provider credential and no Ollama installation. It has approximately 40 GB RAM and an RTX 3060 Laptop GPU, so a small local tool-capable model is viable, but the implementation must not assume every deployment has a GPU.

## Goals / Non-Goals

**Goals:**

- Exercise the existing platform against an actual model-controlled tool loop, not merely an HTTP-shaped fixture.
- Keep model credentials and provider-specific configuration outside the evaluation platform.
- Make a real run distinguishable from a deterministic fixture using stored, reviewable evidence.
- Reproduce a realistic Agent configuration regression while preserving the model's actual output, including nondeterminism.
- Keep the whole demonstration runnable on one machine and explainable during an interview.

**Non-Goals:**

- The platform will not regain Prompt Agent, model-provider settings, Playground or hosted model execution.
- The real example is not a production order system and performs no real financial or fulfillment side effects.
- Supporting every provider dialect, multimodal model, streaming response or Agent framework is not required.
- A particular regression result will not be faked or hard-coded when the model behaves differently.
- Existing deterministic fixtures will not be removed; they remain fast platform regression tests under an honest label.

## Decisions

### 1. Build an actual tool-calling loop in an external service

The real Agent is a separate FastAPI service. For each `/run` request it constructs system/user messages and JSON tool schemas, calls an OpenAI-compatible chat-completions endpoint with tools enabled, executes only tool calls returned by the model, appends tool results, and calls the model again until a final answer or a small maximum-turn limit.

This is the minimum complete Agent loop:

```text
Dataset Case
  -> LLM decides tool call
  -> validate tool name and JSON arguments
  -> execute side-effect-free order tool
  -> append TOOL_RESULT to messages
  -> LLM interprets result and decides next action/final answer
  -> normalized output + complete Trace
```

Directly implementing this small loop is preferred over hiding it behind LangGraph for the first real case: it makes tool selection, argument validation, iteration limits and trace construction easy to explain and audit. LangGraph can later be added as a second external integration without changing the platform contract.

**Alternative considered:** replace the deterministic Agent's `if` branches with an LLM call but still synthesize tool calls. Rejected because it would not prove model-controlled tool selection.

### 2. Use a provider-neutral OpenAI-compatible boundary

The Agent reads only its own environment:

```text
REAL_AGENT_BASE_URL
REAL_AGENT_API_KEY          # optional for local providers
REAL_AGENT_MODEL
REAL_AGENT_TIMEOUT_SECONDS
REAL_AGENT_MAX_TURNS
```

This supports a user-provided remote provider and local Ollama's OpenAI-compatible endpoint without teaching the platform about either. The model credential is mounted only into the real Agent service. It is never copied into Agent Connection metadata or sent back in Trace attributes.

For this machine, the preferred first live verification is a small tool-capable Ollama model because no remote credentials are configured. The Compose profile must keep model download explicit rather than silently downloading several gigabytes during a normal platform startup. A remote provider remains a supported alternative through the same variables.

**Alternative considered:** add OpenAI/DeepSeek settings to the platform. Rejected because it breaks the external-Agent boundary and reverses the completed removal of platform-managed model secrets.

### 3. Separate platform fixture tests from live-model validation

Existing deterministic services remain under the normal Compose path because they make CI repeatable. The real Agent and live end-to-end runner live behind a `real-agent` profile and an explicit command. Naming and metadata use:

```text
execution_origin = deterministic_fixture | real_llm
```

Unit and integration tests may mock the provider only to test protocol parsing and failures, but those tests can never satisfy the live-validation task. The live runner performs provider preflight and exits non-zero if it cannot collect authentic upstream metadata.

**Alternative considered:** run a paid model in every GitHub Actions build. Rejected because secrets, cost, rate limits and nondeterminism make that unsuitable as the only CI signal.

### 4. Capture model evidence at the source and normalize it through existing Trace APIs

The Agent creates a root AGENT span and child LLM, TOOL and TOOL_RESULT spans while it executes. LLM spans preserve safe evidence:

```text
provider family (not credential)
model
upstream request id when present
finish reason
prompt/completion/total tokens as returned
turn index
start/end timestamps
sanitized tool-call structure
```

The raw authorization header, base URL credentials and hidden reasoning are excluded. The response returns the canonical trace payload when the `/run` contract accepts it; otherwise the Agent posts the same trace through authenticated ingestion using a platform project credential distinct from the model credential. Implementation must first test the existing contract and make the smallest compatible extension if nested spans are currently lost.

A live-validation checker requires at least one LLM span, the configured model identity, upstream usage fields, a model-selected tool call for tool-required cases and `execution_origin=real_llm`. Provider request ID is stored when available but is not the sole proof because local providers may omit it.

**Alternative considered:** have the demo script manufacture an OpenInference trace after receiving only final output. Rejected because that repeats the current false-evidence problem.

### 5. Compare a good and degraded configuration, not two fabricated outputs

Both releases use the same provider/model, temperature, Dataset Version and Evaluator Versions. Release snapshots record a hash and human-readable identity for:

- system instruction version;
- tool schema version;
- model and generation options.

The baseline exposes correct policy-oriented tool descriptions. The candidate contains a deliberate but realistic schema/instruction regression, such as removing the mandatory lookup prerequisite or weakening the cancellation tool description. The actual model response is never rewritten. The live runner can use bounded repetitions and report observed pass rates to reduce single-sample randomness.

If the candidate does not regress during a particular run, the command reports the observed comparison rather than claiming the platform failed or replacing the result. A specific `BLOCK` becomes a documented expected demonstration only after a model/configuration pair has been empirically verified and pinned; `INDETERMINATE` remains valid when traces cannot be aligned.

**Alternative considered:** force a regression by returning a hard-coded wrong tool call. Rejected because that would again test Python fixtures rather than an Agent configuration change.

### 6. Use deterministic business assertions to grade nondeterministic Agent behavior

The real Agent may be nondeterministic; the expected business rules are not. The Dataset covers at least five paths using fictional orders. Deterministic evaluators check state, selected tools, arguments, ordering, errors, latency and usage. An external LLM Judge is optional and is not required to prove the Agent itself used an LLM.

This separation makes the result defensible: a real model produces behavior, while deterministic policy checks decide whether the behavior is acceptable.

### 7. Treat the live run as an evidence-producing manual integration test

The runner performs:

```text
provider preflight
  -> randomized model challenge and usage check
  -> real Agent health/connection test
  -> create/reuse versioned Dataset
  -> register baseline/candidate Releases
  -> execute both Experiments through Redis/Celery
  -> verify every required Case has real LLM evidence
  -> compare and attribute actual traces
  -> evaluate YAML Gate
  -> write redacted summary/comparison artifacts
```

The summary records provider family, model, timestamp, Dataset Version, release identities, run IDs, evidence checks and Gate result. It never records credentials. Generated artifacts remain Git-ignored; the repository tracks only a redacted example schema and documentation unless the user explicitly chooses to publish a reviewed report.

## Risks / Trade-offs

- [No model is currently available] -> Implementation can prepare the service, but live tasks remain unchecked until the user authorizes a remote key or local Ollama installation/model download.
- [Small local models may not follow tool schemas reliably] -> Use strict JSON schemas, low temperature, bounded repetitions and preserve failures as evidence rather than adding a rule fallback.
- [Provider dialects differ] -> Support the common non-streaming OpenAI-compatible tool-call subset first and fail explicitly on unsupported responses.
- [A deliberate candidate change may not always regress] -> Compare observed repetitions, pin a validated demo model/config, and never rewrite real output.
- [Model calls add cost and latency] -> Keep the representative Dataset small, show an estimated call count before running, and require an explicit profile/command.
- [Trace may leak user or provider data] -> Use fictional records, redact before persistence, omit headers/raw secrets and run the repository secret scan after live validation.
- [Local model artifacts consume disk and memory] -> Make download/start explicit and document model removal separately; do not include model weights in project artifacts.

## Migration Plan

1. Add the optional Agent and model configuration without changing the default Compose startup.
2. Add the real Dataset, Release identities and live runner alongside existing deterministic fixtures.
3. Run protocol/unit tests with controlled provider responses; label them as non-live tests.
4. Configure one real provider, execute the full live run and retain only redacted evidence.
5. Update UI/docs to show the last verified real run separately from fixture demonstrations.
6. Rollback by disabling the `real-agent` profile and removing its local model/container; no existing platform data contract is removed.
