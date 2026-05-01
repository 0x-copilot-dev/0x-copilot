"""Request-level invocation helpers for runtime harnesses."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from time import perf_counter
from typing import Any

from langgraph.types import Command

from agent_runtime.execution.contracts import RuntimeErrorCode, RuntimeRunHandle
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.observability.logging import (
    LogValueNormalizer,
    RuntimeLogger,
    RuntimeLogLevel,
)
from agent_runtime.observability.tracing import (
    TraceContext,
    TraceNames,
    TraceRunTypes,
    traced,
)


class RuntimeStreamModes:
    """LangGraph stream mode sets used by the runtime."""

    RICH = ["messages", "updates", "custom", "values"]


class RuntimeStreamOptions:
    """Deep Agents/LangGraph stream options used by the runtime."""

    @classmethod
    def rich(cls) -> dict[str, object]:
        return {
            "stream_mode": RuntimeStreamModes.RICH,
            "subgraphs": True,
            "version": "v2",
        }


def runtime_run_handle(harness: RuntimeHarness) -> RuntimeRunHandle:
    """Return the product-owned run handle before graph execution completes."""

    return RuntimeRunHandle.from_context(harness.context)


def runtime_config(harness: RuntimeHarness) -> dict[str, object]:
    """Build LangGraph config carrying only stable runtime metadata."""

    context = harness.context
    run_id = context.run_id
    metadata = {
        "request_id": context.request_id,
        "run_id": run_id,
        "trace_id": context.trace_id,
        "user_id_hash": TraceContext.identity_hash(context.user_id),
        "org_id_hash": TraceContext.identity_hash(context.org_id),
    }
    if context.parent_trace_id is not None:
        metadata["parent_trace_id"] = context.parent_trace_id
    metadata.update(LogValueNormalizer.redact_metadata(context.trace_metadata))

    return {
        "configurable": {
            "thread_id": run_id,
            "request_id": context.request_id,
            "run_id": run_id,
            "trace_id": context.trace_id,
        },
        "max_concurrency": context.max_parallel_tasks,
        "metadata": metadata,
        "tags": ["agent_runtime", f"run:{run_id}"],
    }


@traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
def invoke_runtime(
    harness: RuntimeHarness,
    messages: Sequence[object],
    *,
    logger: RuntimeLogger | None = None,
) -> Any:
    """Invoke a sync-compatible runtime agent with typed config metadata."""

    runtime_logger = logger or RuntimeLogger()
    started_at = perf_counter()
    runtime_logger.event(
        context=harness.context,
        event="runtime.invoke.started",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="started",
        metadata={"message_count": len(messages)},
    )

    if not callable(getattr(harness.agent, "invoke", None)):
        error = AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime agent does not provide invoke().",
            retryable=False,
            correlation_id=harness.context.trace_id,
        )
        runtime_logger.event(
            context=harness.context,
            event="runtime.invoke.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=error.code,
            retryable=error.retryable,
            safe_message=error.safe_message,
        )
        raise error

    try:
        result = harness.agent.invoke(
            {"messages": list(messages)},
            config=runtime_config(harness),
        )
    except AgentRuntimeError as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.invoke.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=exc.code,
            retryable=exc.retryable,
            safe_message=exc.safe_message,
        )
        raise
    except Exception as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.invoke.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
            safe_message="Runtime invocation failed safely.",
            metadata={"exception_type": type(exc).__name__},
        )
        raise AgentRuntimeError(
            RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            "Runtime invocation failed safely.",
            retryable=True,
            correlation_id=harness.context.trace_id,
        ) from exc

    runtime_logger.event(
        context=harness.context,
        event="runtime.invoke.succeeded",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="succeeded",
        duration_ms=_elapsed_ms(started_at),
    )
    return result


@traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def ainvoke_runtime(
    harness: RuntimeHarness,
    messages: Sequence[object],
    *,
    logger: RuntimeLogger | None = None,
) -> Any:
    """Invoke an async-compatible runtime agent with typed config metadata."""

    runtime_logger = logger or RuntimeLogger()
    started_at = perf_counter()
    runtime_logger.event(
        context=harness.context,
        event="runtime.invoke.started",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="started",
        metadata={"message_count": len(messages)},
    )

    if not callable(getattr(harness.agent, "ainvoke", None)):
        error = AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime agent does not provide ainvoke().",
            retryable=False,
            correlation_id=harness.context.trace_id,
        )
        runtime_logger.event(
            context=harness.context,
            event="runtime.invoke.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=error.code,
            retryable=error.retryable,
            safe_message=error.safe_message,
        )
        raise error

    try:
        result = await harness.agent.ainvoke(
            {"messages": list(messages)},
            config=runtime_config(harness),
        )
    except AgentRuntimeError as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.invoke.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=exc.code,
            retryable=exc.retryable,
            safe_message=exc.safe_message,
        )
        raise
    except Exception as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.invoke.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
            safe_message="Runtime invocation failed safely.",
            metadata={"exception_type": type(exc).__name__},
        )
        raise AgentRuntimeError(
            RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            "Runtime invocation failed safely.",
            retryable=True,
            correlation_id=harness.context.trace_id,
        ) from exc

    runtime_logger.event(
        context=harness.context,
        event="runtime.invoke.succeeded",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="succeeded",
        duration_ms=_elapsed_ms(started_at),
    )
    return result


@traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def ainvoke_runtime_resume(
    harness: RuntimeHarness,
    resume: object,
    *,
    logger: RuntimeLogger | None = None,
) -> Any:
    """Resume a checkpointed runtime graph with a LangGraph HITL decision."""

    runtime_logger = logger or RuntimeLogger()
    started_at = perf_counter()
    runtime_logger.event(
        context=harness.context,
        event="runtime.resume.started",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="started",
    )

    if not callable(getattr(harness.agent, "ainvoke", None)):
        error = AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime agent does not provide ainvoke().",
            retryable=False,
            correlation_id=harness.context.trace_id,
        )
        runtime_logger.event(
            context=harness.context,
            event="runtime.resume.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=error.code,
            retryable=error.retryable,
            safe_message=error.safe_message,
        )
        raise error

    try:
        result = await harness.agent.ainvoke(
            Command(resume=resume),
            config=runtime_config(harness),
        )
    except AgentRuntimeError:
        raise
    except Exception as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.resume.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
            safe_message="Runtime resume failed safely.",
            metadata={"exception_type": type(exc).__name__},
        )
        raise AgentRuntimeError(
            RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            "Runtime resume failed safely.",
            retryable=True,
            correlation_id=harness.context.trace_id,
        ) from exc

    runtime_logger.event(
        context=harness.context,
        event="runtime.resume.succeeded",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="succeeded",
        duration_ms=_elapsed_ms(started_at),
    )
    return result


@traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def astream_runtime(
    harness: RuntimeHarness,
    messages: Sequence[object],
    *,
    logger: RuntimeLogger | None = None,
) -> AsyncIterator[object]:
    """Stream runtime chunks from the graph while preserving typed config metadata."""

    runtime_logger = logger or RuntimeLogger()
    started_at = perf_counter()
    runtime_logger.event(
        context=harness.context,
        event="runtime.stream.started",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="started",
        metadata={"message_count": len(messages)},
    )

    if not callable(getattr(harness.agent, "astream", None)):
        yield await ainvoke_runtime(harness, messages, logger=runtime_logger)
        return

    try:
        async for chunk in harness.agent.astream(
            {"messages": list(messages)},
            config=runtime_config(harness),
            **RuntimeStreamOptions.rich(),
        ):
            yield chunk
    except AgentRuntimeError as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.stream.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=exc.code,
            retryable=exc.retryable,
            safe_message=exc.safe_message,
        )
        raise
    except Exception as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.stream.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
            safe_message="Runtime streaming failed safely.",
            metadata={"exception_type": type(exc).__name__},
        )
        raise AgentRuntimeError(
            RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            "Runtime streaming failed safely.",
            retryable=True,
            correlation_id=harness.context.trace_id,
        ) from exc

    runtime_logger.event(
        context=harness.context,
        event="runtime.stream.succeeded",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="succeeded",
        duration_ms=_elapsed_ms(started_at),
    )


@traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def astream_runtime_resume(
    harness: RuntimeHarness,
    resume: object,
    *,
    logger: RuntimeLogger | None = None,
) -> AsyncIterator[object]:
    """Stream a checkpointed runtime graph after a LangGraph HITL decision."""

    runtime_logger = logger or RuntimeLogger()
    started_at = perf_counter()
    runtime_logger.event(
        context=harness.context,
        event="runtime.resume_stream.started",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="started",
    )

    if not callable(getattr(harness.agent, "astream", None)):
        yield await ainvoke_runtime_resume(harness, resume, logger=runtime_logger)
        return

    try:
        async for chunk in harness.agent.astream(
            Command(resume=resume),
            config=runtime_config(harness),
            **RuntimeStreamOptions.rich(),
        ):
            yield chunk
    except AgentRuntimeError:
        raise
    except Exception as exc:
        runtime_logger.event(
            context=harness.context,
            event="runtime.resume_stream.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
            safe_message="Runtime resume streaming failed safely.",
            metadata={"exception_type": type(exc).__name__},
        )
        raise AgentRuntimeError(
            RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            "Runtime resume streaming failed safely.",
            retryable=True,
            correlation_id=harness.context.trace_id,
        ) from exc

    runtime_logger.event(
        context=harness.context,
        event="runtime.resume_stream.succeeded",
        subsystem="runtime",
        operation=TraceNames.RUNTIME_INVOKE,
        status="succeeded",
        duration_ms=_elapsed_ms(started_at),
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))
