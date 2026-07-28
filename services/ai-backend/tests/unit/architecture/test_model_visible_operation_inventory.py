"""F-014: exhaustive model-tool descriptor and presentation-path canaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityActivationMode,
    CapabilityActivationResolver,
    CapabilityBridgeRegistrar,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogScope,
    CapabilityInvocationReceipt,
    CapabilityInvocationTarget,
)
from agent_runtime.capabilities.discovery.revision_authority import (
    CapabilityRefRevalidation,
)
from agent_runtime.capabilities.operations.builtin_catalog import (
    DEFAULT_BUILTIN_OPERATION_CATALOG,
    BuiltinOperationExecution,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.conformance import (
    OperationConformanceGate,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.delegation.subagents.atlas_task_tool import build_atlas_task_tool
from agent_runtime.effects.composition import EFFECT_DESCRIPTOR_STAGE_MAPPINGS
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import _model_visible_tools
from agent_runtime.surfaces_v2.ledger_models import EffectClass, EffectExecutorKind
from tests.unit.architecture.model_visible_operation_inventory import (
    direct_bespoke_surface_violations,
    model_tool_descriptor_violations,
)


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"
_REFERENCE_KEY = b"f014-inventory-reference-key-32-bytes!!"
_SELECTION_REF = f"task-policy-selection://run_1/research/sha256/{'c' * 64}"

# PRD-AR-F Step 0/2 topology proof — the pinned model-visible tool SEQUENCE
# after final factory assembly, with every optional seam supplied.
#
# Order is asserted, not only membership (PRD §1.1 execution discipline rule 7):
# the factory appends in a fixed order, later wrapper passes preserve it, and the
# graph binds the tools to the model in this order. A step that adds, removes, or
# reorders a model-visible tool updates this tuple deliberately; an ordering
# change that no pinned proof asserts is a defect.
#
# F3 capability-discovery bridge tools are registered between
# ``suggest_mcp_connector`` and the gated Wave-1 block. They are absent from
# *this* tuple because it pins the pre-F3 disclosure path (no activation
# decision, no catalog), which is also the current production posture. The
# deferred posture gets its own pinned sequence immediately below.
#
# Descriptor coverage, however, is proven over BOTH postures below. Composing
# only the dark path is what let BUG-07 sit undetected: three model-visible
# bridge tools existed with no catalog row and no descriptor, and the canary
# could not see them because nothing it composed ever produced them.
_PINNED_MODEL_VISIBLE_TOOL_ORDER = (
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

# The same proof for the posture F3 activation actually produces.
#
# This tuple is pinned here, and not only in the bridge lane's own tests,
# because F3.9 made the deferred surface load-bearing beyond tool dispatch: the
# factory decides whether to suppress the MCP card block by reading the bridge
# tools out of *this* composed sequence. A tool silently added to or dropped
# from it therefore moves the system prompt too, which is exactly the class of
# change discipline rule 7 exists to make deliberate.
#
# Note what F3.9 did *not* remove. ``load_mcp_server``, ``call_mcp_tool``, and
# ``auth_mcp`` all survive activation: search and describe replace the card
# block's per-server *enumeration*, not the tools that load a descriptor, call
# one, or authenticate a server. ``invoke_capability`` — the only registrable
# tool that would supersede ``call_mcp_tool`` — is absent because the factory
# threads neither an executor nor a revalidation, so removing the direct tools
# would leave a deferred run able to search and describe but never act.
_PINNED_DEFERRED_MODEL_VISIBLE_TOOL_ORDER = (
    "web_search",
    "load_mcp_server",
    "call_mcp_tool",
    "auth_mcp",
    "load_skill",
    "load_prior_tool_result",
    "ask_a_question",
    "suggest_mcp_connector",
    CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
    CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
    "run_code_mode",
    "run_in_sandbox",
    "stage_rowset_write",
    "publish_artifact",
)


class _FeatureTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} test tool"

    async def ainvoke(self, _value: object) -> dict[str, object]:
        return {"ok": True}


class _AuthProvider:
    async def create_auth_session(self, **_kwargs: object) -> object:
        raise AssertionError("tool inventory must not start OAuth")


class _McpRegistry:
    providers = (_AuthProvider(),)

    async def resolve_server(self, _name: str) -> object:
        raise AssertionError("tool inventory must not resolve an MCP server")


class _SkillRegistry:
    async def load_skill_by_name(self, _name: str) -> object:
        raise AssertionError("tool inventory must not load a skill")


def _tool(name: str) -> StructuredTool:
    async def invoke(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=f"{name} test tool",
    )


def _fully_enabled_factory_tools(
    runtime_context_admin: AgentRuntimeContext,
) -> tuple[object, ...]:
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
        runtime_context=runtime_context_admin,
    )


class _CapabilityExecutor:
    """A wired ``CapabilityExecutorPort`` seam; composing must never call it."""

    async def execute(
        self,
        *,
        target: CapabilityInvocationTarget,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        runtime_context: AgentRuntimeContext,
    ) -> CapabilityInvocationReceipt:
        raise AssertionError("tool inventory must not invoke a capability")


class _Revalidator:
    async def revalidate_at_use(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("tool inventory must not revalidate a capability ref")


def _capability_catalog(context: AgentRuntimeContext) -> CapabilityCatalog:
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
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


def _deferred_decision() -> object:
    """Resolve a real ``deferred`` decision through the F3.1 resolver."""

    return CapabilityActivationResolver().resolve_configured(
        raw_mode=FeatureMode.ENFORCE.value,
        raw_activation=CapabilityActivationMode.DEFERRED.value,
    )


def _deferred_factory_tools(
    runtime_context_admin: AgentRuntimeContext,
) -> tuple[object, ...]:
    """Compose the surface a ``deferred`` run actually hands to the model."""

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
        capability_activation=_deferred_decision(),
        capability_catalog=_capability_catalog(runtime_context_admin),
        runtime_context=runtime_context_admin,
    )


def _every_registrable_bridge_tool(
    runtime_context_admin: AgentRuntimeContext,
) -> tuple[object, ...]:
    """Every bridge tool the registrar can expose, with all optional seams wired.

    ``invoke_capability`` registers only once an executor *and* a revalidation
    are supplied, and the factory threads neither yet, so the deferred factory
    surface alone cannot prove the third tool is covered. The registrar is the
    one seam that decides which bridge tools a run may expose, so asking it
    directly — with every seam supplied — is what pins the maximal surface F3
    activation will produce.
    """

    registrations = CapabilityBridgeRegistrar.registrations_for(
        activation=_deferred_decision(),  # type: ignore[arg-type]
        catalog=_capability_catalog(runtime_context_admin),
        runtime_context=runtime_context_admin,
        executor=_CapabilityExecutor(),
        revalidation=CapabilityRefRevalidation(
            revalidator=_Revalidator(),  # type: ignore[arg-type]
            subject_fingerprint="a" * 64,
        ),
    )
    return tuple(registration.adapter for registration in registrations)


def _tool_order(tools: Sequence[object]) -> tuple[str, ...]:
    return tuple(str(getattr(tool, "name", "")) for tool in tools)


def _framework_tools() -> tuple[object, ...]:
    task = build_atlas_task_tool(
        (
            {
                "name": "researcher",
                "description": "Researches.",
                "runnable": RunnableLambda(lambda value: value),
            },
        )
    )
    return (*TodoListMiddleware().tools, *FilesystemMiddleware().tools, task)


def test_final_model_visible_tool_sequence_matches_the_pinned_topology(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Pin the exact composed sequence, not only its membership."""

    tools = _fully_enabled_factory_tools(runtime_context_admin)

    assert _tool_order(tools) == _PINNED_MODEL_VISIBLE_TOOL_ORDER


