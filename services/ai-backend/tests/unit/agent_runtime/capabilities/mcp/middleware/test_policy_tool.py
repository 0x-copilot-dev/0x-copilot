"""Unit tests for the P2-4 POLICY middleware stage — the per-tool security seam.

``PolicyToolMiddleware`` is the point at which per-tool registration either keeps
or loses every guarantee the ``call_mcp_tool`` gateway used to provide, so these
tests are deliberately adversarial about the four ways it could fail silently:

* **A refusal that still dispatches.** Every DENY / decline / fail-closed test
  asserts the inner tool's call count is exactly ``0`` — not merely that the
  return value looks like an error. A wrapper that refuses *after* calling the
  connector has already made the external change.
* **A GATE that runs twice, or not at all.** The approve path asserts one
  interrupt and exactly one inner call (park + resume in the SAME run); the
  parked pass (where the interrupt raises, as LangGraph's really does) asserts
  the inner tool is never reached; and the approval id is asserted stable across
  both passes, because the record joins on it.
* **A fail-closed rule that is really a blanket refusal.** ``gate=None`` must
  refuse a GATE and still allow an ALLOW; a rule that refuses everything would
  pass a naive fail-closed test while breaking every read.
* **A wrapper that is not actually transparent.** Schema identity is asserted
  field by field, and — because ``langchain-mcp-adapters`` builds every MCP tool
  with ``response_format="content_and_artifact"`` — the artifact is asserted to
  survive the wrapper, and the refusal is asserted not to violate that two-tuple
  contract (which would raise ``ValueError`` and end the run instead of refusing).

Everything is hermetic: no network, no LangGraph, no live MCP server. The PDP,
the P1b descriptor derivation and the real ``ToolAccessGate`` are exercised for
real; only the interrupt handler, the connector call and the auth-session
creator are faked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpLoadErrorCode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.descriptor_source import (
    McpCapabilityDescriptorSource,
)
from agent_runtime.capabilities.mcp.middleware.policy_tool import (
    PolicyCallScope,
    PolicyStageMessages,
    PolicyToolMiddleware,
)
from agent_runtime.capabilities.mcp.tool_naming import McpToolName
from agent_runtime.capabilities.policy.contracts import (
    CapabilityDescriptor,
    MiddlewareStage,
)
from agent_runtime.capabilities.policy.decisions import RunDecisionLedgers
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ConnectorAccessMode,
    ModelConfig,
)
from agent_runtime.execution.filesystem_bypass import (
    FilesystemBypassDecision,
    FilesystemBypassMode,
)
from agent_runtime.surfaces_v2.gate import ToolAccessGate


class IssueArgs(BaseModel):
    """The wrapped tool's argument schema — propagated by identity, not by copy."""

    team: str = "ENG"


class ParkedRun(Exception):
    """Stand-in for ``langgraph.errors.GraphInterrupt`` on the parked pass.

    The real ``interrupt()`` raises out of the tool node the first time it is
    reached and only *returns* the stored decision when the node re-executes on
    resume. Modelling that with a plain exception keeps these tests free of
    LangGraph while still proving the property that matters: on the pass that
    parks, nothing dispatches.
    """


class RecordingMcpTool(BaseTool):
    """The discovered MCP tool the stage wraps, at the adapters' shape.

    ``langchain-mcp-adapters`` returns a ``StructuredTool`` with
    ``response_format="content_and_artifact"``, so the fake returns a two-tuple
    by default. It records every invocation, which is what the DENY / decline /
    fail-closed tests assert against — "the connector was never called" is the
    property, and only a call count can state it.
    """

    name: str = "list_issues"
    description: str = "List issues from the connector."
    args_schema: type[BaseModel] = IssueArgs
    response_format: str = "content_and_artifact"
    result: Any = ("issues", {"raw": [1, 2]})

    calls: list[dict[str, Any]] = []

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("The policed MCP tool must never run synchronously.")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        del args
        self.calls.append(dict(kwargs))
        return self.result


