from __future__ import annotations

import pytest

from agent_runtime.capabilities.skills.sources import SkillSourceConfig
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeDependencies,
)
from tests.unit.fakes import (
    FakeMcpRegistry,
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)


@pytest.fixture
def model_config() -> ModelConfig:
    return ModelConfig(
        provider="Fake",
        model_name="fake-enterprise-model",
        max_input_tokens=128_000,
        timeout_seconds=30,
        temperature=0,
        supports_streaming=True,
    )


@pytest.fixture
def runtime_context_admin(model_config: ModelConfig) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"Admin"},
        permission_scopes={"Search:Read", "Docs:Read"},
        connector_scopes={"Google-Drive": {"Docs:Read"}},
        model_profile=model_config,
        trace_id="trace_123",
        feature_flags={"dynamic_tool_loading"},
    )


@pytest.fixture
def fake_dependencies() -> RuntimeDependencies:
    return RuntimeDependencies(
        tool_registry=FakeToolRegistry(),
        mcp_registry=FakeMcpRegistry(),
        skill_source_config=SkillSourceConfig(roots=("skills",)),
        memory_backend_factory=FakeMemoryBackendFactory(),
        subagent_catalog=FakeSubagentCatalog(),
    )