def test_deferred_model_visible_tool_sequence_matches_the_pinned_topology(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Pin the activated posture's sequence, which now also drives the prompt.

    Set membership is not enough here for the same reason it was not enough for
    the dark path, plus one F3.9-specific reason: the factory reads the bridge
    tools out of this composed surface to decide whether the MCP card block is
    suppressed. Losing a direct MCP tool from this tuple would be a silent
    behavioural change in what the model can reach, not just in what it is
    offered.
    """

    tools = _deferred_factory_tools(runtime_context_admin)

    assert _tool_order(tools) == _PINNED_DEFERRED_MODEL_VISIBLE_TOOL_ORDER


def test_every_assembled_model_tool_has_one_catalog_descriptor(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Cover every posture that can compose a model tool, not just the dark one.

    The union is the point. Asserting set *equality* against the catalog's
    model-visible entries means a row nothing composes is as much a failure as
    a tool nothing catalogs, so neither a stranded catalog row nor a
    flag-gated tool can hide.
    """

    tools = (
        *_fully_enabled_factory_tools(runtime_context_admin),
        *_deferred_factory_tools(runtime_context_admin),
        *_every_registrable_bridge_tool(runtime_context_admin),
        *_framework_tools(),
    )

    assert model_tool_descriptor_violations(tools) == ()
    actual_keys = {
        DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_model_tool_name(tool.name).key
        for tool in tools
    }
    expected_keys = {
        entry.key for entry in DEFAULT_BUILTIN_OPERATION_CATALOG.model_visible_entries()
    }
    assert actual_keys == expected_keys
    assert all(
        DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*entry.key) is not None
        for entry in DEFAULT_BUILTIN_OPERATION_CATALOG.model_visible_entries()
    )


