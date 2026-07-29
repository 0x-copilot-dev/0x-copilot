"""F3 bridge registration at the runtime factory: dark by default, never wider.

The factory is the single place model tools are composed.  These tests pin two
things that matter more than the wiring itself:

* **feature-off parity** — with F3 dark (the current production posture) the
  composed model-visible surface is byte-identical to the pre-F3 path, proven by
  the exact tool sequence *and* the body-free tool-schema digest the prompt
  layer binds; and
* **narrowing only** — every unresolvable input (absent, wrongly typed, or a
  registrar that raises) removes bridge tools and falls back to that same
  untouched surface.  Nothing here can add a tool.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityActivationMode,
    CapabilityActivationResolver,
    CapabilityBridgeRegistrar,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogRevision,
    CapabilityCatalogScope,
)
from agent_runtime.capabilities.middleware import (
    ModelInvocationMiddleware,
    RuntimeControlMiddleware,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import (
    _model_tool_schema_revision,
    _model_visible_tools,
    acreate_agent_runtime,
)
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder
from tests.unit.fakes import FakeToolRegistry

_NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
_REFERENCE_KEY = b"f3-factory-wiring-reference-key-32-by!!"
_SELECTION_REF = f"task-policy-selection://run_1/research/sha256/{'c' * 64}"

# The pre-F3 disclosure path, with every optional factory seam supplied. This is
# the surface an ordinary production run composes today, and every non-deferred
# posture must reproduce it exactly.
_PRE_F3_SURFACE = (
    "web_search",
    "load_mcp_server",
    "call_mcp_tool",
    "auth_mcp",
    "load_skill",
    "load_prior_tool_result",
    "ask_a_question",
    "suggest_mcp_connector",
    "run_code_mode",
    "run_in_sandbox",
    "stage_rowset_write",
    "publish_artifact",
)
# Bridge tools land between ``suggest_mcp_connector`` and the gated Wave-1
# block, so they carry the same display / tool-policy / approval / budget
# middleware as every other model-visible tool.
_BRIDGE_INSERTION_INDEX = _PRE_F3_SURFACE.index("suggest_mcp_connector") + 1


class _FeatureTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} test tool"

    async def ainvoke(self, _value: object) -> dict[str, object]:
        return {"ok": True}


class _AuthProvider:
    async def create_auth_session(self, **_kwargs: object) -> object:
        raise AssertionError("composing the tool surface must not start OAuth")


class _McpRegistry:
    providers = (_AuthProvider(),)

    async def list_available_servers(
        self, _context: AgentRuntimeContext
    ) -> tuple[object, ...]:
        return ()

    async def resolve_server(self, _name: str) -> object:
        raise AssertionError("composing the tool surface must not resolve a server")


class _SkillRegistry:
    async def load_skill_by_name(self, _name: str) -> object:
        raise AssertionError("composing the tool surface must not load a skill")


def _tool(name: str) -> StructuredTool:
    async def invoke(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=f"{name} test tool",
    )


def _catalog(
    context: AgentRuntimeContext,
    *,
    expires_at: datetime | None = None,
) -> CapabilityCatalog:
    """Build a real authorization-projected catalog, never a hand-made one."""

    return AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
        context=context,
        scope=CapabilityCatalogScope.from_context(
            context,
            profile_id="research",
            policy_revision="policy_7",
            connector_scope_revision="scope_9",
        ),
        task_policy_selection_ref=_SELECTION_REF,
        mcp_server_cards=(
            McpServerCard(
                name="drive_server",
                display_name="Drive Server",
                short_description="Find relevant drive records.",
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.OAUTH2,
                required_scopes=frozenset({"Docs:Read"}),
                health=McpServerHealth.HEALTHY,
                load_cost=2,
                connector_slug="Google-Drive",
            ),
        ),
        expires_at=expires_at or (_NOW + timedelta(minutes=15)),
    )


def _ungenerated(catalog: CapabilityCatalog) -> CapabilityCatalog:
    """A catalog that cannot mint a revalidatable ref must register nothing."""

    return CapabilityCatalog(
        scope=catalog.scope,
        revision=CapabilityCatalogRevision(
            **catalog.revision.model_dump(exclude={"generation"}),
        ),
        entries=catalog.entries,
    )


def _decision(activation: CapabilityActivationMode) -> object:
    """Resolve a real decision through the F3.1 resolver, never a hand-built one."""

    raw_mode = {
        CapabilityActivationMode.DIRECT: FeatureMode.OFF,
        CapabilityActivationMode.SERVER: FeatureMode.ENFORCE,
        CapabilityActivationMode.SHADOW: FeatureMode.SHADOW,
        CapabilityActivationMode.DEFERRED: FeatureMode.ENFORCE,
    }[activation]
    return CapabilityActivationResolver().resolve_configured(
        raw_mode=raw_mode.value,
        raw_activation=activation.value,
    )


def _compose(
    runtime_context: AgentRuntimeContext,
    *,
    capability_activation: object | None = None,
    capability_catalog: object | None = None,
) -> tuple[object, ...]:
    """Compose the fully-enabled model tool surface for one posture."""

    return _model_visible_tools(
        tools=(_tool("web_search"),),
        mcp_registry=_McpRegistry(),
        skill_registry=_SkillRegistry(),
        prior_tool_result_loader=object(),
        mcp_discovery_cache=None,
        code_mode_tool=_tool("run_code_mode"),
        sandbox_execute_tool=_tool("run_in_sandbox"),
        stage_rowset_write_tool=_FeatureTool("stage_rowset_write"),
        publish_artifact_tool=_FeatureTool("publish_artifact"),
        capability_activation=capability_activation,
        capability_catalog=capability_catalog,
        runtime_context=runtime_context,
    )


def _without_registry_tools(
    dependencies: RuntimeDependencies,
) -> RuntimeDependencies:
    """Drop the shared fixture's placeholder registry tool.

    ``FakeToolRegistry`` lists a bare string, which composes as an unnamed
    entry. Removing it leaves only the factory's own appends, so the end-to-end
    assertions read as the exact composed sequence they are pinning.
    """

    return dependencies.model_copy(update={"tool_registry": FakeToolRegistry(tools=())})


def _surface(tools: tuple[object, ...]) -> tuple[tuple[str, ...], str]:
    """Project the exact ordered names plus the body-free schema digest."""

    names = tuple(str(getattr(tool, "name", "")) for tool in tools)
    return names, _model_tool_schema_revision(tools)


class TestFeatureOffParity:
    """With F3 dark the composed surface is the untouched pre-F3 surface."""

    def test_the_dark_path_composes_the_pinned_pre_f3_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        names, _digest = _surface(_compose(runtime_context_admin))

        assert names == _PRE_F3_SURFACE

    def test_runtime_dependencies_default_to_no_capability_discovery(
        self,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        assert fake_dependencies.capability_activation is None
        assert fake_dependencies.capability_catalog is None

    @pytest.mark.parametrize(
        "activation",
        [
            CapabilityActivationMode.DIRECT,
            CapabilityActivationMode.SERVER,
            CapabilityActivationMode.SHADOW,
        ],
    )
    def test_every_non_deferred_posture_composes_the_identical_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
        activation: CapabilityActivationMode,
    ) -> None:
        dark = _surface(_compose(runtime_context_admin))

        posture = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=_decision(activation),
                capability_catalog=_catalog(runtime_context_admin),
            )
        )

        assert posture == dark
        assert posture[0] == _PRE_F3_SURFACE

    def test_a_feature_mode_ceiling_composes_the_identical_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """``shadow`` mode cannot be widened by requesting ``deferred``."""

        decision = CapabilityActivationResolver().resolve_configured(
            raw_mode=FeatureMode.SHADOW.value,
            raw_activation=CapabilityActivationMode.DEFERRED.value,
        )

        posture = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=decision,
                capability_catalog=_catalog(runtime_context_admin),
            )
        )

        assert decision.effective_activation is CapabilityActivationMode.SHADOW
        assert posture == _surface(_compose(runtime_context_admin))

    def test_an_unbindable_catalog_composes_the_identical_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        posture = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=_decision(CapabilityActivationMode.DEFERRED),
                capability_catalog=_ungenerated(_catalog(runtime_context_admin)),
            )
        )

        assert posture == _surface(_compose(runtime_context_admin))

    @pytest.mark.parametrize("supplied", ["activation", "catalog"])
    def test_one_missing_input_composes_the_identical_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
        supplied: str,
    ) -> None:
        inputs: dict[str, object | None] = {
            "capability_activation": (
                _decision(CapabilityActivationMode.DEFERRED)
                if supplied == "activation"
                else None
            ),
            "capability_catalog": (
                _catalog(runtime_context_admin) if supplied == "catalog" else None
            ),
        }

        posture = _surface(_compose(runtime_context_admin, **inputs))  # type: ignore[arg-type]

        assert posture == _surface(_compose(runtime_context_admin))

    def test_wrongly_typed_inputs_compose_the_identical_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """An unresolvable input yields fewer tools, never a guessed posture."""

        posture = _surface(
            _compose(
                runtime_context_admin,
                capability_activation="deferred",
                capability_catalog={"entries": ["drive_search"]},
            )
        )

        assert posture == _surface(_compose(runtime_context_admin))

    def test_a_registrar_failure_composes_the_identical_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A dark feature must never widen a surface *or* fail a healthy run."""

        def explode(**_kwargs: object) -> tuple[object, ...]:
            raise RuntimeError("postgres://secret-host/capability_catalog")

        monkeypatch.setattr(
            CapabilityBridgeRegistrar,
            "registrations_for",
            staticmethod(explode),
        )

        posture = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=_decision(CapabilityActivationMode.DEFERRED),
                capability_catalog=_catalog(runtime_context_admin),
            )
        )

        assert posture[0] == _PRE_F3_SURFACE

    async def test_the_dark_factory_build_is_unchanged_end_to_end(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Nothing in the composed graph request moves while F3 is dark."""

        builder = CapturingAgentBuilder()

        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=_without_registry_tools(fake_dependencies),
            agent_builder=builder,
        )

        request = builder.calls[0]
        assert tuple(str(getattr(tool, "name", "")) for tool in request.tools) == (
            "ask_a_question",
            "suggest_mcp_connector",
        )
        assert [type(item) for item in request.middleware] == [
            RuntimeControlMiddleware,
            ModelInvocationMiddleware,
        ]
        assert request.universal_middleware_factories == (
            RuntimeControlMiddleware,
            ModelInvocationMiddleware,
        )


class TestDeferredBridgeRegistration:
    """In ``deferred`` the bridge tools appear, bounded and in one fixed place."""

    def test_bridge_tools_land_in_their_pinned_position(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        names, _digest = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=_decision(CapabilityActivationMode.DEFERRED),
                capability_catalog=_catalog(runtime_context_admin),
            )
        )

        assert names == (
            *_PRE_F3_SURFACE[:_BRIDGE_INSERTION_INDEX],
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
            *_PRE_F3_SURFACE[_BRIDGE_INSERTION_INDEX:],
        )

    def test_the_bridge_is_added_to_the_pre_f3_surface_and_removes_nothing(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """F3.9 suppresses a *prompt block*, never a tool.

        Search and describe replace the MCP card block's per-server
        enumeration. They do not replace ``load_mcp_server`` (describe returns
        no schema, and an MCP-server catalog entry carries no parameters),
        ``call_mcp_tool`` (its superseder ``invoke_capability`` is not
        registered), or ``auth_mcp`` (nothing in the bridge authenticates, and
        a catalog entry has no auth field at all). Dropping any of them would
        leave a deferred run unable to reach MCP.
        """

        names, _digest = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=_decision(CapabilityActivationMode.DEFERRED),
                capability_catalog=_catalog(runtime_context_admin),
            )
        )

        assert set(_PRE_F3_SURFACE) <= set(names)
        assert names.index("load_mcp_server") < names.index("call_mcp_tool")

    def test_invoke_capability_is_absent_until_its_seam_is_wired(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """The factory threads no executor or revalidation yet, so invoke is out."""

        names, _digest = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=_decision(CapabilityActivationMode.DEFERRED),
                capability_catalog=_catalog(runtime_context_admin),
            )
        )

        assert CapabilityBridgeToolName.INVOKE_CAPABILITY.value not in names

    def test_registered_names_stay_inside_the_closed_bridge_vocabulary(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        names, _digest = _surface(
            _compose(
                runtime_context_admin,
                capability_activation=_decision(CapabilityActivationMode.DEFERRED),
                capability_catalog=_catalog(runtime_context_admin),
            )
        )

        added = set(names) - set(_PRE_F3_SURFACE)
        assert added <= CapabilityBridgeToolName.reserved_names()

    def test_bridge_tools_are_ordinary_structured_tools(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """The factory composes them; the discovery package builds no model tool."""

        tools = _compose(
            runtime_context_admin,
            capability_activation=_decision(CapabilityActivationMode.DEFERRED),
            capability_catalog=_catalog(runtime_context_admin),
        )
        bridge = [
            tool
            for tool in tools
            if str(getattr(tool, "name", ""))
            in CapabilityBridgeToolName.reserved_names()
        ]
        neighbour = next(
            tool
            for tool in tools
            if str(getattr(tool, "name", "")) == "suggest_mcp_connector"
        )

        assert len(bridge) == 2
        for tool in bridge:
            assert type(tool) is type(neighbour)
            assert isinstance(tool, StructuredTool)
            assert str(tool.description).strip()
            assert tool.args_schema is not None
            assert tool.args_schema.model_config["extra"] == "forbid"  # type: ignore[union-attr]

    def test_registrations_carry_no_model_framework_type(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """The registrar hands over pure adapters; only the factory wraps them."""

        registrations = CapabilityBridgeRegistrar.registrations_for(
            activation=_decision(CapabilityActivationMode.DEFERRED),  # type: ignore[arg-type]
            catalog=_catalog(runtime_context_admin),
            runtime_context=runtime_context_admin,
        )

        assert registrations
        assert not any(
            isinstance(registration.adapter, StructuredTool)
            for registration in registrations
        )

    async def test_bridge_tools_enter_the_same_wrapping_as_every_other_tool(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Display, tool-policy, and middleware treatment is not special-cased."""

        builder = CapturingAgentBuilder()
        dependencies = _without_registry_tools(fake_dependencies).model_copy(
            update={
                "capability_activation": _decision(CapabilityActivationMode.DEFERRED),
                "capability_catalog": _catalog(runtime_context_admin),
            }
        )

        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=dependencies,
            agent_builder=builder,
        )

        request = builder.calls[0]
        by_name = {str(getattr(tool, "name", "")): tool for tool in request.tools}
        assert tuple(by_name) == (
            "ask_a_question",
            "suggest_mcp_connector",
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
        )
        reference = type(by_name["suggest_mcp_connector"])
        assert all(
            type(by_name[name.value]) is reference
            for name in (
                CapabilityBridgeToolName.SEARCH_CAPABILITIES,
                CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
            )
        )
        assert [type(item) for item in request.middleware] == [
            RuntimeControlMiddleware,
            ModelInvocationMiddleware,
        ]