class RecordingInterrupt:
    """Records the interrupt payloads and returns a canned resume value."""

    def __init__(self, resume: object) -> None:
        self.resume = resume
        self.payloads: list[dict[str, Any]] = []

    @property
    def calls(self) -> int:
        """How many times the run parked on this handler."""

        return len(self.payloads)

    def __call__(self, payload: dict[str, Any]) -> object:
        self.payloads.append(payload)
        if isinstance(self.resume, BaseException):
            raise self.resume
        return self.resume


class StubAuthSessionCreator:
    """``ToolAccessGate``'s OAuth collaborator, which the write gate never uses.

    ``park_for_approval`` deliberately creates no auth session — a write approval
    is "may this effect run", not "connect this server" — so reaching this stub
    at all would itself be the defect.
    """

    async def create_auth_session(self, **kwargs: Any) -> object:
        raise AssertionError("The write gate must not create an auth session.")


@dataclass(frozen=True)
class WrappedTool:
    """One wrapped tool plus the fakes the assertions read."""

    tool: BaseTool
    inner: RecordingMcpTool
    interrupt: RecordingInterrupt


class PolicyToolFixtureMixin:
    """Cards, contexts, descriptors and wrapping for the policy-stage tests."""

    SERVER = "linear"
    RUN_ID = "run_p24"
    TRACE_ID = "trace_p24"
    #: Curated-catalog tools for ``linear`` (``actions/catalog_data/linear.json``):
    #: a trusted READ that auto-runs, a WRITE that gates, a DESTRUCTIVE that gates.
    READ_TOOL = "list_issues"
    WRITE_TOOL = "create_issue"
    DESTRUCTIVE_TOOL = "delete_issue"

    APPROVED: Mapping[str, str] = {"decision": "approved"}
    DECLINED: Mapping[str, str] = {"decision": "rejected"}

    def card(
        self,
        *,
        auth_state: McpAuthState = McpAuthState.AUTHENTICATED,
        required_scopes: frozenset[str] = frozenset(),
        allowed_org_ids: frozenset[str] = frozenset(),
        enabled: bool = True,
    ) -> McpServerCard:
        return McpServerCard(
            name=self.SERVER,
            server_id="srv_linear",
            display_name="Linear",
            short_description="Linear MCP connector.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=auth_state,
            required_scopes=required_scopes,
            health=McpServerHealth.HEALTHY,
            load_cost=10,
            enabled=enabled,
            access_mode=ConnectorAccessMode.READ,
            allowed_org_ids=allowed_org_ids,
        )

    def context(
        self,
        *,
        permission_scopes: frozenset[str] = frozenset(),
        paused_connectors: frozenset[str] = frozenset(),
        bypass: bool = False,
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_p24",
            org_id="org_p24",
            roles={"employee"},
            permission_scopes=permission_scopes,
            connector_scopes={},
            paused_connectors=paused_connectors,
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

    def descriptor(
        self,
        *,
        card: McpServerCard,
        tool_name: str,
        context: AgentRuntimeContext,
    ) -> CapabilityDescriptor:
        """The P1b derivation — the same call P2-3's source makes at load time."""

        return McpCapabilityDescriptorSource.describe(
            card=card, server=card.name, tool=tool_name, context=context
        )

    def gate(
        self, interrupt: RecordingInterrupt, context: AgentRuntimeContext
    ) -> ToolAccessGate:
        return ToolAccessGate(
            auth_session_creator=StubAuthSessionCreator(),
            runtime_context=context,
            interrupt_handler=interrupt,
            classifier=None,
        )

    def build(
        self,
        *,
        tool_name: str = READ_TOOL,
        card: McpServerCard | None = None,
        context: AgentRuntimeContext | None = None,
        resume: object = None,
        with_gate: bool = True,
        cards_by_urn: Mapping[str, McpServerCard] | None = None,
        descriptor_tool: str | None = None,
        response_format: str = "content_and_artifact",
    ) -> WrappedTool:
        """Wrap one recording tool through the real middleware."""

        resolved_card = card if card is not None else self.card()
        resolved_context = context if context is not None else self.context()
        inner = RecordingMcpTool(
            name=tool_name,
            response_format=response_format,
            calls=[],
        )
        descriptor = self.descriptor(
            card=resolved_card,
            tool_name=descriptor_tool or tool_name,
            context=resolved_context,
        )
        interrupt = RecordingInterrupt(
            self.APPROVED if resume is None else resume,
        )
        middleware = PolicyToolMiddleware(
            runtime_context=resolved_context,
            cards_by_urn=(
                {descriptor.urn: resolved_card}
                if cards_by_urn is None
                else cards_by_urn
            ),
            gate=self.gate(interrupt, resolved_context) if with_gate else None,
        )
        return WrappedTool(
            tool=middleware.wrap(inner, descriptor),
            inner=inner,
            interrupt=interrupt,
        )

    #: The injected id a direct ``call`` supplies, standing in for the one
    #: LangChain injects from a ``ToolCall``.
    TOOL_CALL_ID = "call_p24"

    async def call(
        self,
        wrapped: WrappedTool,
        *,
        tool_call_id: str = TOOL_CALL_ID,
        **arguments: Any,
    ) -> Any:
        """Drive the wrapper's async entry point, returning its RAW result.

        Deliberately not ``ainvoke(dict)``: that returns a ``ToolMessage``
        rather than the raw payload these tests assert on. The call scope is
        bound by hand around ``_arun`` because that is exactly what the
        wrapper's own ``arun`` does with the id LangChain hands it — the
        wrapper never adds ``tool_call_id`` to the connector's schema, so it can
        never arrive as a tool argument. :meth:`call_as_tool_call` covers the
        real dispatch path end to end.
        """

        token = PolicyCallScope.bind(tool_call_id)
        try:
            result = await wrapped.tool._arun(  # noqa: SLF001 - the wrapper's own entry point
                **(dict(arguments) or {"team": "ENG"}),
            )
        finally:
            PolicyCallScope.unbind(token)
        # A ``content_and_artifact`` tool returns ``(content, artifact)``;
        # LangChain splits that only when building a ToolMessage. Return the
        # content half so these assertions read the payload, exactly as before.
        if wrapped.tool.response_format == "content_and_artifact" and (
            isinstance(result, tuple) and len(result) == 2
        ):
            return result[0]
        return result

    async def call_as_tool_call(self, wrapped: WrappedTool, **arguments: Any) -> Any:
        """Invoke as the graph does — a ``ToolCall`` — so a ``ToolMessage`` is built."""

        return await wrapped.tool.ainvoke(
            {
                "name": wrapped.tool.name,
                "args": dict(arguments) or {"team": "ENG"},
                "id": "call_p24",
                "type": "tool_call",
            }
        )

    def error_of(self, result: Any) -> Mapping[str, Any]:
        """The typed ``McpLoadError`` payload carried by a refusal."""

        assert isinstance(result, Mapping), (
            f"expected a refusal payload, got {result!r}"
        )
        error = result.get("error")
        assert isinstance(error, Mapping), f"expected a typed error, got {result!r}"
        return error


class TestSchemaIdentity(PolicyToolFixtureMixin):
    def test_wrapper_is_schema_identical(self) -> None:
        wrapped = self.build()
        inner = wrapped.inner
        assert wrapped.tool.name == inner.name
        assert wrapped.tool.description == inner.description
        # The schema is propagated by IDENTITY, never rebuilt (P2-8). A stage
        # that synthesised a replacement would erase every argument of a real
        # connector tool, whose ``args_schema`` is the server's raw JSON-Schema
        # dict rather than a model — and it would fail the composer's identity
        # assertion, which is how that was found.
        assert wrapped.tool.args_schema is inner.args_schema
        assert set(wrapped.tool.tool_call_schema.model_fields) == set(
            inner.tool_call_schema.model_fields
        )
        assert "tool_call_id" not in wrapped.tool.tool_call_schema.model_fields
        assert wrapped.tool.extras == inner.extras
        assert wrapped.tool.metadata == inner.metadata
        assert wrapped.tool.tags == inner.tags
        assert wrapped.tool.return_direct == inner.return_direct
        assert wrapped.tool.response_format == inner.response_format

    def test_annotations_metadata_is_copied_not_shared(self) -> None:
        # The MCP tri-state hints the source ingested ride in ``metadata``; a
        # shared mapping would let a wrapper and its inner edit each other's.
        inner = RecordingMcpTool(
            name=self.READ_TOOL, metadata={"readOnlyHint": True}, calls=[]
        )
        context = self.context()
        card = self.card()
        descriptor = self.descriptor(
            card=card, tool_name=self.READ_TOOL, context=context
        )
        middleware = PolicyToolMiddleware(
            runtime_context=context,
            cards_by_urn={descriptor.urn: card},
            gate=None,
        )
        wrapped = middleware.wrap(inner, descriptor)
        assert wrapped.metadata == {"readOnlyHint": True}
        assert wrapped.metadata is not inner.metadata

    def test_stage_is_policy_and_is_not_constructor_settable(self) -> None:
        middleware = PolicyToolMiddleware(
            runtime_context=self.context(), cards_by_urn={}, gate=None
        )
        assert middleware.stage is MiddlewareStage.POLICY
        with pytest.raises(TypeError):
            PolicyToolMiddleware(
                runtime_context=self.context(),
                cards_by_urn={},
                gate=None,
                stage=MiddlewareStage.OBSERVE,  # type: ignore[call-arg]
            )

    async def test_artifact_survives_the_wrapper(self) -> None:
        # Proves ``response_format`` is propagated: had the wrapper dropped it,
        # LangChain would treat the inner's two-tuple as plain content and the
        # artifact would never reach the ToolMessage.
        wrapped = self.build()
        message = await self.call_as_tool_call(wrapped)
        assert isinstance(message, ToolMessage)
        assert message.content == "issues"
        assert message.artifact == {"raw": [1, 2]}


class TestAllowPath(PolicyToolFixtureMixin):
    async def test_allow_falls_through_to_the_inner_tool(self) -> None:
        wrapped = self.build(tool_name=self.READ_TOOL)
        result = await self.call(wrapped, team="ENG")
        assert len(wrapped.inner.calls) == 1
        assert wrapped.interrupt.calls == 0
        assert result == "issues"

    async def test_allow_forwards_the_model_arguments_unchanged(self) -> None:
        wrapped = self.build(tool_name=self.READ_TOOL)
        await self.call(wrapped, team="PLATFORM")
        assert wrapped.inner.calls == [{"team": "PLATFORM"}]

    async def test_bypass_posture_allows_a_write_without_parking(self) -> None:
        # The P1a matrix invariant, carried over unchanged: BYPASS lifts the
        # write gate, so the wrapper dispatches and never parks.
        wrapped = self.build(
            tool_name=self.WRITE_TOOL, context=self.context(bypass=True)
        )
        await self.call(wrapped, team="ENG")
        assert len(wrapped.inner.calls) == 1
        assert wrapped.interrupt.calls == 0


class TestDenyPath(PolicyToolFixtureMixin):
    async def test_paused_connector_denies_and_never_invokes_the_inner_tool(
        self,
    ) -> None:
        wrapped = self.build(
            tool_name=self.READ_TOOL,
            context=self.context(paused_connectors=frozenset({"srv_linear"})),
        )
        result = await self.call(wrapped, team="ENG")
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.SERVER_UNHEALTHY.value
        assert error["safe_message"] == Messages.Loader.LOAD_FAILED
        assert error["retryable"] is True
        assert wrapped.inner.calls == []

    async def test_missing_scope_denies_permission_and_never_invokes_the_inner_tool(
        self,
    ) -> None:
        wrapped = self.build(
            tool_name=self.READ_TOOL,
            card=self.card(required_scopes=frozenset({"linear:write"})),
            context=self.context(permission_scopes=frozenset()),
        )
        result = await self.call(wrapped, team="ENG")
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == Messages.Loader.UNAUTHORIZED_SERVER
        assert error["retryable"] is False
        assert wrapped.inner.calls == []

    async def test_org_allowlist_miss_denies_permission_without_dispatch(self) -> None:
        wrapped = self.build(
            tool_name=self.READ_TOOL,
            card=self.card(allowed_org_ids=frozenset({"org_someone_else"})),
        )
        result = await self.call(wrapped, team="ENG")
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == Messages.Loader.UNAUTHORIZED_SERVER
        assert wrapped.inner.calls == []

    async def test_refusal_carries_only_safe_typed_material(self) -> None:
        # The refusal is the whole model-visible surface of a denial, so it must
        # name the binding and the correlation id and nothing else — no reason
        # code, no internal detail, no traceback text.
        wrapped = self.build(
            tool_name=self.READ_TOOL,
            card=self.card(required_scopes=frozenset({"linear:write"})),
        )
        result = await self.call(wrapped, team="ENG")
        assert set(result) == {"server_name", "tool_name", "error"}
        assert result["server_name"] == self.SERVER
        assert result["tool_name"] == self.READ_TOOL
        error = self.error_of(result)
        assert set(error) == {
            "code",
            "safe_message",
            "retryable",
            "server_name",
            "correlation_id",
        }
        assert error["correlation_id"] == self.TRACE_ID

    async def test_refusal_keeps_the_content_and_artifact_contract(self) -> None:
        # A refusal rendered as a bare dict under ``content_and_artifact`` makes
        # ``BaseTool.arun`` raise ValueError — the run dies instead of the model
        # reading a refusal. It must arrive as a two-tuple with no artifact.
        wrapped = self.build(
            tool_name=self.READ_TOOL,
            card=self.card(required_scopes=frozenset({"linear:write"})),
        )
        message = await self.call_as_tool_call(wrapped, team="ENG")
        assert isinstance(message, ToolMessage)
        assert message.artifact is None
        assert Messages.Loader.UNAUTHORIZED_SERVER in message.content
        assert wrapped.inner.calls == []

    async def test_refusal_matches_a_plain_content_tool(self) -> None:
        wrapped = self.build(
            tool_name=self.READ_TOOL,
            card=self.card(required_scopes=frozenset({"linear:write"})),
            response_format="content",
        )
        result = await self.call(wrapped, team="ENG")
        assert self.error_of(result)["code"] == McpLoadErrorCode.PERMISSION_DENIED.value


class TestGatePath(PolicyToolFixtureMixin):
    async def test_gate_parks_then_approve_executes_exactly_once(self) -> None:
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED)
        result = await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.calls == 1
        assert len(wrapped.inner.calls) == 1
        assert result == "issues"

    async def test_gate_decline_refuses_without_executing(self) -> None:
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.DECLINED)
        result = await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.calls == 1
        assert wrapped.inner.calls == []
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == PolicyStageMessages.WRITE_DECLINED
        assert error["retryable"] is False

    async def test_parked_pass_never_dispatches(self) -> None:
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=ParkedRun())
        with pytest.raises(ParkedRun):
            await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.calls == 1
        assert wrapped.inner.calls == []

    async def test_gate_payload_binds_the_wrapped_tool(self) -> None:
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED)
        await self.call(wrapped, team="ENG")
        payload = wrapped.interrupt.payloads[0]
        assert payload["server_name"] == self.SERVER
        assert payload["display_name"] == "Linear"
        # Keyed on the injected tool_call_id, so two writes to the same tool in
        # one run cannot share an approval record.
        assert payload["approval_id"] == f"mcp_write:{self.RUN_ID}:{self.TOOL_CALL_ID}"
        assert payload["gate"]["op"] == self.WRITE_TOOL
        assert payload["gate"]["op_class"] == "write"

    async def test_destructive_gate_reports_the_destructive_op_class(self) -> None:
        wrapped = self.build(tool_name=self.DESTRUCTIVE_TOOL, resume=self.DECLINED)
        result = await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.payloads[0]["gate"]["op_class"] == "destructive"
        assert wrapped.inner.calls == []
        assert self.error_of(result)["safe_message"] == (
            PolicyStageMessages.WRITE_DECLINED
        )

    async def test_approval_id_is_stable_across_the_park_and_resume_passes(
        self,
    ) -> None:
        # On resume the tool node re-executes from the top. The SAME call (same
        # injected tool_call_id) must compute the SAME id both times, or the
        # approval record cannot join the two halves of one decision.
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED)
        await self.call(wrapped, tool_call_id="call_same", team="ENG")
        await self.call(wrapped, tool_call_id="call_same", team="ENG")
        first, second = wrapped.interrupt.payloads
        assert first["approval_id"] == second["approval_id"]

    async def test_two_calls_to_one_tool_get_distinct_approval_ids(self) -> None:
        # The regression guard for the hang this migration exists to remove.
        # Two writes to the same tool in one run (e.g. "create three issues")
        # must NOT share an approval id: the second park would otherwise find
        # the first record already resolved, no new pending approval or batch
        # would be created (both are idempotent on the id), every decision would
        # report a lost race, and no resume would ever be enqueued -- the run
        # would park forever. Keyed on tool_name alone, this assertion fails.
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED)
        await self.call(wrapped, tool_call_id="call_A", team="ENG")
        await self.call(wrapped, tool_call_id="call_B", team="ENG")
        first, second = wrapped.interrupt.payloads
        assert first["approval_id"] != second["approval_id"]
        assert first["approval_id"] == f"mcp_write:{self.RUN_ID}:call_A"
        assert second["approval_id"] == f"mcp_write:{self.RUN_ID}:call_B"

    async def test_langchain_dispatch_supplies_the_per_call_approval_id(self) -> None:
        # Through the REAL LangChain dispatch path: the id LangChain injects
        # from the ToolCall is the one the gate parks on, so production calls
        # are per-call unique without the test harness supplying anything.
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED)
        await wrapped.tool.ainvoke(
            {
                "name": wrapped.tool.name,
                "args": {"team": "ENG"},
                "id": "call_from_the_graph",
                "type": "tool_call",
            }
        )
        payload = wrapped.interrupt.payloads[0]
        assert payload["approval_id"] == (
            f"mcp_write:{self.RUN_ID}:call_from_the_graph"
        )