def test_the_deferred_surface_has_one_catalog_descriptor_per_model_tool(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """BUG-07's regression proof: compose ``deferred``, not only the dark path.

    Before the fix this reported ``unregistered model-visible capability:
    search_capabilities`` and the same for ``describe_capability``.
    """

    tools = _deferred_factory_tools(runtime_context_admin)
    names = set(_tool_order(tools))

    # Guards against a vacuous pass: a deferred compose that registered no
    # bridge tool would otherwise satisfy the violation assertion trivially.
    assert names & CapabilityBridgeToolName.reserved_names() == {
        CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
        CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
    }
    assert model_tool_descriptor_violations(tools) == ()


def test_every_bridge_tool_the_registrar_can_expose_is_catalogued(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """``invoke_capability`` is covered too, ahead of its factory seam landing."""

    tools = _every_registrable_bridge_tool(runtime_context_admin)

    assert set(_tool_order(tools)) == CapabilityBridgeToolName.reserved_names()
    assert model_tool_descriptor_violations(tools) == ()


def test_the_deferred_surface_matches_the_reviewed_operation_inventory(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """The sibling gate agrees: no deferred model tool is missing from inventory.

    The two surfaces are validated separately because the gate refuses a
    duplicated registration, and the registrar surface is a superset of the
    bridge tools the factory composes.
    """

    OperationConformanceGate.validate_model_tool_surface(
        _deferred_factory_tools(runtime_context_admin)
    )
    OperationConformanceGate.validate_model_tool_surface(
        _every_registrable_bridge_tool(runtime_context_admin)
    )


def test_the_bridge_tools_declare_the_execution_path_they_actually_take() -> None:
    """Pin *why* each bridge classification is what it is, not just that it is.

    ``search`` and ``describe`` execute no effect and enter no gateway. Search's
    F3.3 second tier does open MCP servers, but only through ``McpLoader`` — the
    same bounded descriptor read ``load_mcp_server`` performs, which this
    catalog has always classified ``pure``. ``pure`` here means "executes no
    effect", never "performs no I/O".

    ``invoke`` takes the gateway path, but its effect is executed and recorded
    under ``builtin.call_mcp_tool``, which owns the stage mapping. The bridge
    borrows that identity rather than holding one of its own, which is what
    keeps F3 a reuse of the single MCP dispatch route instead of a second one.
    """

    executions = {
        entry.tool_name: entry.execution
        for entry in DEFAULT_BUILTIN_OPERATION_CATALOG.model_visible_entries()
    }
    descriptors = {
        name: DEFAULT_OPERATION_DESCRIPTORS.resolve_entry("builtin", name).descriptor
        for name in CapabilityBridgeToolName.reserved_names()
    }

    for name in (
        CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
        CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
    ):
        assert executions[name] is BuiltinOperationExecution.PURE
        assert descriptors[name].executor is EffectExecutorKind.BUILTIN
        assert descriptors[name].effect_class is EffectClass.NONE

    invoke = CapabilityBridgeToolName.INVOKE_CAPABILITY.value
    assert executions[invoke] is BuiltinOperationExecution.GATEWAY
    # Not ``mcp``: no staged effect is ever recorded under this key, so claiming
    # the MCP effect executor would assert a second dispatch route that does not
    # and must not exist.
    assert descriptors[invoke].executor is EffectExecutorKind.BUILTIN
    assert descriptors[invoke].effect_class is EffectClass.INTERNAL_REVERSIBLE
    assert descriptors[invoke].supports_prepare is False

    staged = {mapping.key for mapping in EFFECT_DESCRIPTOR_STAGE_MAPPINGS}
    assert ("builtin", "call_mcp_tool") in staged
    assert ("builtin", invoke) not in staged


def test_unregistered_model_visible_capability_canary_fails_closed(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    tools = (
        *_fully_enabled_factory_tools(runtime_context_admin),
        _tool("planted_f014"),
    )

    assert model_tool_descriptor_violations(tools)[-1:] == (
        "unregistered model-visible capability: planted_f014",
    )


def test_no_model_capability_can_construct_a_bespoke_surface_result() -> None:
    assert direct_bespoke_surface_violations(_SOURCE_ROOT) == ()


def test_direct_bespoke_surface_result_canary_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "agent_runtime" / "capabilities" / "rogue_tool.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "def model_visible_tool():\n    return {'surface': {'kind': 'table'}}\n",
        encoding="utf-8",
    )

    assert direct_bespoke_surface_violations(tmp_path) == (
        "agent_runtime/capabilities/rogue_tool.py:2:direct-surface-result",
    )


def test_direct_surface_envelope_canary_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "agent_runtime" / "capabilities" / "rogue_tool.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "def model_visible_tool():\n"
        "    return SurfaceEnvelope(surface_uri='surface://rogue')\n",
        encoding="utf-8",
    )

    assert direct_bespoke_surface_violations(tmp_path) == (
        "agent_runtime/capabilities/rogue_tool.py:2:direct-surface-envelope",
    )


def test_aliased_direct_surface_envelope_canary_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "agent_runtime" / "capabilities" / "rogue_tool.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "from agent_runtime.capabilities.surfaces.spec_models import SurfaceEnvelope as Envelope\n"
        "\n"
        "def model_visible_tool():\n"
        "    return Envelope(surface_uri='surface://rogue')\n",
        encoding="utf-8",
    )

    assert direct_bespoke_surface_violations(tmp_path) == (
        "agent_runtime/capabilities/rogue_tool.py:4:direct-surface-envelope",
    )
