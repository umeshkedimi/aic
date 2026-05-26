"""Agent domain exceptions."""


class AgentError(Exception):
    """Base exception for agent domain errors."""

    pass


class AgentExecutionError(AgentError):
    """Raised when agent execution fails."""

    def __init__(self, agent_type: str, message: str, cause: Exception | None = None):
        self.agent_type = agent_type
        self.cause = cause
        super().__init__(f"Agent {agent_type} failed: {message}")


class AgentTimeoutError(AgentError):
    """Raised when agent execution times out."""

    pass
