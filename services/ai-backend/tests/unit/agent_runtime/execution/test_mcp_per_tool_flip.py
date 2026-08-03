"""The P2-8 registration flip: one model tool per real MCP tool, behind a flag.

This is the increment where the PDP, the five middleware stages, the connector
resolver and the credential seam are exercised together for the first time, so
these tests are deliberately about *composition*, not about any one stage:

* **Flag OFF is the untouched path.** The default is off, and with it the
  composed model-visible surface is byte-identical to today's — same tools, same
  order, ``call_mcp_tool`` still present — whether or not a credential plane is
  supplied.
* **ON never half-registers.** No credential plane, no MCP seam, a load that
  raised, or a load that produced nothing all keep the legacy gateway. A run
  that could still reach its connectors through the proxy must not be left with
  an empty connector surface because a flag was on.
* **The security properties survive composition.** A trusted READ auto-runs; a
  WRITE parks on the gate and executes exactly once after approval; a declined
  write never dispatches. These are the P1a matrix invariants, now proven
  through the whole POLICY → EXEC_POLICY → OBSERVE → ERROR_MAP → CITATIONS
  stack rather than against the POLICY stage alone.
* **A connector failure is a result, not a run failure.** ERROR_MAP raises its
  typed ``McpToolCallError`` so EXEC_POLICY can decide about a retry; the
  registration site is what turns it back into a tool result the model can route
  around. Without that, one 4xx from one connector fails the run.
* **A connector cannot shadow a native tool.** The reserved-name set passed to
  the source includes both the factory's own tools and the framework builtins
  the factory never sees.

Everything is hermetic: fake directory, fake credential provider, fake MCP
client, recording interrupt handler. No network, no keychain, no live server.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import pytest
from langchain_core.tools import BaseTool, StructuredTool

from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpLoadErrorCode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.client import (
    McpConnectionError,
    McpRequestRejectedError,
)
from agent_runtime.capabilities.mcp.connection import McpServerConnectionConfig
from agent_runtime.capabilities.mcp.middleware.error_map_tool import (
    McpToolCallError,
    McpToolErrorEnvelope,
)
from agent_runtime.capabilities.mcp.per_tool_registration import (
    HarnessBuiltinToolNames,
    McpPerToolCollaborators,
    McpPerToolFlag,
    McpPerToolInterrupts,
    McpPerToolRegistrar,
    McpPerToolRegistration,
)
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    Trust,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import (
    _mcp_per_tool_registration,
    _model_tool_schema_revision,
    _model_visible_tools,
    _with_mcp_per_tool_interrupts,
)
from agent_runtime.execution.filesystem_bypass import (
    FilesystemBypassDecision,
    FilesystemBypassMode,
)
from agent_runtime.execution.tool_surface import ModelToolOwner
from agent_runtime.observability.context_origin import context_origin_of
from agent_runtime.capabilities.skills.sources import SkillSourceConfig
from agent_runtime.surfaces_v2.gate import ToolAccessGate
from tests.unit.fakes import (
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)

_SERVER = "linear"
_SERVER_ID = "srv_linear"
_READ_TOOL = "get_issues"
_WRITE_TOOL = "create_issue"


class StubMcpTool(BaseTool):
    """A discovered MCP tool at the shape ``langchain-mcp-adapters`` returns it.

    Two details are load-bearing and both are the adapter's real behaviour:
    ``response_format="content_and_artifact"`` (so every wrapper must return a
    two-tuple) and the untrusted tri-state hints flattened onto ``metadata``
    (which is what the source ingests to derive ``action``).
    """

    name: str = _READ_TOOL
    description: str = "A stubbed MCP tool."
    response_format: str = "content_and_artifact"
    result: Any = ("issues", {"raw": [1]})
    raises: Any = None
    calls: list[dict[str, Any]] = []

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("MCP tools are driven asynchronously.")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        del args
        self.calls.append(dict(kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result


class StubAuth(httpx.Auth):
    """Stand-in for the rotating ``RefreshingBearerAuth`` (never invoked)."""


class FakeDirectory:
    """Credential-plane endpoint lookup for one known server."""

    def __init__(self, url: str = "https://mcp.linear.app/mcp") -> None:
        self._url = url
        self.requested: list[str] = []

    async def connection_for(self, server_id: str) -> McpServerConnectionConfig:
        self.requested.append(server_id)
        return McpServerConnectionConfig(url=self._url, transport=McpTransport.HTTP)


class FakeCredentials:
    """P0 ``CredentialProvider`` fake; records every server it was asked for."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def auth_for(self, server_id: str) -> httpx.Auth:
        self.requested.append(server_id)
        return StubAuth()


