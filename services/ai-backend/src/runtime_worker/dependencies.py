"""Default dependency factories for local runtime worker execution."""

from __future__ import annotations

from collections.abc import Sequence

from agent_runtime.agent.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.capabilities.skills.sources import SkillSourceConfig
from agent_runtime.context.memory.backends import ScopedMemoryBackendFactory
from agent_runtime.events.normalization.langgraph import LangGraphStreamNormalizer


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

    def __call__(self, _context: AgentRuntimeContext) -> RuntimeDependencies:
        return RuntimeDependencies(
            tool_registry=EmptyToolRegistry(),
            mcp_registry=EmptyMcpRegistry(),
            skill_source_config=SkillSourceConfig(),
            memory_backend_factory=ScopedMemoryBackendFactory(),
            subagent_catalog=EmptySubagentCatalog(),
            stream_normalizer=LangGraphStreamNormalizer(),
        )
