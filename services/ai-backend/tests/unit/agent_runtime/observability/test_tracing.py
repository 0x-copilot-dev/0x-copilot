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
        name=TraceNames.RUNTIME_INVOKE,
        run_type=TraceRunTypes.TOOL,
        tags=("tools",),
        metadata={"operation": TraceNames.RUNTIME_INVOKE},
    )
    def operation() -> str:
        return "loaded"

    assert operation() == "loaded"
    assert calls == [
        {
            "name": TraceNames.RUNTIME_INVOKE,
            "run_type": TraceRunTypes.TOOL,
            "tags": ["agent_runtime", "tools"],
            "metadata": {"operation": TraceNames.RUNTIME_INVOKE},
        }
    ]


def test_identity_hash_is_stable_and_hides_raw_value() -> None:
    raw = "user_123"
    hashed = TraceContext.identity_hash(raw)
    assert hashed != raw
    assert len(hashed) == 16
    assert TraceContext.identity_hash(raw) == hashed


def test_traced_classmethod_returns_working_decorator() -> None:
    decorator = RuntimeTracer.traced(
        name=TraceNames.RUNTIME_INVOKE, run_type=TraceRunTypes.CHAIN
    )

    def original() -> str:
        return "result"

    decorated = decorator(original)
    assert decorated() == "result"