class FakeClient:
    """A fake ``MultiServerMCPClient``: canned tools (or a raise) per server."""

    def __init__(
        self,
        tools: Sequence[BaseTool],
        *,
        error: BaseException | None = None,
    ) -> None:
        self._tools = list(tools)
        self._error = error

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        del server_name
        if self._error is not None:
            raise self._error
        return list(self._tools)


class FakeClientFactory:
    """Hands back the fake client instead of opening anything."""

    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def create(self, connections: Mapping[str, Any]) -> FakeClient:
        del connections
        return self._client


class RaisingClientFactory:
    """A credential plane whose client construction is itself broken."""

    def create(self, connections: Mapping[str, Any]) -> FakeClient:
        del connections
        raise RuntimeError("client construction blew up")


class StubAuthSessionCreator:
    """``ToolAccessGate``'s OAuth collaborator, which the write gate never uses."""

    async def create_auth_session(self, **_kwargs: object) -> object:
        raise AssertionError("the write-approval gate never opens an auth session")


class RecordingInterrupt:
    """Records the interrupt payloads and returns a canned resume value."""

    def __init__(self, resume: object) -> None:
        self.resume = resume
        self.payloads: list[dict[str, Any]] = []

    @property
    def calls(self) -> int:
        return len(self.payloads)

    def __call__(self, payload: dict[str, Any]) -> object:
        self.payloads.append(payload)
        return self.resume


class RecordingSink:
    """The worker's stream seam, reduced to the one method the factory calls."""

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    def publish_connector_resolver(self, run_id: str, resolver: object) -> None:
        self.published.append((run_id, resolver))


class FakeProvider:
    """One MCP provider exposing a single card, as ``DynamicMcpRegistry`` wants."""

    def __init__(self, cards: Sequence[McpServerCard]) -> None:
        self._cards = tuple(cards)

    async def list_server_cards(self) -> Sequence[McpServerCard]:
        return self._cards

    def create_client(self, card: McpServerCard) -> object:
        raise AssertionError("per-tool registration never builds a proxy client")

    async def create_auth_session(self, **_kwargs: object) -> object:
        raise AssertionError("composing the tool surface must not start OAuth")


class FakeRegistry:
    """The MCP seam the factory duck-probes: providers plus ``resolve_server``."""

    def __init__(self, providers: Sequence[object]) -> None:
        self.providers = tuple(providers)

    async def list_available_servers(self, _context: object) -> tuple[object, ...]:
        return ()

    async def resolve_server(self, _name: str) -> object:
        raise AssertionError("per-tool registration never resolves through the proxy")


class RegistryWithoutMcpSeam:
    """A registry that offers no ``resolve_server`` — no MCP tools at all."""

    providers: tuple[object, ...] = ()

    async def list_available_servers(self, _context: object) -> tuple[object, ...]:
        return ()


