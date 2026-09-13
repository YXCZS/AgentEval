"""Public evaluation engine building blocks."""

from .adapters import (
    ADAPTERS,
    AgentEvalsAdapter,
    DeepEvalAdapter,
    PromptfooAdapter,
    RagasAdapter,
    ThirdPartyAdapter,
    ThirdPartyAdapterError,
    evaluate_adapter,
)
from .aggregation import aggregate_run_scores
from .base import (
    EvaluationContext,
    Evaluator,
    EvaluatorConfigurationError,
    EvaluatorOutcome,
)
from .deterministic import DETERMINISTIC_EVALUATORS, evaluate_deterministic
from .external_protocols import (
    ExternalJudgeConfig,
    ExternalJudgeResult,
    ExternalProtocolError,
    call_external_judge,
)
from .future_adapters import (
    FUTURE_ADAPTER_CAPABILITIES,
    FUTURE_ADAPTERS,
    FutureAdapter,
    FutureAdapterError,
    get_future_adapter,
    list_future_adapter_capabilities,
)
from .judge import (
    DEFAULT_RUBRICS,
    evaluate_llm_judge,
)
from .managed_provider import (
    ManagedJudgeConfig,
    ManagedJudgeError,
    ManagedJudgeRetryableError,
    evaluate_managed_judge,
)
from .scoring import (
    attempt_execution_record,
    build_evaluation_context,
    evaluate_and_persist_scores,
    is_managed_judge_snapshot,
    replace_pending_managed_score,
)

__all__ = [
    "ADAPTERS",
    "AgentEvalsAdapter",
    "aggregate_run_scores",
    "DETERMINISTIC_EVALUATORS",
    "EvaluationContext",
    "DeepEvalAdapter",
    "Evaluator",
    "EvaluatorConfigurationError",
    "EvaluatorOutcome",
    "evaluate_deterministic",
    "ExternalJudgeConfig",
    "ExternalJudgeResult",
    "ExternalProtocolError",
    "call_external_judge",
    "FUTURE_ADAPTER_CAPABILITIES",
    "FUTURE_ADAPTERS",
    "FutureAdapter",
    "FutureAdapterError",
    "get_future_adapter",
    "list_future_adapter_capabilities",
    "DEFAULT_RUBRICS",
    "PromptfooAdapter",
    "RagasAdapter",
    "ThirdPartyAdapter",
    "ThirdPartyAdapterError",
    "evaluate_adapter",
    "evaluate_and_persist_scores",
    "evaluate_llm_judge",
    "evaluate_managed_judge",
    "ManagedJudgeConfig",
    "ManagedJudgeError",
    "ManagedJudgeRetryableError",
    "build_evaluation_context",
    "attempt_execution_record",
    "is_managed_judge_snapshot",
    "replace_pending_managed_score",
]
