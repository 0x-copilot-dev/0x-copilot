from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from enterprise_search_ai.agent.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from enterprise_search_ai.agent.errors import AgentRuntimeError
from enterprise_search_ai.agent.factory import RuntimeHarness, create_agent_runtime
from tests.unit.fakes import (
    FakeMcpRegistry,
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)


@dataclass
class CapturingAgentBuilder:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return {"agent": "fake"}


def test_factory_propagates_permissions_to_runtime_ports(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()

    harness = create_agent_runtime(
        context=runtime_context_admin,
        dependencies=fake_dependencies,
        agent_builder=builder,
    )

    assert isinstance(harness, RuntimeHarness)
    assert harness.tools == ("doc_search",)
    assert harness.mcp_servers == ("drive_mcp",)
    assert harness.subagents == ("researcher",)

    tool_registry = fake_dependencies.tool_registry
    mcp_registry = fake_dependencies.mcp_registry
    subagent_catalog = fake_dependencies.subagent_catalog
    memory_factory = fake_dependencies.memory_backend_factory

    assert isinstance(tool_registry, FakeToolRegistry)
    assert isinstance(mcp_registry, FakeMcpRegistry)
    assert isinstance(subagent_catalog, FakeSubagentCatalog)
    assert isinstance(memory_factory, FakeMemoryBackendFactory)
    assert tool_registry.seen_contexts == [runtime_context_admin]
    assert mcp_registry.seen_contexts == [runtime_context_admin]
    assert subagent_catalog.seen_contexts == [runtime_context_admin]
    assert memory_factory.seen_contexts == [runtime_context_admin]

    call = builder.calls[0]
    assert call["runtime_context"] is runtime_context_admin
    assert call["model_config"] is runtime_context_admin.model_profile
    assert call["tools"] == ("doc_search",)


def test_factory_rejects_invalid_dependency_dict(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    with pytest.raises(AgentRuntimeError) as exc_info:
        create_agent_runtime(
            context=runtime_context_admin,
            dependencies={
                "tool_registry": object(),
                "mcp_registry": object(),
                "skill_source_config": {},
                "memory_backend_factory": object(),
                "subagent_catalog": object(),
                "stream_normalizer": object(),
            },
            agent_builder=CapturingAgentBuilder(),
        )

    assert exc_info.value.code == RuntimeErrorCode.DEPENDENCY_ERROR
    assert exc_info.value.safe_message == "Runtime dependencies are invalid."


def test_factory_wraps_builder_failure_without_leaking_secret(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    def failing_builder(**_: object) -> object:
        raise RuntimeError("provider token=super-secret")

    with pytest.raises(AgentRuntimeError) as exc_info:
        create_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=failing_builder,
        )

    assert exc_info.value.code == RuntimeErrorCode.RUNTIME_FACTORY_ERROR
    assert "super-secret" not in exc_info.value.safe_message
    assert exc_info.value.correlation_id == runtime_context_admin.trace_id