class PerToolFixtureMixin:
    """Cards, contexts, collaborators, and the assembled registration."""

    @pytest.fixture(autouse=True)
    def _annotations_registry(self):
        """Bind the per-run MCP annotations registry for the whole test.

        Load-bearing, and for the *whole* test rather than just the load: the
        source captures each tool's untrusted tri-state hints into this registry
        at ``load()``, and the POLICY stage re-derives the descriptor from it on
        **every call**. Unbound, ``readOnlyHint: true`` is invisible and every
        tool falls back to the fail-closed WRITE — which is the correct default
        but the wrong test, since it would make "a trusted read auto-runs"
        unobservable. The worker binds the same registry per run.
        """

        token = McpToolAnnotationsRegistry.bind_for_run({})
        try:
            yield
        finally:
            McpToolAnnotationsRegistry.unbind(token)

    RUN_ID = "run_p28"
    TRACE_ID = "trace_p28"
    APPROVED = {"decision": "approved"}
    DECLINED = {"decision": "rejected"}

    def card(self, *, auth_state: McpAuthState = McpAuthState.AUTHENTICATED):
        return McpServerCard(
            name=_SERVER,
            server_id=_SERVER_ID,
            short_description="Linear MCP connector.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=auth_state,
            required_scopes=frozenset(),
            health=McpServerHealth.HEALTHY,
            load_cost=10,
            enabled=True,
        )

    def context(self, *, bypass: bool = False) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_p28",
            org_id="org_p28",
            roles={"employee"},
            permission_scopes=frozenset(),
            connector_scopes={},
            paused_connectors=frozenset(),
            connector_access_modes={},
            filesystem_bypass=FilesystemBypassDecision(
                master_enabled=True,
                mode=(
                    FilesystemBypassMode.BYPASS
                    if bypass
                    else FilesystemBypassMode.MANUAL
                ),
            ),
            user_policies_json={},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-4o-mini",
                max_input_tokens=4096,
                timeout_seconds=30,
                temperature=0.0,
            ),
            run_id=self.RUN_ID,
            trace_id=self.TRACE_ID,
        )

    def read_tool(self, *, name: str = _READ_TOOL, **overrides: Any) -> StubMcpTool:
        """A tool the connector advertises as read-only (⇒ READ + TRUSTED)."""

        return StubMcpTool(
            name=name, metadata={"readOnlyHint": True}, calls=[], **overrides
        )

    def write_tool(self, *, name: str = _WRITE_TOOL, **overrides: Any) -> StubMcpTool:
        """A tool with no hint at all — the fail-closed WRITE."""

        return StubMcpTool(name=name, calls=[], **overrides)

    def gate(
        self, interrupt: RecordingInterrupt, context: AgentRuntimeContext
    ) -> ToolAccessGate:
        return ToolAccessGate(
            auth_session_creator=StubAuthSessionCreator(),
            runtime_context=context,
            interrupt_handler=interrupt,
            classifier=None,
        )

    def collaborators(
        self,
        tools: Sequence[BaseTool],
        *,
        error: BaseException | None = None,
        factory: object | None = None,
    ) -> McpPerToolCollaborators:
        return McpPerToolCollaborators(
            directory=FakeDirectory(),  # type: ignore[arg-type]
            credentials=FakeCredentials(),  # type: ignore[arg-type]
            client_factory=(  # type: ignore[arg-type]
                factory
                if factory is not None
                else FakeClientFactory(FakeClient(tools, error=error))
            ),
        )

    async def build(
        self,
        tools: Sequence[BaseTool],
        *,
        context: AgentRuntimeContext | None = None,
        collaborators: McpPerToolCollaborators | None = None,
        gate: ToolAccessGate | None = None,
        registry: object | None = None,
        reserved_names: frozenset[str] = frozenset(),
        enabled: bool = True,
        error: BaseException | None = None,
        factory: object | None = None,
    ) -> McpPerToolRegistration | None:
        """Run the flip with the flag forced on unless a test says otherwise."""

        resolved_context = context if context is not None else self.context()
        return await McpPerToolRegistrar.build(
            runtime_context=resolved_context,
            mcp_registry=(
                registry
                if registry is not None
                else FakeRegistry((FakeProvider((self.card(),)),))
            ),
            collaborators=(
                collaborators
                if collaborators is not None
                else self.collaborators(tools, error=error, factory=factory)
            ),
            gate=gate,
            reserved_names=reserved_names,
            environ={McpPerToolFlag.ENV_VAR: "true" if enabled else "false"},
        )

    def names(self, registration: McpPerToolRegistration | None) -> tuple[str, ...]:
        assert registration is not None
        return tuple(tool.name for tool in registration.tools)

    def tool_named(self, registration: McpPerToolRegistration | None, name: str):
        assert registration is not None
        return next(tool for tool in registration.tools if tool.name == name)

    def content(self, result: Any) -> Any:
        """The content half of a ``content_and_artifact`` return."""

        assert isinstance(result, tuple) and len(result) == 2, result
        return result[0]


