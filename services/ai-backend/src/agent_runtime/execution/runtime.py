"""Request-level invocation helpers for runtime harnesses."""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator, Sequence
from contextlib import contextmanager
from time import perf_counter
from typing import Any

from langgraph.types import Command

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.observability.logging import (
    RuntimeLogger,
    RuntimeLogLevel,
)
from agent_runtime.observability.redactor import MetadataRedactor
from agent_runtime.observability.tracing import (
    RuntimeTracer,
    TraceContext,
    TraceNames,
    TraceRunTypes,
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
        metadata.update(MetadataRedactor.redact(context.trace_metadata))

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


_NON_RETRYABLE_ERRORS = (TypeError, ValueError, AttributeError)


class _TracedRuntimeCall:
    """Shared logging and error-handling for all runtime invocation functions.

    Encapsulates the start-event, method-validation, try/except error wrapping,
    and success-event that every ``invoke``/``astream`` variant repeats.
    """

    __slots__ = (
        "_agent",
        "_context",
        "_event_prefix",
        "_log_runtime_errors",
        "_logger",
        "_safe_message",
        "_started_at",
        "_trace_id",
    )

    def __init__(
        self,
        harness: RuntimeHarness,
        *,
        event_prefix: str,
        safe_message: str,
        logger: RuntimeLogger,
        start_metadata: dict[str, object] | None = None,
        log_runtime_errors: bool = True,
    ) -> None:
        self._agent = harness.agent
        self._context = harness.context
        self._event_prefix = event_prefix
        self._safe_message = safe_message
        self._logger = logger
        self._log_runtime_errors = log_runtime_errors
        self._started_at = perf_counter()
        self._trace_id = harness.context.trace_id

        start_kwargs: dict[str, object] = {}
        if start_metadata:
            start_kwargs["metadata"] = start_metadata
        logger.event(
            context=self._context,
            event=f"{event_prefix}.started",
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="started",
            **start_kwargs,
        )

    @property
    def logger(self) -> RuntimeLogger:
        return self._logger

    def has_method(self, method_name: str) -> bool:
        return callable(getattr(self._agent, method_name, None))

    def require_method(self, method_name: str) -> None:
        """Raise ``AgentRuntimeError`` if the agent lacks *method_name*."""
        if self.has_method(method_name):
            return
        error = AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Runtime agent does not provide {method_name}().",
            retryable=False,
            correlation_id=self._trace_id,
        )
        self._log_failure(
            error_code=error.code,
            retryable=error.retryable,
            safe_message=error.safe_message,
        )
        raise error

    @contextmanager
    def guard(self) -> Generator[None, None, None]:
        """Context manager that logs failures/success around the yielded body."""
        try:
            yield
        except AgentRuntimeError as exc:
            if self._log_runtime_errors:
                self._log_failure(
                    error_code=exc.code,
                    retryable=exc.retryable,
                    safe_message=exc.safe_message,
                )
            raise
        except Exception as exc:
            retryable = not isinstance(exc, _NON_RETRYABLE_ERRORS)
            self._log_failure(
                error_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=retryable,
                safe_message=self._safe_message,
                metadata=RuntimeLogger.exception_metadata(exc),
            )
            raise AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                self._safe_message,
                retryable=retryable,
                correlation_id=self._trace_id,
            ) from exc
        self._log_success()

    def _log_failure(self, **kwargs: object) -> None:
        self._logger.event(
            context=self._context,
            event=f"{self._event_prefix}.failed",
            level=RuntimeLogLevel.ERROR,
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="failed",
            duration_ms=_elapsed_ms(self._started_at),
            **kwargs,
        )

    def _log_success(self) -> None:
        self._logger.event(
            context=self._context,
            event=f"{self._event_prefix}.succeeded",
            subsystem="runtime",
            operation=TraceNames.RUNTIME_INVOKE,
            status="succeeded",
            duration_ms=_elapsed_ms(self._started_at),
        )


# ---------------------------------------------------------------------------
# Public invocation helpers (signatures unchanged)
# ---------------------------------------------------------------------------


@RuntimeTracer.traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def ainvoke_runtime(
    harness: RuntimeHarness,
    messages: Sequence[object],
    *,
    logger: RuntimeLogger | None = None,
) -> Any:
    """Invoke an async-compatible runtime agent with typed config metadata."""

    call = _TracedRuntimeCall(
        harness,
        event_prefix="runtime.invoke",
        safe_message="Runtime invocation failed safely.",
        logger=logger or RuntimeLogger(),
        start_metadata={"message_count": len(messages)},
    )
    call.require_method("ainvoke")
    with call.guard():
        return await harness.agent.ainvoke(
            {"messages": list(messages)},
            config=runtime_config(harness),
        )


@RuntimeTracer.traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def ainvoke_runtime_resume(
    harness: RuntimeHarness,
    resume: object,
    *,
    logger: RuntimeLogger | None = None,
) -> Any:
    """Resume a checkpointed runtime graph with a LangGraph HITL decision."""

    call = _TracedRuntimeCall(
        harness,
        event_prefix="runtime.resume",
        safe_message="Runtime resume failed safely.",
        logger=logger or RuntimeLogger(),
        log_runtime_errors=False,
    )
    call.require_method("ainvoke")
    with call.guard():
        return await harness.agent.ainvoke(
            Command(resume=resume),
            config=runtime_config(harness),
        )


@RuntimeTracer.traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def astream_runtime(
    harness: RuntimeHarness,
    messages: Sequence[object],
    *,
    logger: RuntimeLogger | None = None,
) -> AsyncIterator[object]:
    """Stream runtime chunks from the graph while preserving typed config metadata."""

    call = _TracedRuntimeCall(
        harness,
        event_prefix="runtime.stream",
        safe_message="Runtime streaming failed safely.",
        logger=logger or RuntimeLogger(),
        start_metadata={"message_count": len(messages)},
    )
    if not call.has_method("astream"):
        yield await ainvoke_runtime(harness, messages, logger=call.logger)
        return

    with call.guard():
        async for chunk in harness.agent.astream(
            {"messages": list(messages)},
            config=runtime_config(harness),
            **RuntimeStreamOptions.rich(),
        ):
            yield chunk


@RuntimeTracer.traced(name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN)
async def astream_runtime_resume(
    harness: RuntimeHarness,
    resume: object,
    *,
    logger: RuntimeLogger | None = None,
) -> AsyncIterator[object]:
    """Stream a checkpointed runtime graph after a LangGraph HITL decision."""

    call = _TracedRuntimeCall(
        harness,
        event_prefix="runtime.resume_stream",
        safe_message="Runtime resume streaming failed safely.",
        logger=logger or RuntimeLogger(),
        log_runtime_errors=False,
    )
    if not call.has_method("astream"):
        yield await ainvoke_runtime_resume(harness, resume, logger=call.logger)
        return

    with call.guard():
        async for chunk in harness.agent.astream(
            Command(resume=resume),
            config=runtime_config(harness),
            **RuntimeStreamOptions.rich(),
        ):
            yield chunk


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))
