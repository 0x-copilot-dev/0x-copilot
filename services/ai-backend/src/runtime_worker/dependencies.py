"""Default dependency factories for local runtime worker execution."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agent_runtime.capabilities.citation_capturing_tool import (
    CitationCapturingRegistry,
)
from agent_runtime.capabilities.mcp.backend_provider import BackendMcpProvider
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.skills.sources import SkillSource, SkillSourceConfig
from agent_runtime.capabilities.skills.virtual import (
    BackendSkillProvider,
    VirtualSkillRegistry,
)
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuardedRegistry
from agent_runtime.capabilities.tool_error_policy_tool import (
    ToolErrorPolicyRegistry,
)
from agent_runtime.context.memory.backends import ScopedMemoryBackendFactory
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeEnvironment, RuntimeSettings


# Built-in skills shipped with the runtime. The directory is resolved relative
# to this file so wheel-installed deployments and local dev both work without
# extra configuration. Each subdirectory under `skills/` must contain a
# `SKILL.md` with YAML frontmatter (`name`, `description`, ...) per Anthropic's
# Agent Skills spec.
BUILTIN_SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent / "skills"


class WebSearchToolRegistry:
    """Default local tools available to Deep Agents runtime runs."""

    class Values:
        WEB_SEARCH_TOOL_NAME = "web_search"

    class Messages:
        WEB_SEARCH_TOOL_DESCRIPTION = (
            "Search the public web for recent information, documentation, news, "
            "and external references. Returns result snippets with source links."
        )

    def list_available_tools(self, _context: object) -> Sequence[object]:
        """Return the web-search tool as the default built-in tool list."""
        return (self._web_search_tool(),)

    @classmethod
    def _web_search_tool(cls) -> object:
        """Build a retry-wrapped DuckDuckGo search tool.

        The underlying library raises opaque ``DDGSException`` wrappers on transient
        failures; the ``RetryingTool`` wrapper absorbs those and only re-raises after
        sustained failure so a single hiccup does not terminate the subagent run.
        """
        from langchain_community.tools import DuckDuckGoSearchResults

        from agent_runtime.capabilities.retrying_tool import RetryingTool

        inner = DuckDuckGoSearchResults(
            name=cls.Values.WEB_SEARCH_TOOL_NAME,
            description=cls.Messages.WEB_SEARCH_TOOL_DESCRIPTION,
            output_format="list",
        )
        return RetryingTool(
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
            inner=inner,
            max_attempts=3,
            initial_backoff_seconds=1.0,
            max_backoff_seconds=8.0,
        )


class EmptyMcpRegistry:
    """MCP registry used until production MCP adapters are wired."""

    def list_available_servers(self, _context: object) -> Sequence[object]:
        """Return an empty server list (no MCP servers configured)."""
        return ()


class EmptySubagentCatalog:
    """Subagent catalog used until configured subagents are wired."""

    def list_available_subagents(self, _context: object) -> Sequence[object]:
        """Return an empty subagent list (no subagents configured)."""
        return ()


class DefaultRuntimeDependenciesFactory:
    """Build minimal runtime dependencies for worker-driven invocation."""

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        """Load runtime settings; falls back to ``RuntimeSettings.load()`` when ``settings`` is ``None``."""
        self.settings = settings or RuntimeSettings.load()

    def __call__(self, _context: AgentRuntimeContext) -> RuntimeDependencies:
        """Build and return the full ``RuntimeDependencies`` graph for a worker run.

        Tool registries are composed outermost-to-innermost:
        ``ToolErrorPolicyRegistry`` → ``ToolBudgetGuardedRegistry`` →
        ``CitationCapturingRegistry`` → ``WebSearchToolRegistry``.
        """
        self._validate_capability_mode(_context)
        mcp_registry = self._mcp_registry(_context)
        tool_registry = ToolErrorPolicyRegistry(
            inner=ToolBudgetGuardedRegistry(
                inner=CitationCapturingRegistry(inner=WebSearchToolRegistry())
            )
        )
        return RuntimeDependencies(
            tool_registry=tool_registry,
            mcp_registry=mcp_registry,
            skill_source_config=self._skill_source_config(),
            skill_registry=self._skill_registry(_context),
            memory_backend_factory=ScopedMemoryBackendFactory(),
            subagent_catalog=EmptySubagentCatalog(),
        )

    def _skill_source_config(self) -> SkillSourceConfig:
        """Return a ``SkillSourceConfig`` that includes the built-in skills directory, if it exists."""

        if not BUILTIN_SKILLS_ROOT.is_dir():
            return SkillSourceConfig()
        return SkillSourceConfig(
            sources=(SkillSource(path=BUILTIN_SKILLS_ROOT, precedence=0),),
        )

    def _validate_capability_mode(self, context: AgentRuntimeContext) -> None:
        """Raise ``AgentRuntimeError`` in production when no capability source is configured."""
        if self.settings.environment is not RuntimeEnvironment.PRODUCTION:
            return
        if self.settings.execution.allow_empty_capabilities:
            return
        if WebSearchToolRegistry().list_available_tools(context):
            return
        if self.settings.mcp.backend_registry_url is not None:
            return
        if self.settings.skills.backend_registry_url is not None:
            return
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Runtime capability sources are not configured for production.",
            retryable=False,
            correlation_id=context.trace_id,
        )

    def _mcp_registry(self, context: AgentRuntimeContext) -> object:
        """Return a ``DynamicMcpRegistry`` backed by the backend provider, or an ``EmptyMcpRegistry`` if unconfigured."""
        if self.settings.mcp.backend_registry_url is None:
            return EmptyMcpRegistry()
        provider = BackendMcpProvider(
            backend_url=self.settings.mcp.backend_registry_url,
            runtime_context=context,
            auth_redirect_uri=self.settings.mcp.auth_redirect_uri,
        )
        return DynamicMcpRegistry(providers=(provider,))

    def _skill_registry(self, context: AgentRuntimeContext) -> object | None:
        """Return a ``VirtualSkillRegistry`` backed by the backend provider, or ``None`` if unconfigured."""
        if self.settings.skills.backend_registry_url is None:
            return None
        provider = BackendSkillProvider(
            backend_url=self.settings.skills.backend_registry_url,
            runtime_context=context,
        )
        return VirtualSkillRegistry(providers=(provider,))
