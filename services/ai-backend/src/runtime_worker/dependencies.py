"""Default dependency factories for local runtime worker execution."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from agent_runtime.capabilities.citation_capturing_tool import (
    CitationCapturingRegistry,
)
from agent_runtime.capabilities.mcp.backend_provider import BackendMcpProvider
from agent_runtime.capabilities.mcp.freshness import RevisionAwareMcpDiscoveryCache
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.skills.sources import SkillSource, SkillSourceConfig
from agent_runtime.capabilities.skills.virtual import (
    BackendSkillProvider,
    VirtualSkillRegistry,
)
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
from agent_runtime.effects.rollout import effect_execution_capabilities
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import (
    E2RolloutAdmission,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.settings import RuntimeEnvironment, RuntimeSettings
from runtime_worker.capability_discovery_composition import (
    CapabilityDiscoveryComposer,
    CapabilityDiscoveryEnvironment,
    RunCapabilityDiscovery,
)

if TYPE_CHECKING:  # pragma: no cover - typing only.
    from agent_runtime.capabilities.mcp.cards import McpServerCard


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

    def list_available_tools(self, context: object) -> Sequence[object]:
        """Return the built-in tool list, honoring the per-run web-search toggle.

        The composer Tools popover can disable web search for a single turn; the
        run's :class:`AgentRuntimeContext` carries ``web_search_enabled`` (default
        ``True``). When it is ``False`` the ``web_search`` tool is omitted for that
        run only. ``getattr`` defaults to ``True`` so any caller passing a bare
        object / ``None`` (older tests, the capability-mode probe) keeps the
        historic always-on behavior.
        """
        if not getattr(context, "web_search_enabled", True):
            return ()
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
    """MCP registry used when no MCP providers are configured.

    ``list_available_servers`` is ``async`` to honor the registry port contract
    that :func:`agent_runtime.execution.factory.acreate_agent_runtime` awaits in
    its bootstrap fan-out — the ``DynamicMcpRegistry`` sibling is async too. A
    sync method here would raise ``TypeError: … awaitable is required`` for any
    deployment without an MCP backend URL, silently breaking every run.
    """

    async def list_available_servers(self, _context: object) -> Sequence[object]:
        """Return an empty server list (no MCP servers configured)."""
        return ()


class EmptySubagentCatalog:
    """Subagent catalog used until configured subagents are wired."""

    def list_available_subagents(self, _context: object) -> Sequence[object]:
        """Return an empty subagent list (no subagents configured)."""
        return ()


class DefaultRuntimeDependenciesFactory:
    """Build minimal runtime dependencies for worker-driven invocation.

    The ``mcp_discovery_cache`` is one instance per worker process — passed
    through ``RuntimeDependencies`` to the runtime factory, which threads it
    into :class:`McpLoader` and :class:`AuthMcpTool`. When ``None``, the
    loader runs the live network path on every call (pre-cache behaviour).
    """

    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        mcp_discovery_cache: object | None = None,
        capability_discovery: CapabilityDiscoveryComposer | None = None,
    ) -> None:
        """Load runtime settings; falls back to ``RuntimeSettings.load()`` when ``settings`` is ``None``.

        ``capability_discovery`` is the optional F3 composer. When absent, the
        factory builds a default one only for a deployment that has configured
        F3 activation at all, and that default has no card snapshot to project,
        so it resolves dark. Every deployment that has configured nothing is
        byte-identical to the pre-F3 disclosure path.
        """
        self.settings = settings or RuntimeSettings.load()
        self.mcp_discovery_cache = mcp_discovery_cache
        self.capability_discovery = capability_discovery
        self._rollout_admission = E2RolloutAdmission(
            resolution=self.settings.execution.rollout,
            cohorts=self.settings.execution.rollout_cohorts,
            kill_switches=self.settings.execution.rollout_kill_switches,
        )

    def __call__(self, _context: AgentRuntimeContext) -> RuntimeDependencies:
        """Build dependencies without an E2 subject-bound browser adapter.

        The regular worker handlers call :meth:`for_run`, which supplies
        persisted-run facts at the one model-exposure boundary.  Keeping this
        generic callable conservative prevents another composition root from
        activating the device browser adapter with request-shaped context.
        """

        return self._build_dependencies(_context)

    def for_run(
        self,
        context: AgentRuntimeContext,
        *,
        rollout_admission: E2RolloutAdmission,
        rollout_facts: PersistedRunCohortFactsProvider,
        mcp_server_cards: Sequence["McpServerCard"] | None = None,
    ) -> RuntimeDependencies:
        """Build dependencies for one persisted, authenticated runtime run.

        ``mcp_server_cards`` is the already-awaited, already permission-filtered
        card snapshot the F3 catalog is projected from. The runtime dependency
        factory is synchronous by contract while the MCP registry lists cards
        asynchronously, so the caller — which has already awaited them — is the
        only place the snapshot can come from. ``None`` leaves F3 dark.
        """

        return self._build_dependencies(
            context,
            rollout_admission=rollout_admission,
            rollout_facts=rollout_facts,
            mcp_server_cards=mcp_server_cards,
        )

    def _build_dependencies(
        self,
        context: AgentRuntimeContext,
        *,
        rollout_admission: E2RolloutAdmission | None = None,
        rollout_facts: PersistedRunCohortFactsProvider | None = None,
        mcp_server_cards: Sequence["McpServerCard"] | None = None,
    ) -> RuntimeDependencies:
        """Build and return the full ``RuntimeDependencies`` graph for a worker run.

        Tool registries retain tool-specific citation and error adapters. The
        graph-wide runtime middleware owns budget/task/result controls because
        Deep Agents injects additional tools after registry assembly.
        """
        self._validate_capability_mode(context)
        mcp_registry = self._mcp_registry(
            context,
            rollout_admission=rollout_admission,
            rollout_facts=rollout_facts,
        )
        tool_registry = ToolErrorPolicyRegistry(
            inner=CitationCapturingRegistry(inner=WebSearchToolRegistry())
        )
        # Single gate read per run: on the desktop file store this returns the
        # wiring that persists memory / skills / subagent defs as files; on the
        # web / postgres / in-memory images it is ``None`` and every branch below
        # falls back to the prior behavior byte-identically.
        file_agent_wiring = self._file_agent_wiring()
        # Single gate read per run for F3, mirroring the file-store gate above:
        # ``None`` on every deployment that has configured nothing, so the
        # factory registers no bridge tool and the model-visible surface is
        # byte-identical to the pre-F3 disclosure path.
        discovery = self._capability_discovery(
            context,
            mcp_server_cards=mcp_server_cards,
        )
        return RuntimeDependencies(
            tool_registry=tool_registry,
            mcp_registry=mcp_registry,
            skill_source_config=self._skill_source_config(file_agent_wiring),
            skill_registry=self._skill_registry(context),
            memory_backend_factory=self._memory_backend_factory(file_agent_wiring),
            subagent_catalog=self._subagent_catalog(file_agent_wiring),
            mcp_discovery_cache=self.mcp_discovery_cache,
            capability_activation=(
                discovery.activation if discovery is not None else None
            ),
            capability_catalog=discovery.catalog if discovery is not None else None,
        )

    def _capability_discovery(
        self,
        context: AgentRuntimeContext,
        *,
        mcp_server_cards: Sequence["McpServerCard"] | None = None,
    ) -> RunCapabilityDiscovery | None:
        """Return this run's composed F3 inputs, or ``None`` to stay dark.

        The presence gate is read first and cheaply so an unconfigured
        deployment never constructs a composer and never imports the discovery
        package at all — the same shape the file-store gate uses, and the
        property W1's ``_capability_bridge_tools`` documents for the dark path.

        Testing *presence* rather than meaning is deliberate: it is not a second
        activation vocabulary. Any value that is present, including a
        misspelling, still goes through the one F3 resolver and still resolves
        to the conservative default.
        """

        composer = self.capability_discovery
        if composer is None:
            if not CapabilityDiscoveryEnvironment.is_configured():
                return None
            composer = CapabilityDiscoveryComposer()
        return composer.compose(context, mcp_server_cards=mcp_server_cards)

    @staticmethod
    def _file_agent_wiring() -> object | None:
        """Return the file-store agent-state wiring when active, else ``None``.

        The env gate (``RUNTIME_STORE_BACKEND=file`` + ``RUNTIME_FILE_STORE_ROOT``)
        is read cheaply first so the desktop-only file adapter — and its deep
        agent / object-store imports — is never loaded on the web/postgres images.
        """

        import os  # noqa: PLC0415

        backend = os.environ.get("RUNTIME_STORE_BACKEND", "").strip().lower()
        root = os.environ.get("RUNTIME_FILE_STORE_ROOT", "").strip()
        if backend != "file" or not root:
            return None
        from runtime_adapters.file.agent_state_store import (  # noqa: PLC0415
            FileAgentStateWiring,
        )

        wiring = FileAgentStateWiring()
        return wiring if wiring.active else None

    @classmethod
    def build_default_discovery_cache(
        cls,
        settings: RuntimeSettings | None = None,
    ) -> RevisionAwareMcpDiscoveryCache:
        """Compatibility facade returning the worker assembly's only cache.

        New composition roots use :class:`McpRevisionControlPlaneBuilder` so
        the cache, resolver, feed, and lifecycle remain one ownership graph.
        This legacy test seam intentionally retains the old return type.
        """

        from runtime_worker.mcp_revision_composition import (  # noqa: PLC0415
            McpRevisionControlPlaneBuilder,
        )

        return McpRevisionControlPlaneBuilder.build(settings).discovery_cache

    def _skill_source_config(
        self, file_agent_wiring: object | None = None
    ) -> SkillSourceConfig:
        """Return a ``SkillSourceConfig`` combining built-in and file-store skills.

        The built-in wheel skills stay at precedence 0. When the desktop file
        store is active, its ``skills/`` root is added at a higher precedence so
        user-authored / hand-dropped skills override a wheel skill of the same
        name (last source wins). Off the file store the behavior is unchanged.
        """

        sources: list[SkillSource] = []
        if BUILTIN_SKILLS_ROOT.is_dir():
            sources.append(SkillSource(path=BUILTIN_SKILLS_ROOT, precedence=0))
        if file_agent_wiring is not None:
            skills_root = file_agent_wiring.skills_root()
            if skills_root is not None:
                sources.append(SkillSource(path=skills_root, precedence=1))
        return SkillSourceConfig(sources=tuple(sources))

    @staticmethod
    def _memory_backend_factory(file_agent_wiring: object | None = None) -> object:
        """Return the memory backend factory, file-backed when the store is active.

        On the file store the factory's ``backend_builder`` yields per-route
        :class:`FileMemoryBackend` instances so memory persists as inspectable
        JSON + ``.md`` files. Off the file store it is the plain route-plan
        factory, byte-identical to before.
        """

        if file_agent_wiring is None:
            return ScopedMemoryBackendFactory()
        return ScopedMemoryBackendFactory(
            backend_builder=file_agent_wiring.memory_backend_builder()
        )

    @staticmethod
    def _subagent_catalog(file_agent_wiring: object | None = None) -> object:
        """Return the subagent catalog, file-backed when the store is active.

        On the file store, subagent definitions are loaded from
        ``subagent_defs/*.json`` through the standard dynamic catalog (same
        permission-visibility + duplicate-name checks). Off the file store it is
        the empty catalog, unchanged.
        """

        if file_agent_wiring is None:
            return EmptySubagentCatalog()
        from agent_runtime.delegation.subagents.definitions import (  # noqa: PLC0415
            DynamicSubagentCatalog,
        )

        provider = file_agent_wiring.subagent_definition_provider()
        return DynamicSubagentCatalog(providers=(provider,))

    def _validate_capability_mode(self, context: AgentRuntimeContext) -> None:
        """Raise ``AgentRuntimeError`` in production when no capability source is configured."""
        if self.settings.environment is not RuntimeEnvironment.PRODUCTION:
            return
        if self.settings.execution.allow_empty_capabilities:
            return
        # Deployment-level check: web search is ALWAYS composed into the tool
        # registry (a configured capability source), independent of the per-run
        # ``web_search_enabled`` toggle. Probe with ``None`` so a run that
        # disables web search for its turn does not spuriously trip the
        # production "no capability sources" guard.
        if WebSearchToolRegistry().list_available_tools(None):
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

    def _mcp_registry(
        self,
        context: AgentRuntimeContext,
        *,
        rollout_admission: E2RolloutAdmission | None = None,
        rollout_facts: PersistedRunCohortFactsProvider | None = None,
    ) -> object:
        """Compose the backend SaaS provider with the gated desktop-browser provider.

        The backend SaaS provider is present only when an MCP backend URL is
        configured. The device-local browser provider (AC8) is composed
        alongside it — WITHOUT a duplicate name — only when
        ``RUNTIME_ENABLE_DESKTOP_BROWSER`` + ``single_user_desktop`` + a browser
        broker URL/token are all present (``build_browser_mcp`` fails closed
        otherwise). With no providers at all, an ``EmptyMcpRegistry`` is returned
        so non-desktop / unconfigured images are byte-identical.
        """
        providers: list[object] = []
        if (
            self.settings.mcp.backend_registry_url is not None
            and self._backend_mcp_allowed(
                rollout_admission=rollout_admission,
                rollout_facts=rollout_facts,
            )
        ):
            providers.append(
                BackendMcpProvider(
                    backend_url=self.settings.mcp.backend_registry_url,
                    runtime_context=context,
                    auth_redirect_uri=self.settings.mcp.auth_redirect_uri,
                )
            )
        browser_provider = self._browser_provider(
            context,
            rollout_admission=rollout_admission,
            rollout_facts=rollout_facts,
        )
        if browser_provider is not None:
            providers.append(browser_provider)
        if not providers:
            return EmptyMcpRegistry()
        return DynamicMcpRegistry(providers=tuple(providers))

    def _backend_mcp_allowed(
        self,
        *,
        rollout_admission: E2RolloutAdmission | None,
        rollout_facts: PersistedRunCohortFactsProvider | None,
    ) -> bool:
        """Gate generic backend MCP discovery before server cards are exposed.

        A regular worker run supplies persisted facts, so the full MCP effect
        dependency set is cohort-admitted. Generic composition has no verified
        subject: it may retain legacy discovery only while that set is wholly
        uncontrolled; an explicit E2 request cannot leak cards through an
        unscoped dependency factory.
        """

        admission = rollout_admission or self._rollout_admission
        capabilities = effect_execution_capabilities(EffectExecutorKind.MCP)
        if rollout_facts is None:
            return not admission.controls_any(capabilities)
        return admission.permits_all(
            capabilities=capabilities,
            facts_provider=rollout_facts,
        )

    def _browser_provider(
        self,
        context: AgentRuntimeContext,
        *,
        rollout_admission: E2RolloutAdmission | None = None,
        rollout_facts: PersistedRunCohortFactsProvider | None = None,
    ) -> object | None:
        """Build the gated device-local browser MCP provider, or ``None``.

        Gated OFF by default: the ``build_browser_mcp`` seam returns ``None``
        unless the browser flag is truthy, the deployment profile is
        ``single_user_desktop``, and the browser broker URL + token are set. All
        signals come from the trusted desktop service environment; off desktop
        the vars are absent and no card ever appears.
        """
        import os  # noqa: PLC0415

        from agent_runtime.capabilities.browser.constants import (  # noqa: PLC0415
            BrowserEnv,
        )
        from agent_runtime.capabilities.browser.desktop_browser_provider import (  # noqa: PLC0415
            BrowserMcpConfig,
            build_browser_mcp,
        )

        # Browser capability discovery is model-tool exposure.  It is denied
        # unless the worker supplied a persisted run subject and the entire
        # operation/effect/browser dependency set is admitted.  There is no
        # fallback that turns a raw AgentRuntimeContext into rollout facts.
        if rollout_admission is None or rollout_facts is None:
            return None
        if not rollout_admission.permits_all(
            capabilities=(
                RolloutCapability.OPERATION_GATEWAY,
                RolloutCapability.EFFECT_STAGER,
                RolloutCapability.EFFECT_COMMIT,
                RolloutCapability.BROWSER_ADAPTER,
            ),
            facts_provider=rollout_facts,
        ):
            return None
        env = os.environ
        return build_browser_mcp(
            BrowserMcpConfig(
                enabled=BrowserEnv.is_enabled(env.get(BrowserEnv.FLAG)),
                deployment_profile=env.get("ENTERPRISE_DEPLOYMENT_PROFILE", ""),
                broker_url=env.get(BrowserEnv.BROKER_URL) or None,
                broker_token=env.get(BrowserEnv.BROKER_TOKEN) or None,
                service_identity=env.get(BrowserEnv.SERVICE_IDENTITY) or None,
                broker_audience=env.get(BrowserEnv.BROKER_AUDIENCE) or None,
                runtime_context=context,
                effects_enabled=bool(
                    self.settings.execution.surfaces_v2
                    and self.settings.execution.operation_gateway_mode
                    is OperationGatewayMode.ENFORCE
                ),
            )
        )

    def _skill_registry(self, context: AgentRuntimeContext) -> object | None:
        """Return a ``VirtualSkillRegistry`` backed by the backend provider, or ``None`` if unconfigured."""
        if self.settings.skills.backend_registry_url is None:
            return None
        provider = BackendSkillProvider(
            backend_url=self.settings.skills.backend_registry_url,
            runtime_context=context,
        )
        return VirtualSkillRegistry(providers=(provider,))