class TestFlagDefault:
    def test_the_flag_is_on_when_unset(self) -> None:
        """The proxy credential plane removed the reason it defaulted off.

        Direct-connect needed a vendor bearer this process could not obtain, so
        "on" would have registered a connector surface that could not answer.
        Routing the library through ``backend``'s proxy means there is no
        credential left to be missing.
        """

        assert McpPerToolFlag.enabled({}) is True

    def test_it_can_still_be_turned_off(self) -> None:
        """The rollback lever is the whole point of keeping the flag."""

        assert McpPerToolFlag.enabled({McpPerToolFlag.ENV_VAR: "false"}) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
    def test_explicit_truthy_values_enable_it(self, raw: str) -> None:
        assert McpPerToolFlag.enabled({McpPerToolFlag.ENV_VAR: raw}) is True

    @pytest.mark.parametrize("raw", ["", "false", "0", "no", "off", "maybe"])
    def test_everything_else_reads_off(self, raw: str) -> None:
        assert McpPerToolFlag.enabled({McpPerToolFlag.ENV_VAR: raw}) is False


class TestTheFlipDeclines(PerToolFixtureMixin):
    """Every "cannot register safely" road ends on the legacy gateway."""

    async def test_flag_off_registers_nothing(self) -> None:
        assert await self.build([self.read_tool()], enabled=False) is None

    async def test_absent_credential_plane_falls_back(self) -> None:
        # The desktop provider is blocked on a token writer, so this is the
        # production state today: the flag may be on and there is still nothing
        # to connect with. Falling back beats registering a broken toolset.
        assert (
            await McpPerToolRegistrar.build(
                runtime_context=self.context(),
                mcp_registry=FakeRegistry((FakeProvider((self.card(),)),)),
                collaborators=None,
                gate=None,
                reserved_names=frozenset(),
                environ={McpPerToolFlag.ENV_VAR: "true"},
            )
            is None
        )

    async def test_registry_without_the_mcp_seam_falls_back(self) -> None:
        assert (
            await self.build([self.read_tool()], registry=RegistryWithoutMcpSeam())
            is None
        )

    async def test_a_load_that_raises_falls_back(self) -> None:
        assert await self.build([], factory=RaisingClientFactory()) is None

    async def test_a_load_that_registers_nothing_falls_back(self) -> None:
        # Every authorized connector failed to answer: the source records typed
        # failures and returns no pairs, and a connector surface with no tools
        # is strictly worse than the gateway it replaced.
        assert await self.build([], error=McpConnectionError("down")) is None


