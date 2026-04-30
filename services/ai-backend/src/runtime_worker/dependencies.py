"""Default dependency factories for local runtime worker execution."""

from __future__ import annotations

from collections.abc import Sequence

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.capabilities.mcp.backend_provider import BackendMcpProvider
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.skills.sources import SkillSourceConfig
from agent_runtime.capabilities.skills.virtual import BackendSkillProvider, VirtualSkillRegistry
from agent_runtime.context.memory.backends import ScopedMemoryBackendFactory
from agent_runtime.settings import RuntimeSettings


class EmptyToolRegistry:
    """Tool registry used until production connector adapters are wired."""

    def list_available_tools(self, _context: object) -> Sequence[object]:
        return ()


class EmptyMcpRegistry:
    """MCP registry used until production MCP adapters are wired."""

    def list_available_servers(self, _context: object) -> Sequence[object]:
        return ()


class EmptySubagentCatalog:
    """Subagent catalog used until configured subagents are wired."""

    def list_available_subagents(self, _context: object) -> Sequence[object]:
        return ()


class DefaultRuntimeDependenciesFactory:
    """Build minimal runtime dependencies for worker-driven invocation."""

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or RuntimeSettings.load()

    def __call__(self, _context: AgentRuntimeContext) -> RuntimeDependencies:
        mcp_registry = self._mcp_registry(_context)
        return RuntimeDependencies(
            tool_registry=EmptyToolRegistry(),
            mcp_registry=mcp_registry,
            skill_source_config=SkillSourceConfig(),
            skill_registry=self._skill_registry(_context),
            memory_backend_factory=ScopedMemoryBackendFactory(),
            subagent_catalog=EmptySubagentCatalog(),
        )

    def _mcp_registry(self, context: AgentRuntimeContext) -> object:
        if self.settings.mcp.backend_registry_url is None:
            return EmptyMcpRegistry()
        provider = BackendMcpProvider(
            backend_url=self.settings.mcp.backend_registry_url,
            runtime_context=context,
            auth_redirect_uri=self.settings.mcp.auth_redirect_uri,
        )
        return DynamicMcpRegistry(providers=(provider,))

    def _skill_registry(self, context: AgentRuntimeContext) -> object | None:
        if self.settings.skills.backend_registry_url is None:
            return None
        provider = BackendSkillProvider(
            backend_url=self.settings.skills.backend_registry_url,
            runtime_context=context,
        )
        return VirtualSkillRegistry(providers=(provider,))
