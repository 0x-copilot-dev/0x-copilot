from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.observability.tracing import (
    RuntimeTracer,
    TraceContext,
    TraceNames,
    TraceOptions,
    TraceRunTypes,
)


def make_context(model_config: ModelConfig) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        model_profile=model_config,
        request_id="request_123",
        run_id="run_123",
        trace_id="trace_123",
        parent_trace_id="trace_parent",
    )


def test_runtime_tracer_is_noop_when_disabled() -> None:
    tracer = RuntimeTracer(TraceOptions(enabled=False))

    def operation() -> str:
        return "ok"

    decorated = tracer.traceable(
        name=TraceNames.RUNTIME_INVOKE,
        run_type=TraceRunTypes.CHAIN,
    )(operation)

    assert decorated is operation
    assert decorated() == "ok"


def test_runtime_tracer_uses_langsmith_when_enabled(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_traceable(**kwargs):
        calls.append(kwargs)

        def decorator(func):
            def wrapper(*args, **inner_kwargs):
                return func(*args, **inner_kwargs)

            return wrapper

        return decorator

    monkeypatch.setitem(
        __import__("sys").modules,
        "langsmith",
        SimpleNamespace(traceable=fake_traceable),
    )
    tracer = RuntimeTracer(TraceOptions(enabled=True, tags=("agent_runtime",)))

    @tracer.traceable(
        name=TraceNames.TOOLS_LOAD_SPEC,
        run_type=TraceRunTypes.TOOL,
        tags=("tools",),
        metadata={"operation": TraceNames.TOOLS_LOAD_SPEC},
    )
    def operation() -> str:
        return "loaded"

    assert operation() == "loaded"
    assert calls == [
        {
            "name": TraceNames.TOOLS_LOAD_SPEC,
            "run_type": TraceRunTypes.TOOL,
            "tags": ["agent_runtime", "tools"],
            "metadata": {"operation": TraceNames.TOOLS_LOAD_SPEC},
        }
    ]


def test_langsmith_extra_contains_product_ids_without_raw_identity(
    model_config: ModelConfig,
) -> None:
    context = make_context(model_config)

    extra = TraceContext.langsmith_extra_for(context, operation=TraceNames.RUNTIME_INVOKE)
    metadata = extra["metadata"]

    assert metadata["request_id"] == "request_123"  # type: ignore[index]
    assert metadata["run_id"] == "run_123"  # type: ignore[index]
    assert metadata["trace_id"] == "trace_123"  # type: ignore[index]
    assert metadata["parent_trace_id"] == "trace_parent"  # type: ignore[index]
    assert metadata["user_id_hash"] != context.user_id  # type: ignore[index]
    assert metadata["org_id_hash"] != context.org_id  # type: ignore[index]
    assert context.user_id not in str(extra)
    assert context.org_id not in str(extra)
