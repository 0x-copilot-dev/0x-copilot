"""Production ``PolicyToolInvoker`` for AC6 code mode ("Option B").

Pure-compute mode (``pure_compute.py``, Option A) refuses every external call:
the resolver authorizes nothing and the invoker always denies. This module is
the **Option B** counterpart — it lets interpreted Monty code make *real*
external tool calls, but only through the same enforcement an ordinary tool call
gets: budget first, then human approval, then a dispatch to the already
authorized runtime tool.

The seam the PRD fixes (``06-ac6-monty-code-mode.md`` — "the genuinely novel
mechanism"): when interpreted code calls a declared alias, the interpreter
suspends and the service asks *this* invoker. The invoker

1. charges the underlying tool's budget (:class:`ExternalCallBudgetGuard`);
2. requests approval by raising a **LangGraph interrupt from inside the running
   ``run_code_mode`` tool node** (:class:`InterruptApprovalGate`) — the SAME
   interrupt contract ``ask_a_question`` and the MCP tool path already ride, not
   a bespoke pause engine;
3. on approval, dispatches to the real runtime tool
   (:class:`ExternalCallDispatcher`) and returns its value; the service resumes
   the Monty program with that value injected;
4. on rejection / budget-denial / dispatch error, returns a non-``allowed``
   outcome, which the adapter surfaces into interpreted code as a typed
   exception — the side effect never happened.

Every collaborator is a narrow injected port so the drive loop, the approval
contract, and the tool registry stay each other's substitutes (interface
segregation). Nothing here is Monty-specific; the same invoker would back a
future QuickJS adapter.

Not in scope here (the separate mandatory spike, PRD "Acceptance … exactly
once"): compare-and-swap dedup on ``(run_id, interpreter_session_id,
invocation_index)`` for worker-crash recovery. The interrupt payload already
carries ``invocation_index`` so that dedup can key on it without a contract
change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from agent_runtime.api.constants import Keys
from agent_runtime.capabilities.operations.builtin_adapter import (
    BuiltinGatewayGateResolver,
)
from agent_runtime.capabilities.operations.builtin_catalog import (
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterErrorCode,
)
from agent_runtime.capabilities.interpreter.ports import (
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
)
from agent_runtime.capabilities.interpreter.service import ExternalFunctionResolver
from agent_runtime.execution.contracts import JsonValue
from agent_runtime.surfaces_v2.entities import OperationRequest
from agent_runtime.surfaces_v2.ledger_models import (
    OperationOutcome,
)


class ExternalToolDispatchError(Exception):
    """The authorized tool could not run (unknown, unavailable, or it raised).

    Carries a redaction-safe message only; the invoker projects it into a typed
    :class:`PolicyToolInvocationOutcome` — the raw tool traceback never reaches
    interpreted code or model output.
    """

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


@runtime_checkable
class ExternalCallBudgetGuard(Protocol):
    """Charges the underlying tool's budget for one external call.

    Returns ``True`` to admit the call, ``False`` to deny it before any approval
    prompt or dispatch. The PRD requires each bridged call to be charged against
    the underlying tool's budget, exactly as a direct call is.
    """

    async def charge(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
        context: PolicyInvocationContext,
    ) -> bool: ...


@runtime_checkable
class ExternalCallApprovalGate(Protocol):
    """Requests human approval for one external call and reports the decision.

    Returns ``True`` when approved, ``False`` when rejected. The production
    implementation raises a LangGraph interrupt; a test double resolves
    synchronously.
    """

    async def request_approval(
        self,
        *,
        spec: ExternalFunctionSpec,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> bool: ...


@runtime_checkable
class ExternalCallDispatcher(Protocol):
    """Dispatches an approved external call to the real runtime tool.

    Returns the tool's JSON-compatible result, or raises
    :class:`ExternalToolDispatchError` for an unknown/unavailable tool or a tool
    that itself failed. It is only ever reached after budget + approval passed.
    """

    async def dispatch(
        self,
        *,
        spec: ExternalFunctionSpec,
        arguments: Mapping[str, JsonValue],
        context: PolicyInvocationContext,
    ) -> JsonValue: ...


@runtime_checkable
class ExternalCallProposalBuilder(Protocol):
    """Build one staged external-call proposal without dispatching it.

    D2 owns the interpreter boundary only.  The designated adapter (D1/A4/A5)
    supplies this port for an effectful child operation; no interpreter module
    receives a connector client or executor registry.
    """

    async def build_proposal(
        self,
        *,
        spec: ExternalFunctionSpec,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
        operation_id: str,
    ) -> ProposedEffect: ...


class HitlPolicyToolInvoker:
    """Production :class:`PolicyToolInvoker`: budget -> approval -> dispatch.

    Composes three narrow ports into the single seam the interpreter bridge
    routes every external call through. Only an ``allowed`` outcome implies a
    real side effect; every other terminal status returns *before* dispatch so
    no tool runs.
    """

    def __init__(
        self,
        *,
        budget: ExternalCallBudgetGuard,
        approval: ExternalCallApprovalGate,
        dispatcher: ExternalCallDispatcher,
    ) -> None:
        self._budget = budget
        self._approval = approval
        self._dispatcher = dispatcher

    async def invoke(
        self,
        *,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> PolicyToolInvocationOutcome:
        """Route one external call through budget, approval, then dispatch."""

        authorized = await self.authorize(call=call, context=context)
        if authorized.status != PolicyToolInvocationOutcome.ALLOWED:
            return authorized
        return await self.dispatch_authorized(
            call=call,
            context=context,
            invocation_id=authorized.invocation_id,
        )

    async def authorize(
        self,
        *,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> PolicyToolInvocationOutcome:
        """Run the shared budget and approval gates without dispatching.

        The gateway-backed code-mode bridge uses this split to authorize an
        external child operation before building its proposal.  Keeping it here
        prevents a second, interpreter-specific budget/approval implementation.
        """

        budgeted = await self.charge_budget(call=call, context=context)
        if budgeted.status != PolicyToolInvocationOutcome.ALLOWED:
            return budgeted
        approved = await self._approval.request_approval(
            spec=context.spec, call=call, context=context
        )
        if not approved:
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.REJECTED,
                invocation_id=budgeted.invocation_id,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="the external tool call was rejected",
            )
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.ALLOWED,
            invocation_id=budgeted.invocation_id,
        )

    async def charge_budget(
        self,
        *,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> PolicyToolInvocationOutcome:
        """Charge a child operation without implicitly approving an effect.

        Pure/internal interpreter children continue through ``authorize``.
        Effectful children pass this budget gate and let the generic effect
        stager record the user-reviewable policy decision. This prevents an
        invisible interpreter approval from bypassing a staged write surface.
        """

        invocation_id = uuid4().hex
        admitted = await self._budget.charge(
            tool_name=context.spec.tool_name,
            arguments=dict(call.arguments),
            context=context,
        )
        if not admitted:
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=invocation_id,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="the external tool's budget is exhausted",
            )
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.ALLOWED,
            invocation_id=invocation_id,
        )

    async def dispatch_authorized(
        self,
        *,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
        invocation_id: str,
    ) -> PolicyToolInvocationOutcome:
        """Dispatch exactly one already-authorized pure/internal child call."""

        try:
            return_value = await self._dispatcher.dispatch(
                spec=context.spec,
                arguments=dict(call.arguments),
                context=context,
            )
        except ExternalToolDispatchError as exc:
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.ERROR,
                invocation_id=invocation_id,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message=exc.safe_message,
            )
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.ALLOWED,
            invocation_id=invocation_id,
            return_value=return_value,
        )


class CodeModeChildOperationBlocked(RuntimeError):
    """A child operation was denied or needs a proposal adapter."""

    def __init__(self, outcome: PolicyToolInvocationOutcome) -> None:
        super().__init__(outcome.safe_message or "external child operation was blocked")
        self.outcome = outcome


@dataclass
class _GatewayChildAdapter:
    """Operation gateway adapter for one interpreter external callback."""

    legacy: object
    call: ExternalFunctionCall
    context: PolicyInvocationContext
    proposal_builder: ExternalCallProposalBuilder | None
    outcome: PolicyToolInvocationOutcome | None = None

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        del request
        authorize = getattr(self.legacy, "authorize", None)
        dispatch_authorized = getattr(self.legacy, "dispatch_authorized", None)
        if not callable(authorize) or not callable(dispatch_authorized):
            self.outcome = PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=uuid4().hex,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="code-mode child operation is not gateway-enabled",
            )
            raise CodeModeChildOperationBlocked(self.outcome)
        authorized = await authorize(call=self.call, context=self.context)
        if authorized.status != PolicyToolInvocationOutcome.ALLOWED:
            self.outcome = authorized
            raise CodeModeChildOperationBlocked(authorized)
        self.outcome = await dispatch_authorized(
            call=self.call,
            context=self.context,
            invocation_id=authorized.invocation_id,
        )
        if self.outcome.status != PolicyToolInvocationOutcome.ALLOWED:
            raise CodeModeChildOperationBlocked(self.outcome)
        return OperationRawResult(safe_summary="Code-mode child operation completed.")

    async def build_proposal(self, request: OperationRequest) -> ProposedEffect:
        charge_budget = getattr(self.legacy, "charge_budget", None)
        if not callable(charge_budget):
            self.outcome = PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=uuid4().hex,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="code-mode child operation is not gateway-enabled",
            )
            raise CodeModeChildOperationBlocked(self.outcome)
        if self.proposal_builder is None:
            self.outcome = PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=uuid4().hex,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="the external child operation must be staged before it can run",
            )
            raise CodeModeChildOperationBlocked(self.outcome)
        budgeted = await charge_budget(call=self.call, context=self.context)
        if budgeted.status != PolicyToolInvocationOutcome.ALLOWED:
            self.outcome = budgeted
            raise CodeModeChildOperationBlocked(budgeted)
        return await self.proposal_builder.build_proposal(
            spec=self.context.spec,
            call=self.call,
            context=self.context,
            operation_id=request.operation_id,
        )


class GatewayPolicyToolInvoker:
    """Route every code-mode external callback through a child operation.

    The legacy invoker stays authoritative while the gateway is off or in
    shadow mode.  In enforce mode, pure/internal descriptors authorize and
    dispatch through the gateway; external/unknown descriptors can only use a
    proposal builder and therefore cannot bypass staging.
    """

    def __init__(
        self,
        *,
        legacy: object,
        proposal_builder: ExternalCallProposalBuilder | None = None,
    ) -> None:
        self._legacy = legacy
        self._proposal_builder = proposal_builder

    async def invoke(
        self,
        *,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> PolicyToolInvocationOutcome:
        active = OperationContext.active()
        legacy_invoke = getattr(self._legacy, "invoke", None)
        if not callable(legacy_invoke):
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=uuid4().hex,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="code-mode external calls are unavailable",
            )
        if active is None or active.mode.value != "enforce":
            return await legacy_invoke(call=call, context=context)

        capability, op = self._operation_key(context.spec.tool_name)
        request = OperationRequestFactory.create(
            capability=capability,
            op=op,
            arguments=dict(call.arguments),
        )
        adapter = _GatewayChildAdapter(
            legacy=self._legacy,
            call=call,
            context=context,
            proposal_builder=self._proposal_builder,
        )
        try:
            disposition = await OperationGateway(
                descriptors=DEFAULT_OPERATION_DESCRIPTORS,
                gates=BuiltinGatewayGateResolver(),
            ).invoke(request, adapter)
        except CodeModeChildOperationBlocked as exc:
            return exc.outcome
        except Exception:
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.ERROR,
                invocation_id=uuid4().hex,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="the code-mode child operation failed safely",
            )
        if adapter.outcome is not None:
            return adapter.outcome
        if disposition.outcome is OperationOutcome.STAGED:
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=uuid4().hex,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="the external child operation was staged for review",
            )
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.DENIED,
            invocation_id=uuid4().hex,
            error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
            safe_message=disposition.agent_summary,
        )

    @staticmethod
    def _operation_key(tool_name: str) -> tuple[str, str]:
        """Resolve reviewed built-ins exactly; unknown callbacks stay held."""

        entry = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_tool_name(tool_name)
        if entry is not None:
            if (
                DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(entry.capability, entry.op)
                is None
            ):
                raise RuntimeError("code-mode callable lacks an exact descriptor")
            return entry.capability, entry.op
        descriptor = DEFAULT_OPERATION_DESCRIPTORS.resolve_entry("builtin", tool_name)
        if descriptor is not None:
            return descriptor.descriptor.capability, descriptor.descriptor.op
        # Dynamic names intentionally receive the gateway's unknown/held
        # classification.  They never fall through to a dispatcher.
        return "dynamic_tool", "invoke"


class InterruptApprovalGate:
    """Approval gate that raises a LangGraph interrupt from the tool node.

    Reuses the exact interrupt seam ``ask_a_question`` uses: it calls
    ``langgraph.types.interrupt(payload)``, which suspends the graph until a
    human decision returns as the resume value. ``interrupt_handler`` is
    injectable so tests resolve the decision synchronously without a live graph.

    The emitted payload mirrors the ``approval_requested`` shape the rest of the
    runtime already understands (kind, approval id, tool name, invocation
    index). No interpreter internals — no source, snapshot bytes, or callback
    args — are ever placed in the payload.
    """

    #: Discriminator written into the interrupt payload so a future
    #: approval-resolution path can route an interpreter external-call decision.
    APPROVAL_KIND = "code_mode_external_call"

    def __init__(
        self,
        *,
        interrupt_handler: Callable[[dict[str, Any]], object] | None = None,
    ) -> None:
        if interrupt_handler is None:
            from langgraph.types import interrupt as langgraph_interrupt  # noqa: PLC0415

            interrupt_handler = langgraph_interrupt
        self._interrupt = interrupt_handler

    async def request_approval(
        self,
        *,
        spec: ExternalFunctionSpec,
        call: ExternalFunctionCall,
        context: PolicyInvocationContext,
    ) -> bool:
        """Raise an interrupt for this external call and read the human decision."""

        approval_id = (
            f"code_mode:{context.run_id}:"
            f"{context.interpreter_session_id}:{call.invocation_index}"
        )
        payload: dict[str, Any] = {
            Keys.Field.API_EVENT_TYPE: "approval_requested",
            Keys.Field.EVENT_TYPE: "approval_requested",
            Keys.Field.APPROVAL_ID: approval_id,
            Keys.Field.APPROVAL_KIND: self.APPROVAL_KIND,
            "action_id": approval_id,
            "tool_name": spec.tool_name,
            "alias": call.alias,
            "invocation_index": call.invocation_index,
            "interpreter_session_id": context.interpreter_session_id,
            Keys.Field.STATUS: "pending",
        }
        resume = self._interrupt(payload)
        return self._approved(resume)

    @staticmethod
    def _approved(resume: object) -> bool:
        """Interpret a resume value as approve (``True``) or reject (``False``).

        Accepts both single-decision shapes (``{"decision": "approved"}`` — the
        ``ask_a_question`` / ``mcp_auth`` convention) and the batch shape
        (``{"decisions": [{"type": "approve"}]}`` — the MCP tool convention), so
        whichever resume the approval path delivers is understood. Anything
        unrecognised fails closed as a rejection.
        """

        if isinstance(resume, bool):
            return resume
        if not isinstance(resume, Mapping):
            return False
        decision = resume.get(Keys.Field.DECISION)
        if isinstance(decision, str):
            return decision.strip().lower() in {"approve", "approved", "accept"}
        decisions = resume.get("decisions")
        if isinstance(decisions, (list, tuple)) and decisions:
            first = decisions[0]
            if isinstance(first, Mapping):
                kind = first.get("type")
                return isinstance(kind, str) and kind.strip().lower() in {
                    "approve",
                    "approved",
                    "accept",
                }
        return False


class LangChainToolDispatcher:
    """Dispatches an approved external call to an authorized LangChain tool.

    Backed by a ``{tool_name: tool}`` mapping of the run's already
    scope-filtered, model-visible tools. It calls the tool's async ``ainvoke``
    with the interpreted-code arguments and returns the result. An unknown tool,
    a tool with no async surface, or a tool that raises is converted to a typed
    :class:`ExternalToolDispatchError` with a redaction-safe message.
    """

    def __init__(self, tools_by_name: Mapping[str, object]) -> None:
        self._tools = dict(tools_by_name)

    async def dispatch(
        self,
        *,
        spec: ExternalFunctionSpec,
        arguments: Mapping[str, JsonValue],
        context: PolicyInvocationContext,
    ) -> JsonValue:
        """Invoke the authorized tool bound to ``spec.tool_name``."""

        del context
        tool = self._tools.get(spec.tool_name)
        if tool is None:
            raise ExternalToolDispatchError("the external tool is not available")
        ainvoke = getattr(tool, "ainvoke", None)
        if not callable(ainvoke):
            raise ExternalToolDispatchError("the external tool is not available")
        try:
            return await ainvoke(dict(arguments))
        except ExternalToolDispatchError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak a tool traceback
            raise ExternalToolDispatchError("the external tool call failed") from exc


class AuthorizedToolResolver(ExternalFunctionResolver):
    """Resolves a model-declared alias to an authorized tool binding.

    An alias resolves only when it names a tool the run is already allowed to
    call — the alias *is* the authorized tool name. An alias for a tool the run
    cannot call (or a fabricated name) resolves to ``None`` and the service
    fails it closed with ``external_function_unknown`` before the program runs.
    This is the Option-B substitute for :class:`~agent_runtime.capabilities.
    interpreter.pure_compute.PureComputeResolver`, which resolves nothing.
    """

    def __init__(self, tools_by_name: Mapping[str, object]) -> None:
        self._tool_names = frozenset(tools_by_name.keys())

    def resolve(self, alias: str) -> ExternalFunctionSpec | None:
        """Return an :class:`ExternalFunctionSpec` for an authorized alias, else ``None``."""

        if alias not in self._tool_names:
            return None
        return ExternalFunctionSpec(alias=alias, tool_name=alias)


__all__ = (
    "AuthorizedToolResolver",
    "CodeModeChildOperationBlocked",
    "ExternalCallApprovalGate",
    "ExternalCallProposalBuilder",
    "ExternalCallBudgetGuard",
    "ExternalCallDispatcher",
    "ExternalToolDispatchError",
    "GatewayPolicyToolInvoker",
    "HitlPolicyToolInvoker",
    "InterruptApprovalGate",
    "LangChainToolDispatcher",
)