class TestFailClosedBindings(PolicyToolFixtureMixin):
    async def test_gate_none_fails_closed_on_a_write(self) -> None:
        wrapped = self.build(tool_name=self.WRITE_TOOL, with_gate=False)
        result = await self.call(wrapped, team="ENG")
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == PolicyStageMessages.APPROVAL_UNAVAILABLE
        assert wrapped.inner.calls == []

    async def test_gate_none_still_allows_a_read(self) -> None:
        # Fail-closed is scoped to the GATE branch; a blanket refusal would pass
        # the test above while breaking every read in a gateless run.
        wrapped = self.build(tool_name=self.READ_TOOL, with_gate=False)
        result = await self.call(wrapped, team="ENG")
        assert result == "issues"
        assert len(wrapped.inner.calls) == 1

    async def test_unresolvable_card_refuses_without_dispatch(self) -> None:
        wrapped = self.build(tool_name=self.READ_TOOL, cards_by_urn={})
        result = await self.call(wrapped, team="ENG")
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == PolicyStageMessages.POLICY_UNAVAILABLE
        assert wrapped.inner.calls == []

    async def test_descriptor_naming_another_tool_refuses_without_dispatch(
        self,
    ) -> None:
        # A mis-paired ``(tool, descriptor)`` would otherwise police the card of
        # a different capability while dispatching this one.
        wrapped = self.build(
            tool_name=self.READ_TOOL, descriptor_tool=self.DESTRUCTIVE_TOOL
        )
        result = await self.call(wrapped, team="ENG")
        assert self.error_of(result)["safe_message"] == (
            PolicyStageMessages.POLICY_UNAVAILABLE
        )
        assert wrapped.inner.calls == []

    def test_synchronous_invocation_refuses_without_dispatch(self) -> None:
        wrapped = self.build(tool_name=self.READ_TOOL)
        # The wrapper's own sync entry point (LangChain's sync dispatch would
        # reject the call earlier, for declaring an injected tool_call_id).
        raw = wrapped.tool._run(team="ENG")  # noqa: SLF001 - the wrapper's own entry
        result = raw[0] if isinstance(raw, tuple) and len(raw) == 2 else raw
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == PolicyStageMessages.SYNCHRONOUS_DISPATCH
        assert wrapped.inner.calls == []


