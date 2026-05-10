from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeRunHandle,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.graph import ConfiguredRuntimeGraph
from agent_runtime.execution.runtime import (
    ainvoke_runtime,
    runtime_config,
)
from agent_runtime.observability.tracing import TraceNames


@dataclass
class CapturingInvokeAgent:
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False
    result: object = field(
        default_factory=lambda: {
            "answer": "LLM says alice@example.com promised the roadmap update."
        }
    )

    async def ainvoke(
        self, input_data: dict[str, object], *, config: dict[str, object]
    ) -> object:
        self.calls.append({"input": input_data, "config": config})
        if self.fail:
            raise RuntimeError("provider token=super-secret")
        return self.result


def make_harness(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
    *,
    agent: object,
) -> RuntimeHarness:
    return RuntimeHarness(
        agent=agent,
        context=runtime_context_admin.model_copy(
            update={
                "request_id": "request_123",
                "run_id": "run_123",
                "trace_id": "trace_123",
                "parent_trace_id": "trace_parent",
                "trace_metadata": {
                    "query": "What did alice@example.com promise?",
                    "safe_count": 3,
                },
            }
        ),
        dependencies=fake_dependencies,
        tools=("doc_search",),
        mcp_servers=("drive_mcp",),
        subagents=("researcher",),
        memory_backend="memory",
        skill_directories=(),
    )


def runtime_payloads(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    return [
        record.runtime
        for record in records
        if hasattr(record, "runtime") and isinstance(record.runtime, dict)
    ]


def test_runtime_config_uses_product_run_id_without_raw_identity(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    harness = make_harness(
        runtime_context_admin,
        fake_dependencies,
        agent=CapturingInvokeAgent(),
    )

    config = runtime_config(harness)
    handle = RuntimeRunHandle.from_context(harness.context)

    assert handle.run_id == "run_123"
    assert config["configurable"]["thread_id"] == "run_123"  # type: ignore[index]
    assert config["configurable"]["request_id"] == "request_123"  # type: ignore[index]
    assert "user_id" not in config["configurable"]  # type: ignore[operator]
    assert "org_id" not in config["configurable"]  # type: ignore[operator]
    assert config["metadata"]["user_id_hash"] != harness.context.user_id  # type: ignore[index]
    assert config["metadata"]["org_id_hash"] != harness.context.org_id  # type: ignore[index]
    assert config["metadata"]["query"] == "What did alice@example.com promise?"  # type: ignore[index]
    assert config["metadata"]["safe_count"] == 3  # type: ignore[index]
    assert config["max_concurrency"] == harness.context.max_parallel_tasks
    assert config["tags"] == ["agent_runtime", "run:run_123"]


async def test_invoke_runtime_logs_success_and_passes_langgraph_config(
    caplog: pytest.LogCaptureFixture,
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    caplog.set_level(logging.INFO, logger="agent_runtime")
    agent = CapturingInvokeAgent()
    harness = make_harness(runtime_context_admin, fake_dependencies, agent=agent)

    result = await ainvoke_runtime(
        harness, messages=({"role": "user", "content": "secret"},)
    )

    payloads = runtime_payloads(caplog.records)
    assert result == {
        "answer": "LLM says alice@example.com promised the roadmap update."
    }
    assert agent.calls[0]["config"]["configurable"]["thread_id"] == "run_123"
    assert [payload["event"] for payload in payloads] == [
        "runtime.invoke.started",
        "runtime.invoke.succeeded",
    ]
    assert all(payload["request_id"] == "request_123" for payload in payloads)
    assert all(payload["run_id"] == "run_123" for payload in payloads)
    assert all(payload["trace_id"] == "trace_123" for payload in payloads)
    assert payloads[0]["operation"] == TraceNames.RUNTIME_INVOKE
    assert "LLM says" not in str(payloads)
    assert "alice@example.com" not in str(payloads)
    assert "secret" not in str(payloads)


async def test_invoke_runtime_logs_safe_error_without_raw_exception(
    caplog: pytest.LogCaptureFixture,
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    caplog.set_level(logging.INFO, logger="agent_runtime")
    harness = make_harness(
        runtime_context_admin,
        fake_dependencies,
        agent=CapturingInvokeAgent(fail=True),
    )

    with pytest.raises(AgentRuntimeError):
        await ainvoke_runtime(
            harness, messages=({"role": "user", "content": "secret"},)
        )

    payloads = runtime_payloads(caplog.records)
    error_payload = payloads[-1]
    assert error_payload["event"] == "runtime.invoke.failed"
    assert error_payload["level"] == "error"
    assert error_payload["safe_message"] == "Runtime invocation failed safely."
    assert error_payload["metadata"]["exception_type"] == "RuntimeError"  # type: ignore[index]
    assert "super-secret" not in str(payloads)
    assert "secret" not in str(payloads)


async def test_configured_runtime_graph_returns_handle_and_invokes_with_dependencies(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    agent = CapturingInvokeAgent()
    seen_contexts: list[AgentRuntimeContext] = []

    def dependencies_factory(context: AgentRuntimeContext) -> RuntimeDependencies:
        seen_contexts.append(context)
        return fake_dependencies

    graph = ConfiguredRuntimeGraph(
        dependencies_factory=dependencies_factory,
        agent_builder=lambda _: agent,
    )
    context = runtime_context_admin.model_copy(
        update={"request_id": "request_123", "run_id": "run_123"}
    )

    handle = graph.start(context)
    result = await graph.ainvoke({"context": context, "messages": [{"role": "user"}]})

    assert handle.run_id == "run_123"
    assert result == {
        "answer": "LLM says alice@example.com promised the roadmap update."
    }
    assert seen_contexts == [context]
    assert agent.calls[0]["config"]["configurable"]["thread_id"] == "run_123"
