"""F3.9 — ``deferred`` suppresses the path its bridge tools replace.

F3 exists to *reduce* prompt load (PRD §11 Step 8).  Before this lane it did
the opposite: the bridge tools were appended and nothing was removed, so a
``deferred`` run paid for the bridge tools *and* the unbounded MCP card block —
one line per authorized server, on every turn, growing linearly with connector
count.  This module pins the fix and, more importantly, its safety boundary.

Three things are asserted here and nowhere else:

* **suppression is real** — a registered bridge removes the card block and
  replaces it with a constant-size protocol paragraph, so the deferred prompt
  no longer grows with connector count at all;
* **suppression cannot happen without registration** — every way the bridge can
  fail to register (unbindable catalog, wrong types, missing input, ceiling,
  registrar raising) leaves the *complete* pre-F3 surface: card block, direct
  tools, and byte-identical prompt.  There is no reachable state in which the
  model has neither the cards nor a bridge; and
* **the direct MCP tools survive** — search and describe supersede the card
  block's *enumeration*, not the tools that load, call, or authenticate a
  server.  Suppressing those would leave a deferred run unable to reach MCP at
  all, because the factory registers no ``invoke_capability``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import acreate_agent_runtime
from agent_runtime.prompts.runtime import (
    CAPABILITY_DISCOVERY_INSTRUCTIONS,
    MCP_SERVER_CARDS_INSTRUCTIONS,
    NO_MCP_SERVER_CARDS_INSTRUCTIONS,
)
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder
from tests.unit.fakes import FakeMcpRegistry, FakeToolRegistry

_REFERENCE_KEY = b"f39-suppression-reference-key-32-byte!!"
_SELECTION_REF = f"task-policy-selection://run_1/research/sha256/{'c' * 64}"

# A card-block line the model can only have got from the enumeration. Asserting
# on a rendered row rather than on the header is what makes "the block is gone"
# mean the *rows* are gone, not merely its heading.
_CARD_ROW_MARKER = "auth_state="

# The direct MCP tools. None is superseded by the two bridge tools the factory
# actually registers, so all three must survive suppression:
#
# * ``load_mcp_server`` — describe_capability returns bounded metadata and
#   explicitly never a schema, and an MCP-server catalog entry carries no
#   parameters at all, so nothing else can produce a callable tool name;
# * ``call_mcp_tool`` — its superseder is ``invoke_capability``, which needs an
#   executor and a revalidation the factory does not thread, so it is never
#   registered; and
# * ``auth_mcp`` — nothing in the bridge authenticates, and a CapabilityIndexEntry
#   has no auth field, so removing the cards removes the only *proactive* auth
#   signal. The tool is the reactive route that replaces it.
_DIRECT_MCP_TOOLS = (
    McpValues.ToolName.LOAD_MCP_SERVER,
    McpValues.ToolName.CALL_MCP_TOOL,
    McpValues.ToolName.AUTH_MCP,
)


class _AuthProvider:
    async def create_auth_session(self, **_kwargs: object) -> object:
        raise AssertionError("composing a runtime must not start OAuth")


class _McpRegistry(FakeMcpRegistry):
    """A registry that also advertises the OAuth + resolve seams."""

    providers = (_AuthProvider(),)

    async def resolve_server(self, _name: str) -> object:
        raise AssertionError("composing a runtime must not resolve a server")


def _cards(count: int) -> tuple[McpServerCard, ...]:
    return tuple(
        McpServerCard(
            name=f"connector_{index}",
            server_id=f"srv_{index}",
            display_name=f"Connector {index}",
            short_description=f"Read and write records in connector {index}.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=(
                McpAuthState.AUTHENTICATED
                if index % 3
                else McpAuthState.UNAUTHENTICATED
            ),
            health=McpServerHealth.HEALTHY,
            load_cost=1,
        )
        for index in range(count)
    )


def _catalog(
    context: AgentRuntimeContext,
    *,
    cards: tuple[McpServerCard, ...],
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
        mcp_server_cards=cards,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


def _ungenerated(catalog: CapabilityCatalog) -> CapabilityCatalog:
    """A catalog that cannot mint a revalidatable ref registers nothing."""

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


async def _build(
    context: AgentRuntimeContext,
    dependencies: RuntimeDependencies,
    *,
    servers: int = 20,
    capability_activation: object | None = None,
    capability_catalog: object | None = None,
) -> object:
    """Compose one full runtime and return the captured graph request."""

    builder = CapturingAgentBuilder()
    await acreate_agent_runtime(
        context=context,
        dependencies=dependencies.model_copy(
            update={
                "tool_registry": FakeToolRegistry(tools=()),
                "mcp_registry": _McpRegistry(servers=_cards(servers)),
                "capability_activation": capability_activation,
                "capability_catalog": capability_catalog,
            }
        ),
        agent_builder=builder,
    )
    return builder.calls[0]


def _names(request: object) -> tuple[str, ...]:
    return tuple(str(getattr(tool, "name", "")) for tool in request.tools)  # type: ignore[attr-defined]


async def _deferred(
    context: AgentRuntimeContext,
    dependencies: RuntimeDependencies,
    *,
    servers: int = 20,
) -> object:
    cards = _cards(servers)
    return await _build(
        context,
        dependencies,
        servers=servers,
        capability_activation=_decision(CapabilityActivationMode.DEFERRED),
        capability_catalog=_catalog(context, cards=cards),
    )


class TestDeferredSuppressesTheCardBlock:
    """A registered bridge removes the enumeration it replaces."""

    async def test_a_registered_bridge_removes_every_card_row(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        request = await _deferred(runtime_context_admin, fake_dependencies)
        prompt = request.system_prompt  # type: ignore[attr-defined]

        assert MCP_SERVER_CARDS_INSTRUCTIONS not in prompt
        assert _CARD_ROW_MARKER not in prompt
        assert "connector_0" not in prompt
        # Guard against a vacuous pass: the bridge really did register.
        assert set(_names(request)) & CapabilityBridgeToolName.reserved_names() == {
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
        }

    async def test_the_replacement_block_takes_the_card_block_s_place(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        request = await _deferred(runtime_context_admin, fake_dependencies)
        prompt = request.system_prompt  # type: ignore[attr-defined]

        assert CAPABILITY_DISCOVERY_INSTRUCTIONS in prompt

    async def test_the_replacement_is_not_a_re_enumeration(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The whole point: deferred prompt size stops tracking connector count.

        The direct path grows monotonically with every authorized server. If
        the replacement listed the servers under a different heading this would
        fail, which is exactly the failure mode it is here to prevent.
        """

        deferred_sizes = {
            servers: len(
                (
                    await _deferred(
                        runtime_context_admin, fake_dependencies, servers=servers
                    )
                ).system_prompt  # type: ignore[attr-defined]
            )
            for servers in (0, 1, 20, 60)
        }
        direct_sizes = [
            len(
                (
                    await _build(
                        runtime_context_admin, fake_dependencies, servers=servers
                    )
                ).system_prompt  # type: ignore[attr-defined]
            )
            for servers in (0, 1, 20, 60)
        ]

        assert len(set(deferred_sizes.values())) == 1
        assert direct_sizes == sorted(direct_sizes)
        assert direct_sizes[0] < direct_sizes[-1]

    async def test_suppression_survives_a_run_with_no_authorized_servers(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The no-servers block is a card block too, and it contradicts the bridge.

        It tells the model to answer "none are available" and not to call
        ``load_mcp_server``. Leaving it in beside a working search tool would be
        an instruction to ignore the tool.
        """

        request = await _deferred(runtime_context_admin, fake_dependencies, servers=0)
        prompt = request.system_prompt  # type: ignore[attr-defined]

        assert NO_MCP_SERVER_CARDS_INSTRUCTIONS not in prompt
        assert CAPABILITY_DISCOVERY_INSTRUCTIONS in prompt


class TestSuppressionRequiresSuccessfulRegistration:
    """The safety boundary: no bridge tool, no suppression. No exceptions."""

    async def test_an_unbindable_catalog_keeps_the_entire_pre_f3_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The minimum bar: a deliberately broken catalog under a deferred posture.

        A catalog with no generation cannot mint a revalidatable reference, so
        the registrar refuses it. The run must land on the untouched pre-F3
        path — cards *and* direct tools — because a suppressed card block with
        no bridge tool would leave the model no route to MCP whatsoever.
        """

        cards = _cards(20)
        request = await _build(
            runtime_context_admin,
            fake_dependencies,
            capability_activation=_decision(CapabilityActivationMode.DEFERRED),
            capability_catalog=_ungenerated(
                _catalog(runtime_context_admin, cards=cards)
            ),
        )
        prompt = request.system_prompt  # type: ignore[attr-defined]
        names = _names(request)

        assert not set(names) & CapabilityBridgeToolName.reserved_names()
        assert MCP_SERVER_CARDS_INSTRUCTIONS in prompt
        assert _CARD_ROW_MARKER in prompt
        assert "connector_0" in prompt
        assert CAPABILITY_DISCOVERY_INSTRUCTIONS not in prompt
        for tool_name in _DIRECT_MCP_TOOLS:
            assert tool_name in names
        assert (
            prompt
            == (await _build(runtime_context_admin, fake_dependencies)).system_prompt
        )  # type: ignore[attr-defined]

    async def test_a_registrar_failure_keeps_the_entire_pre_f3_surface(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def explode(**_kwargs: object) -> tuple[object, ...]:
            raise RuntimeError("postgres://secret-host/capability_catalog")

        monkeypatch.setattr(
            CapabilityBridgeRegistrar,
            "registrations_for",
            staticmethod(explode),
        )

        request = await _deferred(runtime_context_admin, fake_dependencies)
        prompt = request.system_prompt  # type: ignore[attr-defined]

        assert not set(_names(request)) & CapabilityBridgeToolName.reserved_names()
        assert MCP_SERVER_CARDS_INSTRUCTIONS in prompt
        assert CAPABILITY_DISCOVERY_INSTRUCTIONS not in prompt

    async def test_a_registrar_that_returns_nothing_keeps_the_card_block(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Registering zero tools is not a failure, and must not suppress either.

        This is the case a boolean "the posture is deferred" gate would get
        wrong. Reading the composed surface gets it right for free.
        """

        monkeypatch.setattr(
            CapabilityBridgeRegistrar,
            "registrations_for",
            staticmethod(lambda **_kwargs: ()),
        )

        request = await _deferred(runtime_context_admin, fake_dependencies)
        prompt = request.system_prompt  # type: ignore[attr-defined]

        assert not set(_names(request)) & CapabilityBridgeToolName.reserved_names()
        assert MCP_SERVER_CARDS_INSTRUCTIONS in prompt
        assert CAPABILITY_DISCOVERY_INSTRUCTIONS not in prompt

    @pytest.mark.parametrize(
        "activation",
        [
            CapabilityActivationMode.DIRECT,
            CapabilityActivationMode.SERVER,
            CapabilityActivationMode.SHADOW,
        ],
    )
    async def test_every_non_deferred_posture_keeps_the_card_block(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
        activation: CapabilityActivationMode,
    ) -> None:
        cards = _cards(20)
        request = await _build(
            runtime_context_admin,
            fake_dependencies,
            capability_activation=_decision(activation),
            capability_catalog=_catalog(runtime_context_admin, cards=cards),
        )

        assert MCP_SERVER_CARDS_INSTRUCTIONS in request.system_prompt  # type: ignore[attr-defined]
        assert CAPABILITY_DISCOVERY_INSTRUCTIONS not in request.system_prompt  # type: ignore[attr-defined]

    async def test_a_feature_mode_ceiling_keeps_the_card_block(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """``shadow`` mode cannot be widened into suppression by asking for deferred."""

        decision = CapabilityActivationResolver().resolve_configured(
            raw_mode=FeatureMode.SHADOW.value,
            raw_activation=CapabilityActivationMode.DEFERRED.value,
        )
        cards = _cards(20)
        request = await _build(
            runtime_context_admin,
            fake_dependencies,
            capability_activation=decision,
            capability_catalog=_catalog(runtime_context_admin, cards=cards),
        )

        assert decision.effective_activation is CapabilityActivationMode.SHADOW
        assert MCP_SERVER_CARDS_INSTRUCTIONS in request.system_prompt  # type: ignore[attr-defined]

    async def test_wrongly_typed_inputs_keep_the_card_block(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        request = await _build(
            runtime_context_admin,
            fake_dependencies,
            capability_activation="deferred",
            capability_catalog={"entries": ["connector_0"]},
        )

        assert MCP_SERVER_CARDS_INSTRUCTIONS in request.system_prompt  # type: ignore[attr-defined]
        assert CAPABILITY_DISCOVERY_INSTRUCTIONS not in request.system_prompt  # type: ignore[attr-defined]

    @pytest.mark.parametrize("supplied", ["activation", "catalog", "neither"])
    async def test_a_missing_input_keeps_the_card_block(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
        supplied: str,
    ) -> None:
        cards = _cards(20)
        request = await _build(
            runtime_context_admin,
            fake_dependencies,
            capability_activation=(
                _decision(CapabilityActivationMode.DEFERRED)
                if supplied == "activation"
                else None
            ),
            capability_catalog=(
                _catalog(runtime_context_admin, cards=cards)
                if supplied == "catalog"
                else None
            ),
        )

        assert MCP_SERVER_CARDS_INSTRUCTIONS in request.system_prompt  # type: ignore[attr-defined]


class TestFeatureOffPromptParity:
    """With F3 dark the composed prompt is byte-identical to the pre-F3 one."""

    async def test_the_dark_prompt_is_unchanged_by_this_lane(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """W1's parity argument, extended from the tool surface to the prompt.

        The dark path composes ``capability_bridge_tools`` as an empty set, which
        is the same input the suppression helpers receive by default, so every
        prompt helper takes the branch it took before F3 existed.
        """

        dark = await _build(runtime_context_admin, fake_dependencies)

        assert MCP_SERVER_CARDS_INSTRUCTIONS in dark.system_prompt  # type: ignore[attr-defined]
        assert CAPABILITY_DISCOVERY_INSTRUCTIONS not in dark.system_prompt  # type: ignore[attr-defined]
        assert _CARD_ROW_MARKER in dark.system_prompt  # type: ignore[attr-defined]

    async def test_no_prompt_fragment_is_added_while_f3_is_dark(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies.model_copy(
                update={"mcp_registry": _McpRegistry(servers=_cards(3))}
            ),
            agent_builder=CapturingAgentBuilder(),
        )
        plan = harness.prompt_assembly_plan
        assert plan is not None

        fragment_ids = [fragment.fragment_id for fragment in plan.fragments]
        assert "16_capability_discovery_protocol" not in fragment_ids
        assert "20_mcp_cards" in fragment_ids


class TestDirectMcpToolsSurviveSuppression:
    """Search and describe replace the enumeration, not the tools."""

    async def test_every_direct_mcp_tool_is_still_registered(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        names = _names(await _deferred(runtime_context_admin, fake_dependencies))

        for tool_name in _DIRECT_MCP_TOOLS:
            assert tool_name in names

    async def test_invoke_capability_is_absent_so_call_mcp_tool_is_load_bearing(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Pins *why* ``call_mcp_tool`` may not be suppressed yet.

        The factory threads no executor or revalidation, so the registrar never
        offers ``invoke_capability``. Until it does, ``call_mcp_tool`` is the
        only execution route a deferred run has, and removing it would leave a
        model that can search and describe but never act.
        """

        names = _names(await _deferred(runtime_context_admin, fake_dependencies))

        assert CapabilityBridgeToolName.INVOKE_CAPABILITY.value not in names
        assert McpValues.ToolName.CALL_MCP_TOOL in names

    async def test_the_replacement_block_names_the_tools_it_leaves_in_place(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """A route the model is not told about is a route it does not have.

        ``auth_mcp`` matters most: the cards carried ``auth_state`` and the
        catalog has no equivalent, so this sentence is the only thing standing
        between an unauthenticated server and a model that concludes the
        connector is unavailable.
        """

        prompt = (
            await _deferred(runtime_context_admin, fake_dependencies)
        ).system_prompt  # type: ignore[attr-defined]

        for tool_name in (
            *_DIRECT_MCP_TOOLS,
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
        ):
            assert tool_name in prompt