class TestPerToolRegistration(PerToolFixtureMixin):
    async def test_one_wrapped_tool_per_discovered_mcp_tool(self) -> None:
        registration = await self.build([self.read_tool(), self.write_tool()])
        assert self.names(registration) == (_READ_TOOL, _WRITE_TOOL)
        assert registration is not None
        # The umbrella name is gone: the payload-decoding gateway has no place
        # in a surface where every tool IS its own capability.
        assert McpValues.ToolName.CALL_MCP_TOOL not in registration.tool_names

    async def test_the_connector_resolver_maps_every_registered_name(self) -> None:
        registration = await self.build([self.read_tool(), self.write_tool()])
        assert registration is not None
        assert registration.resolver.server_for(_READ_TOOL) == _SERVER
        assert registration.resolver.server_for(_WRITE_TOOL) == _SERVER
        # A native step is not an MCP call and must not acquire connector
        # identity from a name that merely looks connector-shaped.
        assert registration.resolver.server_for("notion_search") is None

    async def test_cards_by_urn_is_published_for_the_policy_stage(self) -> None:
        registration = await self.build([self.read_tool()])
        assert registration is not None
        urn = CapabilityUrn.for_mcp(_SERVER, _READ_TOOL)
        assert registration.cards_by_urn[urn].name == _SERVER

    async def test_the_wrapped_tool_keeps_the_raw_tool_surface(self) -> None:
        inner = self.read_tool()
        registration = await self.build([inner])
        wrapped = self.tool_named(registration, _READ_TOOL)
        assert wrapped.description == inner.description
        assert wrapped.args_schema is inner.args_schema
        assert wrapped.response_format == inner.response_format
        assert wrapped.extras == inner.extras


class TestReservedNames(PerToolFixtureMixin):
    async def test_a_connector_cannot_take_a_factory_tool_name(self) -> None:
        registration = await self.build(
            [self.read_tool(name="ask_a_question"), self.write_tool()],
            reserved_names=frozenset({"ask_a_question"}),
        )
        assert self.names(registration) == (_WRITE_TOOL,)
        assert registration is not None
        assert [failure.code for failure in registration.failures] == [
            McpLoadErrorCode.LOCAL_TOOL_COLLISION
        ]

    async def test_a_connector_cannot_take_a_framework_builtin_name(self) -> None:
        # ``write_todos`` is registered by LangChain's middleware, not by the
        # factory, so it is absent from the factory's own name set — which is
        # exactly why the registrar adds the framework's names itself.
        assert "write_todos" in HarnessBuiltinToolNames.NAMES
        registration = await self.build(
            [self.read_tool(name="write_todos"), self.write_tool()],
            reserved_names=frozenset(),
        )
        assert self.names(registration) == (_WRITE_TOOL,)

    async def test_a_shadowed_name_never_enters_the_connector_map(self) -> None:
        # The provenance half: had it registered, the stream seam would stamp
        # MCP provenance onto the builtin's own events.
        registration = await self.build(
            [self.read_tool(name="write_todos"), self.write_tool()]
        )
        assert registration is not None
        assert registration.resolver.server_for("write_todos") is None


class TestComposedGateBehaviour(PerToolFixtureMixin):
    """The P1a matrix invariants, through the whole composed stack."""

    async def test_a_trusted_read_auto_runs(self) -> None:
        inner = self.read_tool()
        interrupt = RecordingInterrupt(self.APPROVED)
        context = self.context()
        registration = await self.build(
            [inner], context=context, gate=self.gate(interrupt, context)
        )
        result = await self.tool_named(registration, _READ_TOOL)._arun(team="ENG")
        assert self.content(result) == "issues"
        assert inner.calls == [{"team": "ENG"}]
        assert interrupt.calls == 0

    async def test_a_write_parks_and_executes_once_after_approval(self) -> None:
        inner = self.write_tool()
        interrupt = RecordingInterrupt(self.APPROVED)
        context = self.context()
        registration = await self.build(
            [inner], context=context, gate=self.gate(interrupt, context)
        )
        result = await self.tool_named(registration, _WRITE_TOOL)._arun(title="Ship it")
        assert interrupt.calls == 1
        assert interrupt.payloads[0]["server_name"] == _SERVER
        assert self.content(result) == "issues"
        assert inner.calls == [{"title": "Ship it"}]

    async def test_a_declined_write_never_dispatches(self) -> None:
        inner = self.write_tool()
        interrupt = RecordingInterrupt(self.DECLINED)
        context = self.context()
        registration = await self.build(
            [inner], context=context, gate=self.gate(interrupt, context)
        )
        result = await self.tool_named(registration, _WRITE_TOOL)._arun(title="Nope")
        assert inner.calls == []
        assert self.content(result)["error"]["code"] == (
            McpLoadErrorCode.PERMISSION_DENIED
        )

    async def test_a_write_fails_closed_without_an_approval_channel(self) -> None:
        inner = self.write_tool()
        registration = await self.build([inner], gate=None)
        result = await self.tool_named(registration, _WRITE_TOOL)._arun(title="Nope")
        assert inner.calls == []
        assert self.content(result)["error"]["code"] == (
            McpLoadErrorCode.PERMISSION_DENIED
        )


