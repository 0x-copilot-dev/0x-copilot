"""POLICY stage of the per-tool MCP pipeline (P2-4) — the security seam.

**Composed by the P2-8 registration flip**, behind ``MCP_PER_TOOL_ENABLED``
(default OFF). Until that flag is on for a deployment, the legacy
``call_mcp_tool`` gateway still owns the decision and nothing here runs.

This is ``CallMcpTool._authorize_mcp_dispatch`` **refactored, not rewritten**.
The P1b original is *one gateway that decodes ``server`` / ``tool`` out of a
model-supplied payload* and then asks the PDP about whatever it decoded. Here the
same logic becomes *one wrapper bound to a fixed tool*: :meth:`
PolicyToolMiddleware.wrap` binds the ``(card, server, tool)`` triple at
registration time, and the wrapper asks the PDP about **its own binding** on
every call. The decision itself is still
:meth:`~agent_runtime.capabilities.mcp.descriptor_source.McpDispatchPolicy.evaluate`
— the P1b keystone — so every P1a matrix invariant carries over unchanged, for
free:

* ALLOW → delegate inward (execute now).
* DENY  → a typed refusal **without touching the inner tool**, distinguishing
  ``connector_unavailable`` (availability; retryable) from ``permission_denied``
  (authorization / policy BLOCK; not retryable). The PDP never says *which* gate
  failed, so this table stays deliberately coarse.
* GATE  → :meth:`~agent_runtime.surfaces_v2.gate.ToolAccessGate.park_for_approval`
  — park + resume in the **same** run. Approve → execute the inner tool exactly
  once; decline → a typed refusal. When no approval channel is wired
  (``gate is None``) a GATE **fails closed** to a typed refusal: never a silent
  dispatch, never a stage.

Three properties this module is built around, each of which is a security
property rather than a nicety:

1. **The binding is the tool, not the payload.** The policed tool name is the
   wrapper's own :attr:`~langchain_core.tools.BaseTool.name` and the policed
   server is its bound card's name, so the call that dispatches and the call that
   was policed are the same call by construction. Nothing model-supplied
   participates in choosing *what* is being authorized — only in the arguments.
2. **Every unresolved binding refuses.** A tool whose card is missing from
   ``cards_by_urn``, or whose descriptor URN does not name it, cannot be policed;
   it therefore never dispatches. Refusing at call time (rather than raising at
   ``wrap`` time) keeps one connector's wiring defect from blanking the whole
   registration, and both roads are fail-closed.
3. **The refusal matches the tool's own return contract.**
   ``langchain-mcp-adapters`` builds every MCP tool with
   ``response_format="content_and_artifact"`` (``tools.py``:
   ``StructuredTool(..., response_format="content_and_artifact")``), and
   ``BaseTool.arun`` raises ``ValueError`` if such a tool returns anything but a
   two-tuple. A refusal that crashes the run is not a refusal, so
   :class:`McpRefusalEnvelope` shapes it to whatever format the wrapper declares.

**What is propagated, and what deliberately is not.** The wrapper is built from
:meth:`~agent_runtime.capabilities.mcp.middleware.compose.ToolSchemaIdentity.fields_of`
— the one definition of schema identity, shared with the other four stages since
P2-8 — so a wrapped tool registers with the Deep Agent exactly like the tool it
wraps. It does **not** copy ``handle_tool_error`` / ``handle_validation_error``:
normalising the ``McpClientError`` taxonomy is the ERROR_MAP stage's job (P2-5),
and a POLICY stage that also swallowed exceptions would quietly pre-empt it.

**Where the per-call id comes from, and why not from the schema.** P2-4 obtained
LangChain's ``tool_call_id`` by adding an ``InjectedToolCallId`` field to the
tool's ``args_schema``. That is unsound for a *connector* tool:
``langchain-mcp-adapters`` sets ``args_schema`` to the server's raw
``inputSchema`` — a JSON-Schema ``dict``, not a ``BaseModel`` — so the
augmentation fell through to "build a fresh model containing only
``tool_call_id``" and erased every argument the connector declares. It also made
the wrapper fail the composer's identity assertion, which is how P2-8 found it.
The id is now taken where LangChain already hands it over — the ``arun``
entry point (``BaseTool.arun(..., tool_call_id=…)``, set by ``_prep_run_args``
from the ``ToolCall``) — and carried to ``_arun`` on a per-call ``ContextVar``.
No schema is touched, dict-schema tools work, and the id is available for every
connector rather than only for the ones whose schema happened to be a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, SkipValidation

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from agent_runtime.capabilities.mcp.cards import (
    McpLoadErrorCode,
    McpServerCard,
    McpToolCallResult,
)
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.descriptor_source import McpDispatchPolicy
from agent_runtime.capabilities.mcp.tool_naming import McpToolName
from agent_runtime.capabilities.mcp.middleware.compose import (
    ToolResultShape,
    ToolSchemaIdentity,
)
from agent_runtime.capabilities.policy.contracts import (
    CapabilityDescriptor,
    CapabilityUrn,
    MiddlewareStage,
    PolicyDecision,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.gate import ToolAccessGate


class PolicyStageMessages:
    """Safe, non-leaking copy for every refusal this stage can return.

    Each line is read by the model and paraphrased into user-facing copy, so —
    following the convention already set by ``Messages.Loader`` — a message that
    describes a *permanent* condition says so explicitly. When it did not, a
    deterministic refusal became "try again in a moment", which sends the user
    into a loop that cannot succeed.
    """

    #: A write the user declined at the approval gate.
    WRITE_DECLINED = "The action was declined; no external change was made."
    #: A write that needs approval in a run with no approval channel wired.
    APPROVAL_UNAVAILABLE = (
        "This action needs your approval, which is not available for this run; "
        "no external change was made."
    )
    #: The tool could not be resolved to the connector card it is policed
    #: against, so no decision could be taken and nothing was dispatched.
    POLICY_UNAVAILABLE = (
        "This connector action could not be checked against your permissions, "
        "so it was not run. This is a configuration problem, not a temporary "
        "one: retrying will not change it."
    )
    #: A synchronous invocation, which cannot reach the approval interrupt.
    SYNCHRONOUS_DISPATCH = (
        "This connector action can only run on the asynchronous tool path, "
        "where your approval can be requested; it was not run. Retrying the "
        "same way will not change it."
    )


class PdpReason:
    """The one PDP DENY reason code this stage distinguishes.

    Mirrors ``PdpPolicyService._Reason.CONNECTOR_UNAVAILABLE`` — the availability
    denial, which is retryable because the connector may come back. Every other
    DENY reason (scope miss, allowlist miss, workspace ``BLOCK``) collapses to
    "permission denied", exactly as in the P1b original: the PDP deliberately
    coarsens those into one code so model output cannot distinguish them, and
    this table must not un-coarsen them.
    """

    CONNECTOR_UNAVAILABLE = "connector_unavailable"


class McpRefusalEnvelope:
    """Render a typed refusal in the return shape the wrapper declares.

    The shaping rule itself lives once, in
    :class:`~agent_runtime.capabilities.mcp.middleware.compose.ToolResultShape`
    — the ERROR_MAP result renderer needs the identical rule, and two copies of
    "does this format want a tuple?" is exactly one copy too many.
    """

    @classmethod
    def render(cls, result: McpToolCallResult, *, response_format: str) -> Any:
        """Return the JSON-safe refusal payload, tupled when the format needs it."""

        return ToolResultShape.render(
            result.model_dump(mode="json", exclude_none=True),
            response_format=response_format,
        )


#: The argument LangChain injects only into a tool whose schema declares
#: ``Annotated[str, InjectedToolCallId]``. Read, never added and never removed —
#: the same rule the OBSERVE and CITATIONS stages follow.
_TOOL_CALL_ID_ARG = "tool_call_id"

#: LangChain's per-call id, carried from :meth:`PolicyGatedMcpTool.arun` (where
#: the framework supplies it) down to ``_arun`` (where the approval id is built).
#: A ``ContextVar`` rather than instance state because one registered tool object
#: serves every call in the run, including concurrent ones — an attribute would
#: let two calls share an approval id, which is precisely the collision the
#: approval id exists to avoid.
_TOOL_CALL_ID: ContextVar[str | None] = ContextVar(
    "mcp_policy_tool_call_id", default=None
)


class PolicyCallScope:
    """The per-call ``ContextVar`` holding LangChain's ``tool_call_id``.

    Mirrors the bind / unbind / current shape of ``McpCallBindingScope`` and
    ``McpToolAnnotationsRegistry`` so there is one recognisable pattern for
    call-scoped context in this package.
    """

    @classmethod
    def bind(cls, tool_call_id: str | None) -> object:
        """Set the id for this call; return the token that restores the previous."""

        return _TOOL_CALL_ID.set(tool_call_id)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous id. Safe to call with the bind result."""

        _TOOL_CALL_ID.reset(token)  # type: ignore[arg-type]

    @classmethod
    def current(cls) -> str | None:
        """Return the id of the call in flight, or ``None`` outside one."""

        return _TOOL_CALL_ID.get(None)


