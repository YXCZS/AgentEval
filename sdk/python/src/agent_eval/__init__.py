"""Public package namespace for the Agent Eval Python SDK."""

from .client import Client
from .errors import (
    AgentEvalApiError,
    AgentEvalConnectionError,
    AgentEvalError,
    IncompatibleServiceError,
)
from .models import AgentRelease, Dataset, DatasetCase, DatasetSelection, Experiment
from .runner import (
    CancellationToken,
    ExperimentResult,
    ExperimentRunner,
    ProgressEvent,
    TaskResult,
)

__version__ = "0.1.0"

__all__ = [
    "AgentEvalApiError",
    "AgentEvalConnectionError",
    "AgentEvalError",
    "AgentRelease",
    "CancellationToken",
    "Client",
    "Dataset",
    "DatasetCase",
    "DatasetSelection",
    "Experiment",
    "ExperimentResult",
    "ExperimentRunner",
    "IncompatibleServiceError",
    "ProgressEvent",
    "TaskResult",
    "__version__",
]