class TestNamespacedToolNameBinding(PolicyToolFixtureMixin):
    """The stage must bind a tool whose model-surface name is namespaced.

    ``McpToolSource`` registers every connector tool as
    ``mcp__{server_slug}__{tool}`` while deriving its descriptor — and therefore
    its URN — from the connector's own bare name. The two registers meet for the
    first time in ``McpPolicyGate._card_for``, whose cross-check rebuilds the URN
    from ``tool.name``. Comparing a namespaced name against a bare-name URN
    matches nothing, so the card resolves to ``None`` and **every MCP call in
    every run** refuses with ``POLICY_UNAVAILABLE`` — the whole connector
    surface, silently, with no load failure recorded anywhere.
    """

    NAMESPACED_READ = McpToolName.compose(
        server=PolicyToolFixtureMixin.SERVER, tool=PolicyToolFixtureMixin.READ_TOOL
    )

    async def test_namespaced_read_dispatches_instead_of_refusing(self) -> None:
        wrapped = self.build(
            tool_name=self.NAMESPACED_READ, descriptor_tool=self.READ_TOOL
        )
        result = await self.call(wrapped, team="ENG")
        assert result == "issues"
        assert len(wrapped.inner.calls) == 1

    async def test_namespaced_write_still_parks_for_approval(self) -> None:
        # The namespace must not cost the gate either: a WRITE whose card now
        # resolves has to reach the approval interrupt, not sail through.
        wrapped = self.build(
            tool_name=McpToolName.compose(server=self.SERVER, tool=self.WRITE_TOOL),
            descriptor_tool=self.WRITE_TOOL,
        )
        await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.calls == 1

    async def test_a_digested_over_long_name_refuses_rather_than_guessing(
        self,
    ) -> None:
        # The documented boundary, pinned so it stays a decision rather than a
        # surprise. Past the provider's 64-character limit ``compose`` digests
        # the pair, and a digested name cannot be inverted back into the bare
        # name the URN was built from — so the pair does not round-trip and the
        # stage refuses instead of policing against a card it cannot confirm is
        # this tool's. Reachable for a long connector slug plus a long tool
        # name; the fix is for the SOURCE to derive the descriptor from
        # ``strip(compose(...))`` so the two are equal by construction.
        long_tool = "list_issues_by_project_and_team_with_filters_and_cursor"
        namespaced = McpToolName.compose(server=self.SERVER, tool=long_tool)
        assert namespaced != f"mcp__{self.SERVER}__{long_tool}", (
            "fixture must actually exercise the digested path"
        )

        wrapped = self.build(tool_name=namespaced, descriptor_tool=long_tool)
        result = await self.call(wrapped, team="ENG")

        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == PolicyStageMessages.POLICY_UNAVAILABLE
        assert wrapped.inner.calls == []

    async def test_a_namespace_naming_another_connector_refuses(self) -> None:
        # Strictly stronger than the bare-name world could be: the model-surface
        # name carries its connector, so a tool registered under ``github`` and
        # paired with a ``linear`` descriptor is now detectable — and must be
        # refused rather than policed against Linear's card.
        wrapped = self.build(
            tool_name=McpToolName.compose(server="github", tool=self.READ_TOOL),
            descriptor_tool=self.READ_TOOL,
        )
        result = await self.call(wrapped, team="ENG")
        error = self.error_of(result)
        assert error["code"] == McpLoadErrorCode.PERMISSION_DENIED.value
        assert error["safe_message"] == PolicyStageMessages.POLICY_UNAVAILABLE