class TestConnectorFailureBecomesAResult(PerToolFixtureMixin):
    """ERROR_MAP raises; the registration site renders. Both halves are needed."""

    async def test_a_refused_call_returns_a_typed_result_not_an_exception(
        self,
    ) -> None:
        # Without the renderer this exception escapes the tool node and fails the
        # whole run, instead of telling the model what happened and that a retry
        # cannot help.
        inner = self.read_tool(
            raises=McpRequestRejectedError("400 bad request", status_code=400)
        )
        registration = await self.build([inner])
        payload = self.content(
            await self.tool_named(registration, _READ_TOOL)._arun(team="ENG")
        )
        assert payload["code"] == McpLoadErrorCode.MCP_PROTOCOL_ERROR
        assert payload["retryable"] is False
        assert payload["connector"] == _SERVER
        assert payload["tool"] == _READ_TOOL
        # The safe message is authored copy — never the exception's own text.
        assert "400 bad request" not in payload["safe_message"]

    async def test_a_transport_failure_on_a_write_is_never_replayed(self) -> None:
        inner = self.write_tool(raises=McpConnectionError("connection reset"))
        interrupt = RecordingInterrupt(self.APPROVED)
        context = self.context()
        registration = await self.build(
            [inner], context=context, gate=self.gate(interrupt, context)
        )
        payload = self.content(
            await self.tool_named(registration, _WRITE_TOOL)._arun(title="Ship it")
        )
        assert payload["replay_safe"] is False
        # One attempt only: a write that may already have landed is never sent
        # a second time, whatever ``retryable`` advises.
        assert len(inner.calls) == 1

    async def test_a_non_mcp_exception_still_propagates(self) -> None:
        # Only the normalized connector taxonomy becomes a result. A defect must
        # keep the path the runtime's generic tool-error policy already handles.
        inner = self.read_tool(raises=ValueError("a defect, not a connector"))
        registration = await self.build([inner])
        with pytest.raises(ValueError):
            await self.tool_named(registration, _READ_TOOL)._arun(team="ENG")

    async def test_the_rendered_result_matches_the_declared_return_shape(
        self,
    ) -> None:
        # A ``content_and_artifact`` tool whose refusal is not a two-tuple makes
        # ``BaseTool.arun`` raise — a failure that crashes the run it was
        # reporting on.
        inner = self.read_tool(raises=McpConnectionError("down"))
        registration = await self.build([inner])
        result = await self.tool_named(registration, _READ_TOOL)._arun(team="ENG")
        assert isinstance(result, tuple) and result[1] is None

    async def test_the_rendered_payload_is_the_errors_own_safe_message(self) -> None:
        # ``str(exc)`` IS the safe message, so an outer sanitizer has nothing to
        # strip — and the rendered result says exactly the same thing the raised
        # error did, never the exception's own text.
        inner = self.read_tool(raises=McpConnectionError("internal detail"))
        registration = await self.build([inner])
        payload = self.content(
            await self.tool_named(registration, _READ_TOOL)._arun(team="ENG")
        )
        envelope = McpToolErrorEnvelope(**payload)
        assert str(McpToolCallError(envelope)) == payload["safe_message"]
        assert "internal detail" not in payload["safe_message"]