class PolicyGatedMcpTool(DelegatingTool):
    """One MCP tool, policed by the PDP before it can reach the connector.

    Schema-identical to the tool it wraps (see the module docstring for exactly
    which fields are propagated and why two are not), so the model surface is
    unchanged and only the invocation path differs.

    ``card`` is ``None`` only when the binding could not be resolved at ``wrap``
    time; every call then refuses. It is modelled as an optional field rather
    than a raise so one connector's wiring defect cannot blank the whole
    registration — and refusing is the fail-closed outcome either way.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    #: The resolved connector card this tool is policed against — half of the
    #: fixed ``(card, server, tool)`` binding. The other half is ``self.name``.
    card: McpServerCard | None
    #: The run whose posture, scopes, pauses and policies the PDP reads.
    runtime_context: AgentRuntimeContext
    #: The run's approval channel. ``None`` ⇒ a GATE fails closed (never a
    #: silent dispatch, never a stage) — the P1b invariant, verbatim.
    #:
    #: ``SkipValidation`` because ``ToolAccessGate`` is a stdlib dataclass whose
    #: ``auth_session_creator`` is typed as a ``Protocol``: pydantic would try to
    #: build a dataclass validator for it and fail at class-definition time
    #: (a non-runtime-checkable Protocol is not a valid ``isinstance`` argument).
    #: The gate is constructor-injected by the runtime, never parsed from wire
    #: input, so there is nothing here for pydantic to validate anyway.
    gate: SkipValidation[ToolAccessGate | None] = None

    @classmethod
    def wrapping(
        cls,
        tool: BaseTool,
        *,
        card: McpServerCard | None,
        runtime_context: AgentRuntimeContext,
        gate: ToolAccessGate | None,
    ) -> "PolicyGatedMcpTool":
        """Return a wrapper carrying ``tool``'s model-visible surface verbatim.

        Every propagated field comes from the shared
        :class:`~agent_runtime.capabilities.mcp.middleware.compose.ToolSchemaIdentity`
        rule, so this stage cannot drift from the other four — including
        ``args_schema``, which is now handed over untouched (see the module
        docstring for what happened when it was not).
        """

        return cls(
            **ToolSchemaIdentity.fields_of(tool),
            inner=tool,
            card=card,
            runtime_context=runtime_context,
            gate=gate,
        )

    async def arun(
        self,
        tool_input: str | dict[str, Any],
        *args: Any,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Bind LangChain's per-call id for the duration of this invocation.

        ``BaseTool.arun`` receives ``tool_call_id`` from ``_prep_run_args`` (it
        is the ``ToolCall``'s ``id``) but only forwards it into ``_arun`` for a
        tool whose schema declares ``InjectedToolCallId``. Reading it here — and
        changing nothing else about the call — is how the approval id stays
        per-call unique without the wrapper touching the connector's schema.
        """

        token = PolicyCallScope.bind(tool_call_id)
        try:
            return await super().arun(
                tool_input, *args, tool_call_id=tool_call_id, **kwargs
            )
        finally:
            PolicyCallScope.unbind(token)

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Refuse: the approval interrupt is only reachable from the async path.

        The graph drives tools via ``ainvoke`` and every MCP tool the adapters
        build is coroutine-only, so this path is not the production one. It is
        still made explicit rather than inherited, because the one thing a sync
        entry point must never do is delegate inward and thereby dispatch a call
        whose GATE could not be raised.
        """

        del args, config, kwargs
        return self._refusal(
            McpLoadErrorCode.PERMISSION_DENIED,
            PolicyStageMessages.SYNCHRONOUS_DISPATCH,
        )

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Police this tool's fixed binding, then delegate inward on ALLOW.

        The PDP sees ``kwargs`` as the call's arguments. That is total for MCP:
        every MCP tool declares an object ``inputSchema``, so LangChain's
        ``_to_args_and_kwargs`` yields named arguments and never positional ones
        (only a bare-string schema does, which no MCP tool has).

        ``tool_call_id`` is LangChain plumbing, not one of the tool's arguments:
        it is hidden from the PDP's view of the call, and — unlike P2-4, which
        popped it — left in ``kwargs`` untouched, because the only way it can be
        there now is that the *inner* tool's own schema asked for it, and
        removing an argument the inner asked for breaks the inner.
        """

        tool_call_id = self._tool_call_id(kwargs)
        refusal = await self._authorize(
            self._policed_arguments(kwargs), tool_call_id=tool_call_id
        )
        if refusal is not None:
            return refusal
        return await self.adelegate(*args, config=config, **kwargs)

    @staticmethod
    def _policed_arguments(kwargs: Mapping[str, Any]) -> dict[str, Any]:
        """Return the call's arguments as the PDP should see them."""

        return {
            name: value for name, value in kwargs.items() if name != _TOOL_CALL_ID_ARG
        }

    @staticmethod
    def _tool_call_id(kwargs: Mapping[str, Any]) -> str | None:
        """Return this call's id: the injected kwarg, else the bound call scope.

        The kwarg exists only when the inner tool's schema declares
        ``InjectedToolCallId``; the scope is set by :meth:`arun` for every call
        LangChain drives. ``None`` means neither was available — a direct
        ``_arun`` (unit tests, replay), where only one call is ever in flight
        and the approval id may safely fall back to the tool name.
        """

        value = kwargs.get(_TOOL_CALL_ID_ARG)
        if isinstance(value, str) and value:
            return value
        return PolicyCallScope.current()

    async def _authorize(
        self, arguments: Mapping[str, Any], *, tool_call_id: str | None = None
    ) -> Any | None:
        """Consult the PDP; return a refusal, or ``None`` to dispatch.

        The P1b ``_authorize_mcp_dispatch`` contract, bound to a fixed tool:
        ALLOW → ``None``; DENY → a typed refusal; GATE → park on the
        write-approval interrupt and return ``None`` only on approve, so an
        approved write executes in the SAME run and a declined one never
        dispatches.
        """

        card = self.card
        if card is None:
            # Unresolved binding: there is no card to police against, so there
            # is no decision — and no decision means no dispatch.
            return self._refusal(
                McpLoadErrorCode.PERMISSION_DENIED,
                PolicyStageMessages.POLICY_UNAVAILABLE,
            )
        decision = McpDispatchPolicy.evaluate(
            card=card,
            server=card.name,
            tool=self.name,
            arguments=arguments,
            context=self.runtime_context,
        )
        if decision.decision is PolicyDecision.ALLOW:
            return None
        if decision.decision is PolicyDecision.DENY:
            return self._policy_denied(decision.reason)
        # GATE — the write-approval interrupt (park + resume in the same run).
        if self.gate is None:
            return self._refusal(
                McpLoadErrorCode.PERMISSION_DENIED,
                PolicyStageMessages.APPROVAL_UNAVAILABLE,
            )
        resume = await self.gate.park_for_approval(
            card=card,
            # The CONNECTOR register: this value becomes ``gate.op`` and the
            # approval card's own sentence ("Allow Linear to run …?"), which
            # names the connector separately — so the namespace would read back
            # to the user as "Allow Linear to run mcp__linear__create_issue?".
            tool_name=McpToolName.strip(self.name),
            arguments=arguments,
            op_class=decision.descriptor.action.value,
            approval_id=self._approval_id(tool_call_id),
        )
        if not resume.approved:
            return self._refusal(
                McpLoadErrorCode.PERMISSION_DENIED,
                PolicyStageMessages.WRITE_DECLINED,
            )
        return None

    def _policy_denied(self, reason: str) -> Any:
        """Map a PDP DENY reason code onto a typed, non-leaking refusal."""

        if reason == PdpReason.CONNECTOR_UNAVAILABLE:
            return self._refusal(
                McpLoadErrorCode.SERVER_UNHEALTHY,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
            )
        return self._refusal(
            McpLoadErrorCode.PERMISSION_DENIED,
            Messages.Loader.UNAUTHORIZED_SERVER,
        )

    def _refusal(
        self,
        code: McpLoadErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> Any:
        """Build this tool's typed failure result, rendered for its return shape."""

        return McpRefusalEnvelope.render(
            McpToolCallResult.fail(
                code,
                safe_message,
                retryable=retryable,
                server_name=self.card.name if self.card is not None else None,
                tool_name=self.name,
                correlation_id=self.runtime_context.trace_id,
            ),
            response_format=self.response_format,
        )

    def _approval_id(self, tool_call_id: str | None) -> str:
        """Deterministic, per-call id for this tool's write-approval gate.

        Keyed on ``(run_id, tool_call_id)``, matching P1b, which preferred
        ``tool_call_id`` and fell back to the tool name only when none was
        injected.

        Both halves are load-bearing, in opposite directions:

        * **Deterministic** across the park→resume replay. On resume the tool
          node re-executes from the top, so the id computed on the parked pass
          and on the resumed pass must be identical for the approval record to
          join. ``tool_call_id`` is stable across that replay; a counter or a
          uuid would silently break the join.
        * **Unique per call.** Two writes to the *same* tool in one run must not
          share an id. If they did, the second park would find the first
          approval record already resolved, no new pending approval or batch
          would be created (both are idempotent on the id), and any decision
          would report a lost race — so no resume would ever be enqueued and the
          run would park forever. That is precisely the hang this whole
          migration exists to remove.

        The ``tool_name`` fallback covers direct invocation (unit tests, replay),
        where LangChain injects nothing and only one call is ever in flight.
        """

        suffix = tool_call_id or self.name
        return f"mcp_write:{self.runtime_context.run_id}:{suffix}"


@dataclass(frozen=True)
class PolicyToolMiddleware:
    """The POLICY stage: bind each MCP tool to its card and police every call.

    Implements the P0
    :class:`~agent_runtime.capabilities.policy.contracts.ToolMiddleware` Protocol
    at :attr:`~agent_runtime.capabilities.policy.contracts.MiddlewareStage.POLICY`
    — ``MIDDLEWARE_ORDER[0]``, the outermost wrapper, so the decision is taken
    before any other stage (retry, observation, error mapping, citations) can
    run. Services are constructor-injected; the uniform seam stays
    ``(tool, descriptor) -> tool``.

    ``cards_by_urn`` is the map :class:`~agent_runtime.capabilities.mcp.tool_source.McpToolCatalog`
    publishes for exactly this purpose: it lets the stage build a GATE interrupt
    payload for the fixed tool it wraps without re-resolving the server, which is
    what kept the P1b gateway's per-call ``registry.resolve_server`` round-trip
    necessary.
    """

    #: The run whose posture, scopes, pauses and policies the PDP reads.
    runtime_context: AgentRuntimeContext
    #: ``CapabilityDescriptor.urn -> McpServerCard`` (the catalog's published map).
    cards_by_urn: Mapping[str, McpServerCard]
    #: The run's approval channel; ``None`` makes every GATE fail closed.
    gate: ToolAccessGate | None = None
    #: Fixed by construction — this middleware IS the policy stage, and a
    #: composer asserting ``MIDDLEWARE_ORDER`` must not be able to be told
    #: otherwise at the constructor.
    stage: MiddlewareStage = field(default=MiddlewareStage.POLICY, init=False)

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Return a schema-identical tool that is policed before it dispatches."""

        return PolicyGatedMcpTool.wrapping(
            tool,
            card=self._card_for(tool, descriptor),
            runtime_context=self.runtime_context,
            gate=self.gate,
        )

    def _card_for(
        self, tool: BaseTool, descriptor: CapabilityDescriptor
    ) -> McpServerCard | None:
        """Resolve the card this tool is policed against, or ``None`` (fail closed).

        The descriptor's URN is both the lookup key and a cross-check. A pair
        whose URN does not name this very tool is a composition defect with a
        security consequence, not a cosmetic one: the wrapper would police one
        capability's card — its trust, its auth state, its allowlists, its
        availability — while dispatching a different connector's tool. Rather
        than police the wrong card, bind nothing and refuse.
        """

        card = self.cards_by_urn.get(descriptor.urn)
        if card is None:
            return None
        if descriptor.urn != CapabilityUrn.for_mcp(card.name, tool.name):
            return None
        return card


__all__ = [
    "McpRefusalEnvelope",
    "PdpReason",
    "PolicyCallScope",
    "PolicyGatedMcpTool",
    "PolicyStageMessages",
    "PolicyToolMiddleware",
]
