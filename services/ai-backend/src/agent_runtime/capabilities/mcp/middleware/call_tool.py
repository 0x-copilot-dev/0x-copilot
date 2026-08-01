"""Model-facing tool that invokes a selected MCP tool after discovery."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent_runtime.capabilities.mcp.cards import (
    McpLoadError,
    McpLoadErrorCode,
    McpServerCard,
    McpToolCallRequest,
    McpToolCallResult,
)
from agent_runtime.capabilities.mcp.constants import Messages, Values
from agent_runtime.capabilities.mcp.descriptor_source import McpDispatchPolicy
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.capabilities.mcp.gateway_context import (
    McpOperationGatewayContext,
    McpOperationGatewayServices,
)
from agent_runtime.capabilities.mcp.operation_adapter import McpOperationAdapter
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.policy.contracts import PolicyDecision
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.effects.contracts import EffectPolicySnapshot
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.gate import ToolAccessGate
from agent_runtime.surfaces_v2.ledger_models import (
    EffectPolicy,
    OperationOutcome,
)

_LOGGER = logging.getLogger(__name__)
_DESKTOP_BROWSER_SERVER = "desktop_browser"

# The PDP DENY reason (a stable machine code from
# ``PdpPolicyService._Reason``) that maps to "connector unavailable" copy;
# every other DENY reason collapses to "permission denied". The PDP never
# leaks which gate failed, so this table stays deliberately coarse.
_CONNECTOR_UNAVAILABLE_REASON = "connector_unavailable"
# Safe copy for a write the user declined at the approval gate, and for a write
# that needs approval when no approval channel is wired for this run.
_WRITE_DECLINED = "The action was declined; no external change was made."
_APPROVAL_UNAVAILABLE = (
    "This action needs your approval, which is not available for this run; "
    "no external change was made."
)


@dataclass(frozen=True)
class CallMcpTool:
    """Invoke a tool from one previously discovered MCP server."""

    registry: DynamicMcpRegistry
    loader: McpLoader
    runtime_context: AgentRuntimeContext
    # The canonical adapter owns the ToolAccessGate decision at the connector
    # dispatch boundary. ``None`` keeps gateway tests and non-MCP providers
    # simple; it never enables a direct provider-dispatch fallback.
    gate: ToolAccessGate | None = None
    name: str = Values.ToolName.CALL_MCP_TOOL
    description: str = Messages.Middleware.CALL_MCP_TOOL_DESCRIPTION

    async def ainvoke(
        self,
        raw_input: McpToolCallRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate input, re-check permissions, call the tool, and annotate with a citation hint."""
        parsed_input = CallMcpToolInputParser.parse(
            raw_input,
            self.runtime_context.trace_id,
        )
        if isinstance(parsed_input, McpToolCallResult):
            return parsed_input.model_dump(mode="json", exclude_none=True)

        # D1/D7: model-facing MCP calls have one route. A safe rollback may
        # hold work when the durable gateway is not composed, but must never
        # recreate the retired direct provider-dispatch branch.
        services = McpOperationGatewayContext.canonical()
        if services is None:
            return await self._hold_after_safe_preflight(parsed_input)
        return await self._ainvoke_operation_gateway(parsed_input, services)

    async def _hold_after_safe_preflight(
        self, parsed_input: McpToolCallRequest
    ) -> dict[str, Any]:
        """Return honest load/permission failures before an unbound hold.

        Resolution and card authorization do not construct a connector client,
        so preserving this feedback cannot restore the retired dispatch path.
        """

        resolution = await self.registry.resolve_server(parsed_input.server_name)
        if isinstance(resolution, McpLoadError):
            return McpToolCallResult.fail(
                resolution.code,
                resolution.safe_message,
                retryable=resolution.retryable,
                server_name=resolution.server_name or parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        if not McpPermissionPolicy.is_server_card_authorized(
            self.runtime_context, resolution.card
        ):
            return McpToolCallResult.fail(
                McpLoadErrorCode.PERMISSION_DENIED,
                Messages.Loader.UNAUTHORIZED_SERVER,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        return self._held_without_gateway(parsed_input)

    def _held_without_gateway(self, parsed_input: McpToolCallRequest) -> dict[str, Any]:
        """Fail closed before connector construction when gateway wiring is absent."""

        return McpToolCallResult.ok(
            server_name=parsed_input.server_name,
            tool_name=parsed_input.tool_name,
            output={
                "status": "held",
                "summary": (
                    "The connector operation is held until the canonical operation "
                    "gateway is available; no connector call was made."
                ),
            },
        ).model_dump(mode="json", exclude_none=True)

    async def _ainvoke_operation_gateway(
        self,
        parsed_input: McpToolCallRequest,
        services: McpOperationGatewayServices,
    ) -> dict[str, Any]:
        """Run one classified MCP operation without touching the legacy path."""

        resolution = await self.registry.resolve_server(parsed_input.server_name)
        if isinstance(resolution, McpLoadError):
            return McpToolCallResult.fail(
                resolution.code,
                resolution.safe_message,
                retryable=resolution.retryable,
                server_name=resolution.server_name or parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        if not McpPermissionPolicy.is_server_card_authorized(
            self.runtime_context, resolution.card
        ):
            return McpToolCallResult.fail(
                McpLoadErrorCode.PERMISSION_DENIED,
                Messages.Loader.UNAUTHORIZED_SERVER,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        is_browser = (
            parsed_input.server_name.strip().lower().replace("-", "_")
            == _DESKTOP_BROWSER_SERVER
        )
        # P1b: the PDP owns the MCP approval decision. It is consulted BEFORE any
        # operation id is minted, so a write that GATEs parks on the interrupt
        # before the gateway's idempotency scope is entered — the approved write
        # then mints its id and executes exactly once on resume. ALLOW falls
        # through to execute-now; DENY / declined return a typed refusal. The
        # browser server keeps its own effect-staging lane (below) and is not
        # routed through this connector-write gate.
        if not is_browser:
            gate_result = await self._authorize_mcp_dispatch(
                parsed_input, resolution.card
            )
            if gate_result is not None:
                return gate_result

        request = OperationRequestFactory.create(
            capability=parsed_input.server_name,
            op=parsed_input.tool_name,
            arguments=parsed_input.arguments,
        )
        operation_context = OperationContext.require()
        stored_arguments = operation_context.arguments.get(request.canonical_args_ref)
        if stored_arguments is None:
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        digest, canonical_bytes = stored_arguments
        try:
            await services.argument_store.persist(
                ref=request.canonical_args_ref,
                digest=digest,
                canonical_bytes=canonical_bytes,
            )
        except Exception:  # noqa: BLE001 - never dispatch without durable material.
            _LOGGER.warning(
                "mcp_operation_arguments_unavailable",
                extra={"operation_id": request.operation_id},
                exc_info=True,
            )
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        mcp_adapter: McpOperationAdapter | None = None
        if is_browser:
            # Local imports avoid making the MCP package import the browser
            # operation stack while the global capability catalog is booting.
            from agent_runtime.capabilities.browser.contracts import (  # noqa: PLC0415
                BrowserActionPlanStore,
            )
            from agent_runtime.capabilities.browser.effect_adapter import (  # noqa: PLC0415
                BrowserEffectStageAdapter,
            )
            from agent_runtime.capabilities.browser.operation_adapter import (  # noqa: PLC0415
                BrowserOperationAdapter,
                is_browser_read_operation,
            )
        if is_browser and not is_browser_read_operation(parsed_input.tool_name):
            plans = services.browser_plans
            if not isinstance(plans, BrowserActionPlanStore):
                return McpToolCallResult.fail(
                    McpLoadErrorCode.CONNECTION_FAILED,
                    Messages.Loader.LOAD_FAILED,
                    retryable=True,
                    server_name=parsed_input.server_name,
                    tool_name=parsed_input.tool_name,
                    correlation_id=self.runtime_context.trace_id,
                ).model_dump(mode="json", exclude_none=True)
            browser_policy = EffectPolicySnapshot(
                snapshot_ref=(
                    f"policy://runs/{self.runtime_context.run_id}/desktop-browser"
                ),
                descriptor_known=(
                    services.descriptors.resolve(
                        parsed_input.server_name,
                        parsed_input.tool_name,
                    )
                    is not None
                ),
                capability_policy=EffectPolicy.REQUIRE,
                user_policy=EffectPolicy.REQUIRE,
                sensitive_target=True,
            )
            adapter = BrowserOperationAdapter(
                stager=BrowserEffectStageAdapter(
                    plans=plans,
                    stager=services.stager,
                    scope=services.stage_scope,
                    actor=services.stage_author,
                    policy_snapshot=browser_policy,
                )
            )
        else:
            mcp_adapter = McpOperationAdapter(
                registry=self.registry,
                runtime_context=self.runtime_context,
                timeout_seconds=self.loader.timeout_seconds,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                arguments=parsed_input.arguments,
                gate=self.gate,
                execution=services.execution,
                tool_call_id=parsed_input.tool_call_id,
                # Post-PDP-authorized: the gateway executes (read or write)
                # instead of routing a write to the retired staging path.
                authorized_to_execute=True,
            )
            adapter = mcp_adapter
        disposition = await services.gateway.invoke(request, adapter)
        output: dict[str, Any] = {
            "status": disposition.outcome.value,
            "operation_id": disposition.operation_id,
            "summary": disposition.agent_summary,
        }
        if disposition.outcome is OperationOutcome.SUCCEEDED:
            stored = mcp_adapter.stored_result if mcp_adapter is not None else None
            if stored is None:
                # A successful disposition without a durable result is an
                # adapter invariant violation.  Do not pretend the read
                # completed to the model.
                return McpToolCallResult.fail(
                    McpLoadErrorCode.CONNECTION_FAILED,
                    Messages.Loader.LOAD_FAILED,
                    retryable=True,
                    server_name=parsed_input.server_name,
                    tool_name=parsed_input.tool_name,
                    correlation_id=self.runtime_context.trace_id,
                ).model_dump(mode="json", exclude_none=True)
            output.update(
                {
                    "status": "completed",
                    "summary": (
                        f"Fetched {parsed_input.tool_name} from "
                        f"{parsed_input.server_name}."
                    ),
                    "result_ref": stored.result_ref,
                    "result": stored.model_output,
                }
            )
        elif disposition.outcome is OperationOutcome.STAGED:
            output.update(
                {
                    "status": "staged",
                    "stage_id": disposition.stage_ids[0],
                    "summary": (
                        f"Proposed {parsed_input.tool_name} on "
                        f"{parsed_input.server_name}; no external change has "
                        "been made."
                    ),
                }
            )
        elif disposition.outcome is OperationOutcome.BLOCKED:
            output["status"] = "blocked"
        else:
            output["status"] = "failed"
        return McpToolCallResult.ok(
            server_name=parsed_input.server_name,
            tool_name=parsed_input.tool_name,
            output=output,
        ).model_dump(mode="json", exclude_none=True)

    async def _authorize_mcp_dispatch(
        self,
        parsed_input: McpToolCallRequest,
        card: McpServerCard,
    ) -> dict[str, Any] | None:
        """Consult the PDP; return a refusal dict, or ``None`` to dispatch.

        ALLOW → ``None`` (fall through to execute-now). DENY → a typed refusal.
        GATE → park on the write-approval interrupt (reusing the run's
        ``ToolAccessGate``); on approve → ``None`` (execute in the SAME run), on
        reject → a typed refusal. When a run has no approval channel wired
        (``gate is None``), a GATE cannot be satisfied and fails closed to a
        typed refusal — never a silent dispatch, never a stage.
        """

        decision = McpDispatchPolicy.evaluate(
            card=card,
            server=parsed_input.server_name,
            tool=parsed_input.tool_name,
            arguments=parsed_input.arguments,
            context=self.runtime_context,
        )
        if decision.decision is PolicyDecision.ALLOW:
            return None
        if decision.decision is PolicyDecision.DENY:
            return self._policy_denied_result(parsed_input, decision.reason)
        # GATE — the write-approval interrupt (park + resume in the same run).
        if self.gate is None:
            return self._refusal(
                parsed_input,
                McpLoadErrorCode.PERMISSION_DENIED,
                _APPROVAL_UNAVAILABLE,
            )
        resume = await self.gate.park_for_approval(
            card=card,
            tool_name=parsed_input.tool_name,
            arguments=parsed_input.arguments,
            op_class=decision.descriptor.action.value,
            approval_id=self._write_approval_id(parsed_input),
        )
        if not resume.approved:
            return self._refusal(
                parsed_input,
                McpLoadErrorCode.PERMISSION_DENIED,
                _WRITE_DECLINED,
            )
        return None

    def _policy_denied_result(
        self, parsed_input: McpToolCallRequest, reason: str
    ) -> dict[str, Any]:
        """Map a PDP DENY reason code onto a typed, non-leaking refusal."""

        if reason == _CONNECTOR_UNAVAILABLE_REASON:
            return self._refusal(
                parsed_input,
                McpLoadErrorCode.SERVER_UNHEALTHY,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
            )
        return self._refusal(
            parsed_input,
            McpLoadErrorCode.PERMISSION_DENIED,
            Messages.Loader.UNAUTHORIZED_SERVER,
        )

    def _refusal(
        self,
        parsed_input: McpToolCallRequest,
        code: McpLoadErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        """Build a typed ``call_mcp_tool`` failure result."""

        return McpToolCallResult.fail(
            code,
            safe_message,
            retryable=retryable,
            server_name=parsed_input.server_name,
            tool_name=parsed_input.tool_name,
            correlation_id=self.runtime_context.trace_id,
        ).model_dump(mode="json", exclude_none=True)

    def _write_approval_id(self, parsed_input: McpToolCallRequest) -> str:
        """Deterministic id for the write-approval gate of this tool call."""

        suffix = parsed_input.tool_call_id or parsed_input.tool_name
        return f"mcp_write:{self.runtime_context.run_id}:{suffix}"

    async def __call__(
        self,
        raw_input: McpToolCallRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)


class CallMcpToolInputParser:
    """Parser for untrusted generic MCP tool invocation input."""

    @classmethod
    def parse(
        cls,
        raw_input: McpToolCallRequest | Mapping[str, Any],
        correlation_id: str,
    ) -> McpToolCallRequest | McpToolCallResult:
        """Validate ``raw_input`` into a typed request; return a failure result on error."""
        if isinstance(raw_input, McpToolCallRequest):
            return raw_input
        try:
            return McpToolCallRequest.model_validate(raw_input)
        except ValidationError:
            return McpToolCallResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                correlation_id=correlation_id,
            )
