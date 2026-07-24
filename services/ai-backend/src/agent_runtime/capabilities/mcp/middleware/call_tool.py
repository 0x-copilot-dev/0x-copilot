"""Model-facing tool that invokes a selected MCP tool after discovery."""

from __future__ import annotations

import asyncio
import logging
import time
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
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.surfaces.generator import (
    GenToolDescriptor,
    SurfaceGenerationScheduler,
)
from agent_runtime.capabilities.surfaces.projector import SurfaceProjector
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from agent_runtime.surfaces_v2.gate import ToolAccessGate
from agent_runtime.surfaces_v2.ledger_models import GateAuthState

_LOGGER = logging.getLogger(__name__)


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
        dispatch_latency_ms: int | None = None
        try:
            client = resolution.provider.create_client(resolution.card)
            dispatch_started = time.perf_counter()
            output = await asyncio.wait_for(
                client.call_tool(
                    tool_name=parsed_input.tool_name,
                    arguments=parsed_input.arguments,
                ),
                timeout=self.loader.timeout_seconds,
            )
            dispatch_latency_ms = int((time.perf_counter() - dispatch_started) * 1000)
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

        # Generative Surfaces v2 (PRD-A3/E3): record the executed read on the Work
        # Ledger. Awaited (not fire-and-forget) for deterministic event ordering;
        # a no-op unless a ``WorkLedgerEmitter`` is bound, which happens only when
        # ``SURFACES_V2`` is on. The v1 ``result["surface"]`` appendage was retired
        # in E3 — the surface envelope is now computed on-demand INSIDE
        # ``_emit_ledger`` (only when an emitter is bound) and handed straight to
        # the ledger; the tool result dict is never mutated with a surface.
        await CallMcpTool._emit_ledger(
            server_name=parsed_input.server_name,
            tool_name=parsed_input.tool_name,
            call_id=parsed_input.tool_call_id,
            output=output,
            latency_ms=dispatch_latency_ms,
        )
        return result

    @staticmethod
    async def _emit_ledger(
        *,
        server_name: str,
        tool_name: str,
        call_id: str | None,
        output: object,
        latency_ms: int | None,
    ) -> None:
        """Emit the v2 ledger read path for this tool call, if an emitter is bound.

        No-ops when no :class:`WorkLedgerEmitter` is active (``SURFACES_V2`` off ⇒
        no binding). When bound, computes the surface envelope on-demand
        (:meth:`_compute_surface_envelope` — the builtin → store → schedule-
        generation ladder that survives the v1 retirement) and hands it straight
        to the emitter so it can record ``surface.created`` / ``view.derived``.
        The v1 ``result["surface"]`` appendage no longer exists (E3 D4). The
        emitter swallows its own exceptions; this wrapper adds a second
        best-effort guard so a ledger emit can never break a tool result.
        """

        emitter = WorkLedgerEmitter.active()
        if emitter is None:
            return
        surface, surface_uri = CallMcpTool._compute_surface_envelope(
            server_name=server_name,
            tool_name=tool_name,
            output=output,
            call_id=call_id,
        )
        try:
            await emitter.on_tool_result(
                server_name=server_name,
                tool_name=tool_name,
                call_id=call_id or "",
                output=output,
                surface=surface,
                surface_uri=surface_uri,
                latency_ms=latency_ms,
            )
        except Exception:  # noqa: BLE001 - best-effort; never break MCP results
            _LOGGER.warning(
                "[surfaces_v2] mcp.ledger_raised server=%s tool=%s",
                server_name,
                tool_name,
                exc_info=True,
            )

    @staticmethod
    def _compute_surface_envelope(
        *,
        server_name: str,
        tool_name: str,
        output: object,
        call_id: str | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Compute the surface envelope for a tool result, for v2 ledger emission.

        Runs the builtin → store → schedule-generation ladder (via
        :meth:`_surface_projector`) and returns ``(surface_dump, surface_uri)``,
        or ``(None, None)`` when the output is non-mapping / the projector declines
        / it raises. Display-only and best-effort — surface projection never blocks
        a tool call. Invoked ONLY from :meth:`_emit_ledger`, i.e. only when a
        ``WorkLedgerEmitter`` is bound (``SURFACES_V2`` on); the v1
        ``RUNTIME_SURFACE_EMISSION`` gate is gone — v2 is the sole consumer and its
        own flag decides whether this runs at all.
        """

        if not isinstance(output, Mapping):
            return (None, None)
        try:
            projector, tool_descriptor = CallMcpTool._surface_projector(tool_name)
            envelope = projector.resolve(
                server_name,
                tool_name,
                output,
                call_id=call_id or None,
                tool_descriptor=tool_descriptor,
            )
            if envelope is None:
                return (None, None)
            return (
                envelope.model_dump(mode="json", exclude_none=True),
                envelope.surface_uri,
            )
        except Exception:  # noqa: BLE001 - best-effort; never break MCP results
            _LOGGER.warning(
                "[surfaces] mcp.surface_raised server=%s tool=%s",
                server_name,
                tool_name,
                exc_info=True,
            )
            return (None, None)

    @staticmethod
    def _surface_projector(
        tool_name: str,
    ) -> tuple[SurfaceProjector, GenToolDescriptor | None]:
        """Build the projector for this call, wiring generation when it is on.

        With no active scheduler (generation disabled), returns a bare projector
        — byte-for-byte the pre-PRD-07 behaviour. With one bound, shares its
        store for cache reads and passes a minimal tool descriptor for the prompt.
        """

        scheduler = SurfaceGenerationScheduler.active()
        if scheduler is None:
            return (SurfaceProjector(), None)
        return (
            SurfaceProjector(store=scheduler.store, scheduler=scheduler),
            GenToolDescriptor(name=tool_name),
        )

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