class TestDestructiveUnderBypass(PolicyToolFixtureMixin):
    """BYPASS lifts the write gate; it does not lift the destructive one.

    Asserted through the real wrapper, so what is pinned is a dispatch: the
    inner connector tool is or is not invoked.
    """

    async def test_destructive_parks_under_bypass(self) -> None:
        wrapped = self.build(
            tool_name=self.DESTRUCTIVE_TOOL, context=self.context(bypass=True)
        )
        await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.calls == 1
        # And it still executes on approve — GATE, not DENY.
        assert len(wrapped.inner.calls) == 1

    async def test_a_declined_destructive_under_bypass_never_dispatches(self) -> None:
        wrapped = self.build(
            tool_name=self.DESTRUCTIVE_TOOL,
            context=self.context(bypass=True),
            resume=self.DECLINED,
        )
        await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.calls == 1
        assert wrapped.inner.calls == []

    async def test_the_card_raised_under_bypass_withholds_the_always_option(
        self,
    ) -> None:
        wrapped = self.build(
            tool_name=self.DESTRUCTIVE_TOOL, context=self.context(bypass=True)
        )
        await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.payloads[0]["grant_options"] == ["allow_once"]


class TestRunScopedAlwaysThroughTheMiddleware(PolicyToolFixtureMixin):
    """``always`` on a card stops the run re-asking the same question.

    The middleware registers the pending ask before parking and records the
    reply the moment the resume comes back approved; the NEXT call re-enters the
    PDP and reads the rule. That round trip is what makes the rule layer
    reachable rather than merely present.
    """

    APPROVED_ALWAYS: Mapping[str, str] = {
        "decision": "approved",
        "decision_scope": "always",
    }

    def setup_method(self) -> None:
        RunDecisionLedgers.reset()

    def teardown_method(self) -> None:
        RunDecisionLedgers.reset()

    async def test_always_stops_the_second_write_parking(self) -> None:
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED_ALWAYS)
        await self.call(wrapped, team="ENG")
        assert wrapped.interrupt.calls == 1

        await self.call(wrapped, tool_call_id="call_p24_b", team="ENG")

        assert wrapped.interrupt.calls == 1
        assert len(wrapped.inner.calls) == 2

    async def test_once_keeps_asking(self) -> None:
        # The default, and what a plain approve has always meant.
        wrapped = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED)
        await self.call(wrapped, team="ENG")
        await self.call(wrapped, tool_call_id="call_p24_b", team="ENG")
        assert wrapped.interrupt.calls == 2
        assert len(wrapped.inner.calls) == 2

    async def test_always_does_not_carry_across_to_a_destructive_op(self) -> None:
        # An `always` on a write cannot buy silence on a delete, because the
        # rule is written against the permission key the card named.
        write = self.build(tool_name=self.WRITE_TOOL, resume=self.APPROVED_ALWAYS)
        await self.call(write, team="ENG")
        destructive = self.build(
            tool_name=self.DESTRUCTIVE_TOOL, resume=self.APPROVED_ALWAYS
        )
        await self.call(destructive, tool_call_id="call_p24_c", team="ENG")
        assert destructive.interrupt.calls == 1

    async def test_a_declined_always_writes_no_rule(self) -> None:
        # ``GateResume`` drops the scope on a rejection, so a decline cannot
        # leave behind the grant it was declining.
        wrapped = self.build(
            tool_name=self.WRITE_TOOL,
            resume={"decision": "rejected", "decision_scope": "always"},
        )
        await self.call(wrapped, team="ENG")
        await self.call(wrapped, tool_call_id="call_p24_b", team="ENG")
        assert wrapped.interrupt.calls == 2
        assert wrapped.inner.calls == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
