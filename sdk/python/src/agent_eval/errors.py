"""SDK exceptions that never expose credentials or upstream response bodies."""


class AgentEvalError(Exception):
    """Base SDK error safe to print in CI logs."""


class AgentEvalConnectionError(AgentEvalError):
    pass


class AgentEvalApiError(AgentEvalError):
    def __init__(self, status_code: int, method: str, path: str) -> None:
        self.status_code = status_code
        self.method = method
        self.path = path
        super().__init__(f"Agent Eval API returned HTTP {status_code} for {method} {path}")


class IncompatibleServiceError(AgentEvalError):
    pass
