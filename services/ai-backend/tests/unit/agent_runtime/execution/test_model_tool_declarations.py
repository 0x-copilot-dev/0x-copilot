"""Every tool the factory composes declares who owns its schema text (PRD-03).

``_model_visible_tools`` is the single place model tools are composed, and it is
therefore the single place a tool's resident context cost can be accepted. These
tests pin the two halves of that:

* **completeness** — measuring the composed surface yields no ``UNDECLARED``
  row, which is the runtime half of the design's declaration contract (§4.2);
  the AST conformance gate is the CI half, and neither substitutes for the
  other, because a declaration made under the wrong ``if`` branch would satisfy
  a source sweep and still measure as undeclared here; and
* **inertness** — declaring changes no tool, no order, and no digest. A
  declaration stamps an attribute and returns the same object, so the
  model-visible surface and the ``tool_schema_revision`` bound into prompt-cache
  identity are byte-for-byte what they were before this ledger existed.

Owner labels are asserted by value rather than by shape. A label is what an
occupancy report prints next to "650 tokens on every model call", so it has to
name the package a reader can actually open.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityActivationMode,
    CapabilityActivationResolver,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogScope,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import (
    _model_tool_schema_revision,
    _model_visible_tools,
)
from agent_runtime.execution.tool_surface import ModelToolOwner
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    declare_context_origin,
)
from agent_runtime.observability.context_tool_ledger import (
    ToolSchemaFootprint,
    ToolSchemaLedger,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class _FeatureTool:
    """A domain adapter the worker builds per run and the factory wraps."""

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


class ComposedSurfaceMixin:
    """Compose the fully-enabled model surface and project its declarations."""

    # The surface an ordinary production run composes with every optional seam
    # supplied. Pinned here so a change to composition *order* — which the
    # display, tool-policy, approval, and budget middleware all key off — cannot
    # ride along with a declaration edit unnoticed.
    COMPOSED_SURFACE = (
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
        "revise_artifact",
    )

    # Owner per composed tool. ``web_search`` arrives through the injected tool
    # registry — the seam the Deep Agents middleware tools also arrive on — so it
    # is attributed to the library rather than claimed by a package that does not
    # author its description. PRD-06's pinned adapter refines that bucket.
    EXPECTED_LABELS = {
        "web_search": "deepagents.middleware:web_search",
        "load_mcp_server": "agent_runtime.capabilities.mcp:load_mcp_server",
        "call_mcp_tool": "agent_runtime.capabilities.mcp:call_mcp_tool",
        "auth_mcp": "agent_runtime.capabilities.mcp:auth_mcp",
        "load_skill": "agent_runtime.capabilities.skills:load_skill",
        "load_prior_tool_result": (
            "agent_runtime.capabilities.tools:load_prior_tool_result"
        ),
        "ask_a_question": "agent_runtime.capabilities.tools:ask_a_question",
        "suggest_mcp_connector": (
            "agent_runtime.capabilities.discovery:suggest_mcp_connector"
        ),
        "run_code_mode": "agent_runtime.capabilities.interpreter:run_code_mode",
        "run_in_sandbox": "agent_runtime.capabilities.sandbox:run_in_sandbox",
        "stage_rowset_write": "agent_runtime.capabilities.dataflow:stage_rowset_write",
        "publish_artifact": "agent_runtime.capabilities.backends:publish_artifact",
        "revise_artifact": "agent_runtime.capabilities.backends:revise_artifact",
    }

    THIRD_PARTY_TOOLS = frozenset({"web_search"})

    NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
    REFERENCE_KEY = b"prd03-declaration-reference-key-32-by!!"
    SELECTION_REF = f"task-policy-selection://run_1/research/sha256/{'c' * 64}"

    def tool(self, name: str) -> StructuredTool:
        async def invoke(value: str = "") -> str:
            return value

        return StructuredTool.from_function(
            coroutine=invoke,
            name=name,
            description=f"{name} test tool",
        )

    def compose(
        self,
        runtime_context: AgentRuntimeContext,
        *,
        registry_tools: Sequence[object] | None = None,
        gated: bool = True,
        capability_activation: object | None = None,
        capability_catalog: object | None = None,
    ) -> tuple[object, ...]:
        """Compose the model surface with every optional seam supplied."""

        gated_tools: dict[str, object | None] = {
            "code_mode_tool": self.tool("run_code_mode"),
            "sandbox_execute_tool": self.tool("run_in_sandbox"),
            "stage_rowset_write_tool": _FeatureTool("stage_rowset_write"),
            "publish_artifact_tool": _FeatureTool("publish_artifact"),
            "revise_artifact_tool": _FeatureTool("revise_artifact"),
        }
        return _model_visible_tools(
            tools=(
                (self.tool("web_search"),)
                if registry_tools is None
                else tuple(registry_tools)
            ),
            mcp_registry=_McpRegistry(),
            skill_registry=_SkillRegistry(),
            prior_tool_result_loader=object(),
            mcp_discovery_cache=None,
            capability_activation=capability_activation,
            capability_catalog=capability_catalog,
            runtime_context=runtime_context,
            **(gated_tools if gated else {}),  # type: ignore[arg-type]
        )

    def deferred_activation(self) -> object:
        """Resolve the one posture that registers F3 bridge tools."""

        return CapabilityActivationResolver().resolve_configured(
            raw_mode=FeatureMode.ENFORCE.value,
            raw_activation=CapabilityActivationMode.DEFERRED.value,
        )

    def catalog(self, runtime_context: AgentRuntimeContext) -> CapabilityCatalog:
        """Build a real authorization-projected catalog, never a hand-made one."""

        return AuthorizedCatalogBuilder(reference_key=self.REFERENCE_KEY).build(
            context=runtime_context,
            scope=CapabilityCatalogScope.from_context(
                runtime_context,
                profile_id="research",
                policy_revision="policy_7",
                connector_scope_revision="scope_9",
            ),
            task_policy_selection_ref=self.SELECTION_REF,
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
            expires_at=self.NOW + timedelta(minutes=15),
        )

    def footprints(
        self, runtime_context: AgentRuntimeContext, **kwargs: object
    ) -> dict[str, ToolSchemaFootprint]:
        composed = self.compose(runtime_context, **kwargs)  # type: ignore[arg-type]
        return {
            footprint.tool_name: footprint
            for footprint in ToolSchemaLedger.measure(composed)
        }

    def legacy_revision(self, model_tools: Sequence[object]) -> str:
        """The pre-ledger digest serialization, copied verbatim from the factory."""

        schemas: list[dict[str, object]] = []
        for tool in model_tools:
            args_schema = getattr(tool, "args_schema", None)
            schema: object = None
            model_json_schema = getattr(args_schema, "model_json_schema", None)
            if callable(model_json_schema):
                schema = model_json_schema()
            schemas.append(
                {
                    "name": str(getattr(tool, "name", "")),
                    "description": str(getattr(tool, "description", "")),
                    "args_schema": schema,
                }
            )
        return canonical_json_sha256(
            {
                "schema_revision": "model-visible-tools-v1",
                "tools": sorted(schemas, key=lambda item: str(item["name"])),
            }
        )


class TestComposedSurfaceIsFullyDeclared(ComposedSurfaceMixin):
    def test_the_composed_order_is_unchanged(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        composed = self.compose(runtime_context_admin)

        assert (
            tuple(str(getattr(tool, "name", "")) for tool in composed)
            == self.COMPOSED_SURFACE
        )

    def test_every_composed_tool_carries_a_declaration(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        footprints = self.footprints(runtime_context_admin)

        undeclared = sorted(
            name
            for name, footprint in footprints.items()
            if footprint.label == UNDECLARED_CONTEXT_LABEL
        )

        assert undeclared == []

    def test_every_composed_tool_reports_its_owner(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        footprints = self.footprints(runtime_context_admin)

        assert {
            name: footprint.label for name, footprint in footprints.items()
        } == self.EXPECTED_LABELS

    def test_the_gated_wave_one_tools_are_declared_when_present(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # These four are the design's headline number: composed only when their
        # flag+desktop gate is on, and worth ~1,337 resident tokens when it is.
        footprints = self.footprints(runtime_context_admin)

        assert all(
            footprints[name].declared
            for name in (
                "run_code_mode",
                "run_in_sandbox",
                "stage_rowset_write",
                "publish_artifact",
                "revise_artifact",
            )
        )

    def test_a_gated_off_surface_is_still_fully_declared(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The web posture composes a materially different surface (§2.1), and a
        # declaration made only on the desktop branch would pass every other
        # assertion here.
        footprints = self.footprints(runtime_context_admin, gated=False)

        assert all(footprint.declared for footprint in footprints.values())

    def test_every_declaration_sits_in_the_tools_segment(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        footprints = self.footprints(runtime_context_admin)

        assert all(
            footprint.segment_class is ContextSegmentClass.TOOLS
            for footprint in footprints.values()
        )

    def test_tool_schema_text_is_resident_rent(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Tool-block text is re-sent on every model call until the surface
        # itself changes. ``RESIDENT`` is what tells a reader to fix it by
        # deferring the tool rather than by shrinking a per-result note.
        footprints = self.footprints(runtime_context_admin)

        assert all(
            footprint.lifecycle is ContextLifecycle.RESIDENT
            for footprint in footprints.values()
        )

    def test_only_library_supplied_tools_are_third_party(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        footprints = self.footprints(runtime_context_admin)

        assert {
            name for name, footprint in footprints.items() if footprint.third_party
        } == self.THIRD_PARTY_TOOLS

    def test_a_tool_that_declares_itself_keeps_its_own_declaration(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # A tool arriving pre-declared was declared by the code that authored
        # it, which knows more than the composition site does. Relabelling it
        # here would let the factory silently claim someone else's text.
        authored = declare_context_origin(
            self.tool("web_search"),
            ContextOrigin(
                owner="agent_runtime.capabilities.tools",
                name="web_search",
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=ContextLifecycle.RESIDENT,
            ),
        )

        footprints = self.footprints(
            runtime_context_admin, registry_tools=(authored,), gated=False
        )

        assert footprints["web_search"].label == (
            "agent_runtime.capabilities.tools:web_search"
        )
        assert footprints["web_search"].third_party is False

    def test_owner_namespaces_are_the_declared_enum(self) -> None:
        # Every label's owner half has to be a ``ModelToolOwner`` member, so a
        # new append site cannot invent a second spelling of an existing
        # namespace and split one owner's report across two rows.
        owners = {label.split(":", 1)[0] for label in self.EXPECTED_LABELS.values()}

        assert owners <= {str(owner) for owner in ModelToolOwner}


class TestCapabilityBridgeToolsAreDeclared(ComposedSurfaceMixin):
    """The F3 bridge composes tools the factory does not name individually."""

    def test_bridge_tools_carry_the_discovery_owner(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # ``_capability_bridge_tools`` returns a set decided by the registrar,
        # so the factory declares the group's owner and each tool still gets its
        # own label off its own name — a group declaration is shorthand for the
        # owner, never a roll-up that costs the report per-tool granularity.
        footprints = self.footprints(
            runtime_context_admin,
            capability_activation=self.deferred_activation(),
            capability_catalog=self.catalog(runtime_context_admin),
        )

        assert footprints[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value].label == (
            f"{ModelToolOwner.DISCOVERY}"
            f":{CapabilityBridgeToolName.SEARCH_CAPABILITIES.value}"
        )
        assert footprints[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value].label == (
            f"{ModelToolOwner.DISCOVERY}"
            f":{CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value}"
        )

    def test_a_deferred_surface_is_fully_declared(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        footprints = self.footprints(
            runtime_context_admin,
            capability_activation=self.deferred_activation(),
            capability_catalog=self.catalog(runtime_context_admin),
        )

        assert all(footprint.declared for footprint in footprints.values())

    def test_bridge_tools_are_first_party(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        footprints = self.footprints(
            runtime_context_admin,
            capability_activation=self.deferred_activation(),
            capability_catalog=self.catalog(runtime_context_admin),
        )

        assert (
            footprints[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value].third_party
            is False
        )

    def test_the_bridge_widens_the_measured_surface(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The point of measuring per tool: turning the bridge on is a context
        # cost, and the ledger has to be able to say how much.
        dark = self.footprints(runtime_context_admin)
        deferred = self.footprints(
            runtime_context_admin,
            capability_activation=self.deferred_activation(),
            capability_catalog=self.catalog(runtime_context_admin),
        )

        assert set(deferred) - set(dark) == {
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
        }
        assert sum(footprint.estimated_tokens for footprint in deferred.values()) > sum(
            footprint.estimated_tokens for footprint in dark.values()
        )


class TestDeclaringIsInert(ComposedSurfaceMixin):
    def test_the_schema_digest_matches_the_pre_ledger_serialization(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        composed = self.compose(runtime_context_admin)

        assert _model_tool_schema_revision(composed) == self.legacy_revision(composed)

    def test_declared_tools_still_expose_their_typed_schemas(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        composed = self.compose(runtime_context_admin)
        by_name = {str(getattr(tool, "name", "")): tool for tool in composed}

        assert getattr(by_name["ask_a_question"], "args_schema", None) is not None

    def test_declaring_returns_the_same_object_the_factory_composed(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # ``ModelToolDeclaration.declared`` wraps the append site, so a copy
        # here would mean the composed surface is not the object the middleware
        # chain was built around.
        registry_tool = self.tool("web_search")

        composed = self.compose(
            runtime_context_admin, registry_tools=(registry_tool,), gated=False
        )

        assert composed[0] is registry_tool

    def test_an_undeclarable_tool_still_composes(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Declaring is observability; a tool that refuses attribute assignment
        # costs a label in a report, never a run (§6.4).
        class _Frozen:
            __slots__ = ("description", "name")

            def __init__(self) -> None:
                self.name = "frozen_tool"
                self.description = "refuses attribute assignment"

        composed = self.compose(
            runtime_context_admin, registry_tools=(_Frozen(),), gated=False
        )

        assert str(getattr(composed[0], "name", "")) == "frozen_tool"
        assert ToolSchemaLedger.measure(composed)[0].declared is False
