## 1. Honest boundaries and contracts

- [ ] 1.1 Audit the current external `/run` result-to-Trace path and add only the contract fields needed to preserve Agent/LLM/Tool/Tool Result parent-child spans, model identity, finish reason, upstream request id, usage, turn index and `execution_origin`; verify contract and integration tests retrieve the same evidence without credentials.
- [ ] 1.2 Mark every existing order, RAG and Custom example and its UI/documentation references as `deterministic_fixture`, and verify repository search finds no claim that those fixtures prove a real LLM Agent was tested.
- [ ] 1.3 Define a machine-readable live-evidence validator that rejects fixture/mock identities, missing LLM spans, absent model identity, missing provider usage and missing model-selected tools on tool-required Cases; verify positive and negative fixtures cannot be confused.

## 2. Real external LLM Tool Agent

- [ ] 2.1 Add an isolated real-Agent configuration module for OpenAI-compatible base URL, optional Agent-owned API key, model, timeout, temperature and maximum turns; verify startup fails safely on incomplete configuration and no response or log exposes the key.
- [ ] 2.2 Implement the non-streaming model-controlled tool loop—model request, tool-call parsing, strict argument validation, tool execution, tool-result message and follow-up model request—with no deterministic/mock fallback; verify tests cover one-tool, multi-turn, malformed-call, provider-auth, timeout and maximum-turn outcomes.
- [ ] 2.3 Implement side-effect-free fictional order tools for lookup, cancellation eligibility and refund eligibility, keeping policy enforcement inside tools rather than trusting the model; verify tests cover processing, shipped, delivered and unknown orders without real customer data.
- [ ] 2.4 Emit Trace evidence directly during execution with an AGENT root and LLM/TOOL/TOOL_RESULT children, preserving actual provider metadata and timing while excluding authorization headers and hidden reasoning; verify a recorded tool trajectory is produced from provider-returned tool calls rather than synthesized after completion.

## 3. Real regression assets and deployment

- [ ] 3.1 Add one versioned real-use Dataset with at least five Cases covering order query, processing-order cancellation, shipped-order refusal, delivered-order refund and unknown order; verify deterministic state/tool/argument/order evaluators can grade every Case.
- [ ] 3.2 Define baseline and degraded candidate Agent configurations using the same provider/model and generation options, record prompt/tool-schema hashes in immutable Release identities, and verify the only intended comparison variable is an auditable instruction or Tool Schema change.
- [ ] 3.3 Add an optional `real-agent` Docker Compose profile and safe `.env.example` placeholders for remote OpenAI-compatible or local Ollama use, ensuring only the real-Agent container receives the provider credential; verify default Compose starts without a model and rendered Compose configuration does not inject the credential into Web, API, Worker or PostgreSQL.
- [ ] 3.4 Add explicit local-model setup and teardown instructions appropriate for a one-machine deployment, including model size/call-count warnings; verify model weights, credentials and generated responses remain outside Git.

## 4. Live end-to-end validation

- [ ] 4.1 Implement a real-provider preflight using a randomized challenge and actual usage metadata, with a non-zero exit on missing model, invalid credentials, unreachable endpoint or fixture-like response; verify no mock server or precomputed answer can be selected by the live command.
- [ ] 4.2 Implement the live runner from provider preflight through Agent Connection, immutable baseline/candidate Releases, Dataset Version, Redis/Celery Experiments, real Trace evidence validation, comparison, first-error attribution and YAML Gate; verify every required Case links to stored LLM and Tool spans.
- [ ] 4.3 Generate redacted `summary.json`, `comparison.json` and `comparison.md` artifacts containing model, Dataset Version, Release identities, run IDs, evidence-check results, observed regressions and Gate status; verify artifacts contain no credential, authorization header or unreviewed sensitive input.
- [ ] 4.4 Configure one actual remote model or install/download one actual local Ollama model, then run both Releases through the full Compose workflow until at least one real configuration-caused candidate regression is observed and linked to a non-PASS Gate; verify the result comes from upstream model calls with actual usage and preserve the observed output without rewriting it. This task MUST remain unchecked while only mocks or deterministic fixtures have run.

## 5. Product presentation and final verification

- [ ] 5.1 Show `真实 LLM` versus `确定性 Fixture` identity and model/usage evidence in the relevant Trace, Experiment and Demo documentation views; verify a user cannot mistake fixture results for live-model validation.
- [ ] 5.2 Add backend and browser regression tests for evidence display, error states and links from a real comparison/Gate to both LLM traces; verify unit/integration suites and desktop/mobile Playwright flows pass while clearly labeling provider mocks as test-only.
- [ ] 5.3 Update the Chinese beginner guide, architecture, security boundaries and interview narrative with the exact real Agent data flow, reproduction command, provider ownership and nondeterminism limits; verify every command/link works and documentation makes no production-use claim beyond the captured evidence.
- [ ] 5.4 Run the complete backend, worker, frontend, Compose, live-evidence and secret-scan verification suite; verify deterministic tests remain green, the live run is independently reproducible with an authorized provider, and tracked files contain no real key or generated private artifact.
