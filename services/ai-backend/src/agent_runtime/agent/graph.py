"""LangGraph export surface for local development and deployment."""

from __future__ import annotations

from typing import Any

from agent_runtime.agent.contracts import RuntimeErrorCode
from agent_runtime.agent.errors import AgentRuntimeError


class UnconfiguredRuntimeGraph:
    """Placeholder graph until the app layer provides runtime dependencies."""

    def invoke(self, *_: Any, **__: Any) -> Any:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime graph requires application-provided context and dependencies.",
            retryable=False,
        )

    async def ainvoke(self, *_: Any, **__: Any) -> Any:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime graph requires application-provided context and dependencies.",
            retryable=False,
        )


def create_graph() -> UnconfiguredRuntimeGraph:
    """Return a stable export target for `langgraph.json`."""

    return UnconfiguredRuntimeGraph()


graph = create_graph()
