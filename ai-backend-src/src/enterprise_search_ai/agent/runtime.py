"""Request-level invocation helpers for runtime harnesses."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from enterprise_search_ai.agent.contracts import RuntimeErrorCode
from enterprise_search_ai.agent.errors import AgentRuntimeError
from enterprise_search_ai.agent.factory import RuntimeHarness


def runtime_config(harness: RuntimeHarness) -> dict[str, dict[str, str]]:
    """Build LangGraph config carrying only stable runtime metadata."""

    return {
        "configurable": {
            "trace_id": harness.context.trace_id,
            "user_id": harness.context.user_id,
            "org_id": harness.context.org_id,
        }
    }


def invoke_runtime(harness: RuntimeHarness, messages: Sequence[object]) -> Any:
    """Invoke a sync-compatible runtime agent with typed config metadata."""

    if not callable(getattr(harness.agent, "invoke", None)):
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime agent does not provide invoke().",
            retryable=False,
            correlation_id=harness.context.trace_id,
        )

    return harness.agent.invoke(
        {"messages": list(messages)},
        config=runtime_config(harness),
    )


async def ainvoke_runtime(harness: RuntimeHarness, messages: Sequence[object]) -> Any:
    """Invoke an async-compatible runtime agent with typed config metadata."""

    if not callable(getattr(harness.agent, "ainvoke", None)):
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime agent does not provide ainvoke().",
            retryable=False,
            correlation_id=harness.context.trace_id,
        )

    return await harness.agent.ainvoke(
        {"messages": list(messages)},
        config=runtime_config(harness),
    )