class TestDescriptorDrivenInterrupts(PerToolFixtureMixin):
    """The single ``call_mcp_tool`` key becomes one entry per eligible tool."""

    def descriptor(self, *, action: Action, trust: Trust) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            urn=CapabilityUrn.for_mcp(_SERVER, _WRITE_TOOL),
            action=action,
            trust=trust,
            source="mcp",
            connector_state=ConnectorState.LIVE,
        )

    @pytest.mark.parametrize(
        ("action", "trust", "eligible"),
        [
            (Action.READ, Trust.TRUSTED, False),
            (Action.READ, Trust.UNTRUSTED, True),
            (Action.WRITE, Trust.TRUSTED, True),
            (Action.WRITE, Trust.UNTRUSTED, True),
            (Action.DESTRUCTIVE, Trust.TRUSTED, True),
        ],
    )
    def test_eligibility_is_action_by_trust(
        self, action: Action, trust: Trust, eligible: bool
    ) -> None:
        assert (
            McpPerToolInterrupts.eligible(self.descriptor(action=action, trust=trust))
            is eligible
        )

    async def test_no_entries_when_the_policy_stage_can_park(self) -> None:
        # Both gates would fire for one write, which is two approval cards for
        # one action. The in-tool gate is the one that knows the PDP decision.
        interrupt = RecordingInterrupt(self.APPROVED)
        context = self.context()
        registration = await self.build(
            [self.read_tool(), self.write_tool()],
            context=context,
            gate=self.gate(interrupt, context),
        )
        assert registration is not None
        assert dict(registration.interrupt_on) == {}

    async def test_eligible_names_are_declared_when_there_is_no_gate(self) -> None:
        # The non-OAuth connector case: today its writes are refused outright
        # ("approval is not available for this run"). Declared here, they park.
        registration = await self.build(
            [self.read_tool(), self.write_tool()], gate=None
        )
        assert registration is not None
        declared = dict(registration.interrupt_on)
        assert set(declared) == {_WRITE_TOOL}
        assert declared[_WRITE_TOOL] == {
            "allowed_decisions": ["approve", "edit", "reject"]
        }
        assert McpValues.ToolName.CALL_MCP_TOOL not in declared

    def test_merging_keeps_the_policy_entries_and_copies_when_absent(self) -> None:
        legacy = {"call_mcp_tool": {"allowed_decisions": ["approve"]}}
        assert _with_mcp_per_tool_interrupts(legacy, None) == legacy
        assert _with_mcp_per_tool_interrupts(legacy, None) is not legacy


def _tool(name: str) -> StructuredTool:
    async def invoke(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        coroutine=invoke, name=name, description=f"{name} test tool"
    )


class TestComposedFactorySurface(PerToolFixtureMixin):
    """What the factory actually hands the graph, on each side of the flag."""

    def surface(
        self, registration: McpPerToolRegistration | None
    ) -> tuple[tuple[str, ...], str]:
        tools = _model_visible_tools(
            tools=(_tool("web_search"),),
            mcp_registry=FakeRegistry((FakeProvider((self.card(),)),)),
            skill_registry=None,
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            runtime_context=self.context(),
            mcp_per_tool=registration,
        )
        names = tuple(str(getattr(tool, "name", "")) for tool in tools)
        return names, _model_tool_schema_revision(tools)

    def test_flag_off_composes_the_untouched_legacy_surface(self) -> None:
        names, _digest = self.surface(None)
        assert names == (
            "web_search",
            "load_mcp_server",
            "call_mcp_tool",
            "auth_mcp",
            "ask_a_question",
            "list_connected_servers",
            "suggest_mcp_connector",
        )

    async def test_the_flip_replaces_the_umbrella_with_the_real_tools(self) -> None:
        registration = await self.build([self.read_tool(), self.write_tool()])
        names, _digest = self.surface(registration)
        assert names == (
            "web_search",
            "load_mcp_server",
            _READ_TOOL,
            _WRITE_TOOL,
            "auth_mcp",
            "ask_a_question",
            "list_connected_servers",
            "suggest_mcp_connector",
        )
        assert "call_mcp_tool" not in names

    async def test_every_registered_mcp_tool_declares_its_owner(self) -> None:
        registration = await self.build([self.read_tool()])
        tools = _model_visible_tools(
            tools=(),
            mcp_registry=FakeRegistry((FakeProvider((self.card(),)),)),
            skill_registry=None,
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            runtime_context=self.context(),
            mcp_per_tool=registration,
        )
        origins = {
            str(getattr(tool, "name", "")): context_origin_of(tool) for tool in tools
        }
        # The occupancy ledger reports against these declarations, so a
        # per-tool connector tool has to arrive owned exactly like the umbrella
        # tool it replaced — otherwise the flip silently untracks the whole MCP
        # tool block's resident context cost.
        assert origins[_READ_TOOL] is not None
        assert origins[_READ_TOOL].owner == ModelToolOwner.MCP.value
        assert origins[_READ_TOOL].owner == origins["load_mcp_server"].owner


