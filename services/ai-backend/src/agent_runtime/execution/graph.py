"""LangGraph export surface for local development.

The production execution path is `runtime_worker`, which has durable queue,
persistence, and event sequencing ports. This module stays as the stable
`langgraph.json` export target and as a configured local/test utility.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    RuntimeRunHandle,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import (
    AgentBuilder,
    acreate_agent_runtime,
    create_agent_runtime,
)
from agent_runtime.execution.runtime import ainvoke_runtime, invoke_runtime
from agent_runtime.observability.logging import RuntimeLogger


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


RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]


@dataclass(frozen=True)
class ConfiguredRuntimeGraph:
    """App-configured graph entrypoint that owns product run creation."""

    dependencies_factory: RuntimeDependenciesFactory
    agent_builder: AgentBuilder | None = None
    logger: RuntimeLogger | None = None

    def start(self, context: AgentRuntimeContext | dict[str, Any]) -> RuntimeRunHandle:
        """Validate request context and return the product-owned run handle."""

        runtime_context = self._coerce_context(context)
        return RuntimeRunHandle.from_context(runtime_context)

    def invoke(
        self,
        input_data: dict[str, Any],
        *_: Any,
        **__: Any,
    ) -> Any:
        """Invoke the runtime graph using app-provided dependencies."""

        runtime_context = self._context_from_input(input_data)
        dependencies = self.dependencies_factory(runtime_context)
        harness = create_agent_runtime(
            context=runtime_context,
            dependencies=dependencies,
            agent_builder=self.agent_builder,
        )
        return invoke_runtime(
            harness,
            self._messages_from_input(input_data),
            logger=self.logger,
        )

    async def ainvoke(
        self,
        input_data: dict[str, Any],
        *_: Any,
        **__: Any,
    ) -> Any:
        """Invoke the runtime graph asynchronously using app-provided dependencies."""

        runtime_context = self._context_from_input(input_data)
        dependencies = self.dependencies_factory(runtime_context)
        harness = await acreate_agent_runtime(
            context=runtime_context,
            dependencies=dependencies,
            agent_builder=self.agent_builder,
        )
        return await ainvoke_runtime(
            harness,
            self._messages_from_input(input_data),
            logger=self.logger,
        )

    @classmethod
    def _context_from_input(cls, input_data: dict[str, Any]) -> AgentRuntimeContext:
        raw_context = input_data.get("context")
        if raw_context is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime graph input requires context.",
                retryable=False,
            )
        return cls._coerce_context(raw_context)

    @classmethod
    def _coerce_context(
        cls, context: AgentRuntimeContext | dict[str, Any]
    ) -> AgentRuntimeContext:
        if isinstance(context, AgentRuntimeContext):
            return context
        try:
            return AgentRuntimeContext.model_validate(context)
        except Exception as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime graph context is invalid.",
                retryable=False,
            ) from exc

    @classmethod
    def _messages_from_input(cls, input_data: dict[str, Any]) -> Sequence[object]:
        messages = input_data.get("messages", ())
        if isinstance(messages, str) or not isinstance(messages, Sequence):
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime graph input messages are invalid.",
                retryable=False,
            )
        return tuple(messages)


def create_graph() -> UnconfiguredRuntimeGraph:
    """Return a stable export target for `langgraph.json`."""

    return UnconfiguredRuntimeGraph()


graph = create_graph()
