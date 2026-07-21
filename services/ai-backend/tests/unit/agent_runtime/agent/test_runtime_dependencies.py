from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import RuntimeDependencies, SkillSourceConfig
from agent_runtime.settings import RuntimeSettings
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
    WebSearchToolRegistry,
)
from tests.unit.agent_runtime.agent.helpers import MissingToolRegistryMethod
from tests.unit.fakes import (
    FakeMcpRegistry,
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)


def test_runtime_dependencies_accept_fake_ports(
    fake_dependencies: RuntimeDependencies,
) -> None:
    assert isinstance(fake_dependencies.tool_registry, FakeToolRegistry)
    assert isinstance(fake_dependencies.mcp_registry, FakeMcpRegistry)
    assert fake_dependencies.skill_source_config.roots == ("skills",)


def test_runtime_dependencies_reject_missing_required_protocol_method() -> None:
    with pytest.raises(ValidationError):
        RuntimeDependencies(
            tool_registry=MissingToolRegistryMethod(),
            mcp_registry=FakeMcpRegistry(),
            skill_source_config=SkillSourceConfig(),
            memory_backend_factory=FakeMemoryBackendFactory(),
            subagent_catalog=FakeSubagentCatalog(),
        )


def test_default_runtime_dependencies_include_web_search_tool(
    runtime_context_admin,
) -> None:
    # The context fixture keeps the default web_search_enabled=True.
    assert runtime_context_admin.web_search_enabled is True
    tools = WebSearchToolRegistry().list_available_tools(runtime_context_admin)

    assert len(tools) == 1
    assert getattr(tools[0], "name", "") == "web_search"


def test_web_search_registry_omits_tool_when_disabled(runtime_context_admin) -> None:
    disabled = runtime_context_admin.model_copy(update={"web_search_enabled": False})

    tools = WebSearchToolRegistry().list_available_tools(disabled)

    assert tools == ()


def test_web_search_registry_defaults_on_for_bare_context() -> None:
    # A bare object / None (older callers, the capability-mode probe) has no
    # web_search_enabled attribute and must keep the historic always-on default.
    assert len(WebSearchToolRegistry().list_available_tools(None)) == 1
    assert len(WebSearchToolRegistry().list_available_tools(object())) == 1


def test_production_capability_guard_holds_when_run_disables_web_search(
    runtime_context_admin,
) -> None:
    # Disabling web search for a single run must NOT trip the production
    # "no capability sources configured" guard — the deployment still composes
    # web search as a source (the guard probes with a default context).
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "production",
            "OPENAI_API_KEY": "sk-test",
        }
    )
    disabled = runtime_context_admin.model_copy(update={"web_search_enabled": False})

    dependencies = DefaultRuntimeDependenciesFactory(settings)(disabled)

    # No exception raised; the run's own tool list is empty (web search off).
    assert dependencies.tool_registry.list_available_tools(disabled) == ()


def test_default_runtime_dependencies_allow_production_with_default_web_search_tool(
    runtime_context_admin,
) -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "production",
            "OPENAI_API_KEY": "sk-test",
        }
    )

    dependencies = DefaultRuntimeDependenciesFactory(settings)(runtime_context_admin)
    tools = dependencies.tool_registry.list_available_tools(runtime_context_admin)

    assert getattr(tools[0], "name", "") == "web_search"


def test_default_runtime_dependencies_keep_web_search_when_empty_capabilities_allowed(
    runtime_context_admin,
) -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "production",
            "RUNTIME_ALLOW_EMPTY_CAPABILITIES": "true",
            "OPENAI_API_KEY": "sk-test",
        }
    )

    dependencies = DefaultRuntimeDependenciesFactory(settings)(runtime_context_admin)

    tools = dependencies.tool_registry.list_available_tools(runtime_context_admin)
    assert getattr(tools[0], "name", "") == "web_search"
