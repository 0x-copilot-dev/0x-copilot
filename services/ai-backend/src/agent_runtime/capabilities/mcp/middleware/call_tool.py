"""Model-facing tool that invokes a selected MCP tool after discovery."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent_runtime.capabilities.citation_capturing_tool import _CitationHint
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.capabilities.mcp.cards import (
    McpLoadError,
    McpLoadErrorCode,
    McpToolCallRequest,
    McpToolCallResult,
)
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpTimeoutError,
)
from agent_runtime.capabilities.mcp.constants import Messages, Values
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.capabilities.mcp.middleware.cite_mcp import (
    CitationProjectingMcpMiddleware,
)
from agent_runtime.capabilities.mcp.outcomes import McpToolCallOutcome
from agent_runtime.capabilities.mcp.operation_adapter import (
    McpOperationAdapter,
    McpOperationGatewayContext,
    McpOperationGatewayServices,
)
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.probes import OperationShadowProbe
from agent_runtime.effects.contracts import EffectPolicySnapshot
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from agent_runtime.surfaces_v2.gate import ToolAccessGate
from agent_runtime.surfaces_v2.ledger_models import (
    EffectPolicy,
    GateAuthState,
    OperationOutcome,
)

_LOGGER = logging.getLogger(__name__)
_DESKTOP_BROWSER_SERVER = "desktop_browser"


@dataclass(frozen=True)
class CallMcpTool:
    """Invoke a tool from one previously discovered MCP server."""

    registry: DynamicMcpRegistry
    loader: McpLoader
    runtime_context: AgentRuntimeContext
    # Generative Surfaces v2 (PRD-C2): the ToolAccessGate parks the run at the
    # connector-dispatch boundary on missing/expired/insufficient auth. ``None``
    # ⇒ pre-C2 bytes (the flag-off / unwired path) — every gate branch below is
    # additionally guarded by ``SurfacesV2Flag.enabled()`` so the field being set
    # never changes behaviour with the flag off.
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
            return self._held_without_gateway(parsed_input)
        return await self._ainvoke_operation_gateway(parsed_input, services)

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

    async def _retired_direct_dispatch(self) -> None:
        """Tombstone retained only to make accidental legacy reachability explicit."""

        raise RuntimeError("direct model-facing MCP dispatch has been retired")

    async def _legacy_unreachable(
        self, parsed_input: McpToolCallRequest
    ) -> dict[str, Any]:
        """Unreachable compatibility code kept out of the model-facing route."""

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

        # Defense-in-depth: re-check authorization after registry resolve so a stale
        # tool reference from an earlier turn can't bypass per-chat pausing.
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

        # Generative Surfaces v2 (PRD-C2): gate at the connector-dispatch
        # boundary. When the connector's auth is not usable right now, park the
        # run on the mcp_auth interrupt seam BEFORE any client is created; a
        # cancelled gate returns a typed AUTH_FAILURE and the dependent call
        # never dispatches (fail closed). On resume the tool node re-executes
        # from the top with a fresh card — a now-valid auth returns ``None`` from
        # ``gate_state`` and dispatch proceeds (this IS "resume re-enters the
        # parked call"). Flag off / gate unwired ⇒ this whole block short-circuits
        # before any behaviour change (byte-identical).
        if SurfacesV2Flag.enabled() and self.gate is not None:
            gate_state = self.gate.gate_state(resolution.card)
            if gate_state is not None:
                resume = await self.gate.park(
                    card=resolution.card,
                    tool_name=parsed_input.tool_name,
                    arguments=parsed_input.arguments,
                    state=gate_state,
                )
                if not resume.approved:
                    return McpToolCallResult.fail(
                        McpLoadErrorCode.AUTH_FAILURE,
                        Messages.Loader.AUTH_FAILED,
                        server_name=parsed_input.server_name,
                        tool_name=parsed_input.tool_name,
                        correlation_id=self.runtime_context.trace_id,
                    ).model_dump(mode="json", exclude_none=True)

        # Wall time of the connector dispatch, for the v2 ``read.executed``
        # ledger event (PRD-A3 D1). Measured only around the dispatch itself so
        # citation/ordinal/surface work downstream does not inflate it. Unused
        # when ``SURFACES_V2`` is off (no emitter bound ⇒ ``_emit_ledger`` no-ops).
        try:

            async def _dispatch() -> object:
                await self._retired_direct_dispatch()
                raise AssertionError("retired direct MCP dispatch returned")

            output = await OperationShadowProbe.invoke_legacy(
                capability=parsed_input.server_name,
                op=parsed_input.tool_name,
                arguments=parsed_input.arguments,
                legacy=_dispatch,
                legacy_class=OperationShadowProbe.legacy_mcp_effect_class(
                    parsed_input.server_name,
                    parsed_input.tool_name,
                ),
            )
        except (McpTimeoutError, TimeoutError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.TIMEOUT,
                Messages.Loader.TIMEOUT,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except McpAuthError:
            # Mid-run revocation (PRD-C2): the card SAID authenticated but the
            # vendor rejected the dispatch. Flag on + gate wired ⇒ re-enter the
            # gate with ``EXPIRED`` instead of returning the terminal failure —
            # ``park`` raises the interrupt so the run parks in place; on resume
            # the node re-executes and the pre-dispatch gate handles the retry.
            # If ``park`` RETURNS (resume re-execution that still failed), fall
            # through to the fail-closed AUTH_FAILURE (never loop). Flag off /
            # gate unwired ⇒ byte-identical to the pre-C2 terminal failure.
            if SurfacesV2Flag.enabled() and self.gate is not None:
                await self.gate.park(
                    card=resolution.card,
                    tool_name=parsed_input.tool_name,
                    arguments=parsed_input.arguments,
                    state=GateAuthState.EXPIRED,
                )
            return McpToolCallResult.fail(
                McpLoadErrorCode.AUTH_FAILURE,
                Messages.Loader.AUTH_FAILED,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except PermissionError:
            return McpToolCallResult.fail(
                McpLoadErrorCode.AUTH_FAILURE,
                Messages.Loader.AUTH_FAILED,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpConnectionError, ConnectionError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.CONNECTION_FAILED,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpClientError, Exception):
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        # Project citation sources from the structured output. Best-effort;
        # the original output shape is preserved for JSON consumers.
        await CitationProjectingMcpMiddleware.project(
            connector=parsed_input.server_name,
            tool_call_id=self.runtime_context.trace_id,
            result=output,
        )

        # Classify protocol-level failures per the MCP spec: a successful HTTP
        # response carrying ``isError: true`` is a failure, not a "completed"
        # result. Preserve the full ``output`` envelope on the failure result so
        # the model can read the inner error text and self-correct.
        if McpToolCallOutcome.is_protocol_error(output):
            return McpToolCallResult.fail(
                McpLoadErrorCode.MCP_PROTOCOL_ERROR,
                McpToolCallOutcome.extract_error_text(output),
                retryable=False,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
                output=output,
            ).model_dump(mode="json", exclude_none=True)

        # Allocate a conversation-scoped ordinal bound to tool_call_id so the
        # citation resolver can stamp source_tool_call_id on citation_made events.
        # Best-effort: when no allocator is bound (replay/eval) or no tool_call_id
        # was injected (manual call sites), the output is returned unchanged.
        try:
            allocator = ConversationOrdinalAllocator.active()
            if allocator is None:
                _LOGGER.warning(
                    "[citations] mcp.hint_skipped server=%s tool=%s "
                    "reason=no_allocator_bound",
                    parsed_input.server_name,
                    parsed_input.tool_name,
                )
            elif not parsed_input.tool_call_id:
                _LOGGER.warning(
                    "[citations] mcp.hint_skipped server=%s tool=%s "
                    "reason=no_tool_call_id_injected (replay/eval path)",
                    parsed_input.server_name,
                    parsed_input.tool_name,
                )
            else:
                qualified_tool_name = (
                    f"{parsed_input.server_name}.{parsed_input.tool_name}"
                )
                ordinal = await allocator.allocate_for_tool_call(
                    tool_call_id=parsed_input.tool_call_id,
                    tool_name=qualified_tool_name,
                )
                hinted = _CitationHint.append_to(
                    output,
                    ordinal=ordinal,
                    tool_name=qualified_tool_name,
                )
                if isinstance(hinted, dict):
                    output = hinted
                _LOGGER.info(
                    "[citations] mcp.hint_appended server=%s tool=%s "
                    "ordinal=%d call_id=%s",
                    parsed_input.server_name,
                    parsed_input.tool_name,
                    ordinal,
                    parsed_input.tool_call_id,
                )
        except Exception:  # noqa: BLE001 - best-effort; never break MCP results
            _LOGGER.warning(
                "[citations] mcp.hint_raised server=%s tool=%s",
                parsed_input.server_name,
                parsed_input.tool_name,
                exc_info=True,
            )

        result = McpToolCallResult.ok(
            server_name=parsed_input.server_name,
            tool_name=parsed_input.tool_name,
            output=output,
        ).model_dump(mode="json", exclude_none=True)

        return result

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
        is_browser = (
            parsed_input.server_name.strip().lower().replace("-", "_")
            == _DESKTOP_BROWSER_SERVER
        )
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
                services=services,
                tool_call_id=parsed_input.tool_call_id,
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