class TestFactoryWiring(PerToolFixtureMixin):
    """The factory's own seam: flag read from the environment, resolver published."""

    def dependencies(
        self,
        *,
        collaborators: object | None,
        sink: object | None,
    ) -> RuntimeDependencies:
        return RuntimeDependencies(
            tool_registry=FakeToolRegistry(tools=()),
            mcp_registry=FakeRegistry((FakeProvider((self.card(),)),)),
            skill_source_config=SkillSourceConfig(roots=("skills",)),
            memory_backend_factory=FakeMemoryBackendFactory(),
            subagent_catalog=FakeSubagentCatalog(),
            mcp_per_tool_collaborators=collaborators,
            mcp_connector_resolver_sink=sink,
        )

    async def test_an_unset_environment_registers_the_per_tool_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset is the shape production runs in — nothing sets this var."""

        monkeypatch.delenv(McpPerToolFlag.ENV_VAR, raising=False)
        assert (
            await _mcp_per_tool_registration(
                runtime_context=self.context(),
                runtime_dependencies=self.dependencies(
                    collaborators=self.collaborators([self.read_tool()]), sink=None
                ),
                tools=(),
            )
            is not None
        )

    async def test_the_flag_is_read_from_the_process_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Turning it off must still reach the factory, not just the reader.

        A rollback lever that the flag class honours but the factory ignores is
        worse than no lever: it reports a mitigation that never applied.
        """

        monkeypatch.setenv(McpPerToolFlag.ENV_VAR, "false")
        assert (
            await _mcp_per_tool_registration(
                runtime_context=self.context(),
                runtime_dependencies=self.dependencies(
                    collaborators=self.collaborators([self.read_tool()]), sink=None
                ),
                tools=(),
            )
            is None
        )

    async def test_a_wrongly_typed_credential_plane_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(McpPerToolFlag.ENV_VAR, "true")
        assert (
            await _mcp_per_tool_registration(
                runtime_context=self.context(),
                runtime_dependencies=self.dependencies(
                    collaborators=object(), sink=None
                ),
                tools=(),
            )
            is None
        )

    async def test_the_resolver_is_published_to_the_stream_seam(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(McpPerToolFlag.ENV_VAR, "true")
        sink = RecordingSink()
        context = self.context()
        registration = await _mcp_per_tool_registration(
            runtime_context=context,
            runtime_dependencies=self.dependencies(
                collaborators=self.collaborators([self.read_tool()]), sink=sink
            ),
            tools=(),
        )
        assert registration is not None
        assert len(sink.published) == 1
        run_id, resolver = sink.published[0]
        assert run_id == context.run_id
        assert resolver.server_for(_READ_TOOL) == _SERVER  # type: ignore[attr-defined]

    async def test_nothing_is_published_when_the_flip_declines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(McpPerToolFlag.ENV_VAR, "true")
        sink = RecordingSink()
        assert (
            await _mcp_per_tool_registration(
                runtime_context=self.context(),
                runtime_dependencies=self.dependencies(collaborators=None, sink=sink),
                tools=(),
            )
            is None
        )
        assert sink.published == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
