"""MCP adapter for the universal Operation Gateway.

This module is the deliberately narrow bridge between the model-facing
``call_mcp_tool`` tool and the v2.1 operation/effect contracts.  It owns no
executor and exposes no approval or apply operation.  Reads are the only branch
which can create an MCP client; write, destructive, and unknown operations stage
the exact canonical argument bytes already held by :class:`OperationContext`.

The runtime worker binds its trusted gateway composition whenever durable
execution is available. An unbound context is fail-closed: ``CallMcpTool``
holds the request rather than restoring a retired direct-dispatch path.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from pathlib import Path

from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    CatalogActionKind,
    ClassificationBasis,
    ClassifiedAction,
)
from agent_runtime.capabilities.actions.policy import EffectiveActionPolicyResolver
from agent_runtime.capabilities.mcp.cards import McpLoadError, McpServerCard
from agent_runtime.capabilities.mcp.constants import Limits
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpAmbiguousDispatchError,
    McpClientError,
    McpConnectionError,
    McpLeaseError,
    McpNotFoundError,
    McpRequestRejectedError,
    McpTimeoutError,
    aclose_mcp_client_safely,
)
from agent_runtime.capabilities.mcp.middleware.cite_mcp import (
    CitationProjectingMcpMiddleware,
)
from agent_runtime.capabilities.mcp.material_resolver import (
    McpOperationArgumentMaterialResolver,
)
from agent_runtime.capabilities.mcp.execution_services import (
    McpOperationArgumentStorePort,
    McpOperationExecutionServices,
    McpOperationResultStorePort,
    McpOperationStoredResult,
)
from agent_runtime.capabilities.mcp.outcomes import McpToolCallOutcome
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.mcp.registry import (
    DynamicMcpRegistry,
    RegisteredMcpServer,
)
from agent_runtime.capabilities.mcp.target_ref import McpTargetRef, McpTargetRefCodec
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationAdapter,
    OperationClassification,
    OperationRawResult,
    OperationRequest,
    ProposedEffect as GatewayProposedEffect,
)
from agent_runtime.capabilities.operations.errors import (
    OperationGatewayError,
    OperationGatewayErrorCode,
)
from agent_runtime.capabilities.operations.presentation_boundary import (
    assert_transport_adapter_presentation_boundary,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicyMode
from agent_runtime.effects.contracts import EffectPolicySnapshot, ProposedEffect
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.tool_error_sanitizer import ErrorSanitizer
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    sha256_hex,
)
from agent_runtime.surfaces_v2.entities import EffectTarget, OperationDescriptor
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
    OperationClassificationBasis,
)
from agent_runtime.surfaces_v2.gate import ToolAccessGate
from agent_runtime.surfaces_v2.ledger_models import GateAuthState

_CANONICAL_JSON_MEDIA_TYPE = "application/json"
_AUTH_REQUIRED = "Authentication is required before this connector call."
_CONNECTOR_UNAVAILABLE = "The connector is unavailable; no external change was made."
_CONNECTOR_TIMEOUT = "The connector timed out; no external change was made."
_CONNECTOR_PROTOCOL_ERROR = (
    "The connector reported an error; no external change was made."
)
# The three below exist because "the connector is unavailable" was being used
# for auth expiry, a missing connector, and a malformed request alike. Only one
# of those is a wait-and-retry situation, and none of them is the same
# instruction to the user, so they no longer share a sentence.
_CONNECTOR_AUTH_EXPIRED = (
    "The connector's authentication is no longer valid; reconnect it to "
    "continue. No external change was made, and retrying without "
    "reconnecting will fail the same way."
)
_CONNECTOR_NOT_FOUND = (
    "The connector could not be found — it may not be installed or may have "
    "been removed. No external change was made. This is not a temporary "
    "outage and retrying will not change it."
)
_CONNECTOR_REQUEST_REJECTED = (
    "The connector rejected the request as invalid. It replied, so this is "
    "not an outage and not temporary: the same call returns the same error. "
    "No external change was made."
)
_RETRYABLE_ACQUISITION_LEASE_CODES = frozenset({"pool_saturated", "server_unavailable"})


class McpOperationGateResolver:
    """Acknowledge gates already enforced at the MCP dispatch boundary.

    ``CallMcpTool`` resolves the current card and permission before invoking the
    gateway. Reads re-check authentication immediately before client creation;
    effects never create a client and A4 resolves their immutable policy into a
    held stage. This resolver therefore does not grant a new capability—it
    prevents descriptor metadata from blocking the already-verified boundary.
    """

    async def resolve(
        self,
        *,
        request: OperationRequest,
        descriptor: OperationDescriptor,
        classification: OperationClassification,
    ) -> GateResolution:
        del request, descriptor, classification
        return GateResolution(allowed=True)


class McpOperationAdapter(OperationAdapter):
    """Execute MCP reads or stage exact immutable canonical MCP arguments."""

    def __init__(
        self,
        *,
        registry: DynamicMcpRegistry,
        runtime_context: AgentRuntimeContext,
        timeout_seconds: float,
        server_name: str,
        tool_name: str,
        arguments: Mapping[str, object],
        gate: ToolAccessGate | None,
        execution: McpOperationExecutionServices,
        tool_call_id: str | None,
    ) -> None:
        # Python permits local and dynamic imports, so a test-only import
        # convention is insufficient.  Verify this provider module's source
        # before it gains any registry/client capability.
        assert_transport_adapter_presentation_boundary(Path(__file__))
        self._registry = registry
        self._runtime_context = runtime_context
        self._timeout_seconds = timeout_seconds
        self._server_name = server_name
        self._tool_name = tool_name
        self._arguments = dict(arguments)
        self._gate = gate
        self._execution = execution
        self._tool_call_id = tool_call_id
        self._stored_result: McpOperationStoredResult | None = None
        self._protocol_error: str | None = None

    @property
    def stored_result(self) -> McpOperationStoredResult | None:
        """Return the persisted read projection after a successful read."""

        return self._stored_result

    @property
    def protocol_error(self) -> str | None:
        """Return the connector's own error text after a protocol failure.

        Set only when a dispatched read came back flagged ``isError`` by the
        server, and already sanitized (secrets, connection strings, internal
        ids stripped; resource ids the server returned kept). ``CallMcpTool``
        reads it to surface the connector's real message on the failure
        result. It travels on this side-channel — mirroring ``stored_result``
        for the success case — and never through the gateway disposition,
        whose failure summary is contractually generic (see
        ``OperationDisposition.failure_code``: nothing from an exception body
        reaches the model).
        """

        return self._protocol_error

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        """Revalidate, gate, dispatch exactly once, and persist the result."""

        resolution = await self._resolve_authorized()
        await self._require_current_auth(resolution.card)
        dispatch_started = time.perf_counter()
        output = await self._dispatch(resolution)
        dispatch_latency_ms = max(
            0, int((time.perf_counter() - dispatch_started) * 1000)
        )
        if McpToolCallOutcome.is_protocol_error(output):
            # Preserve the connector's OWN error text so the model can read what
            # the server actually rejected — an invalid argument, a missing
            # field, a "column does not exist" — and self-correct, instead of
            # seeing only a generic "the connector reported an error" and
            # retrying the same bad call. The raw envelope is untrusted (it may
            # carry a connection string or an internal id), so it is scrubbed
            # before it can reach the model, and it is stashed on the adapter
            # side-channel rather than the raised error: the gateway's failure
            # disposition stays generic by contract, and CallMcpTool lifts this
            # sanitized text onto the failure result.
            self._protocol_error = (
                ErrorSanitizer.sanitize_text(
                    McpToolCallOutcome.extract_error_text(output),
                    max_length=Limits.SAFE_MESSAGE_MAX_LENGTH,
                ).strip()
                or _CONNECTOR_PROTOCOL_ERROR
            )
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_PROTOCOL_ERROR,
                retryable=False,
            )
        await CitationProjectingMcpMiddleware.project(
            connector=self._server_name,
            tool_call_id=self._tool_call_id or self._runtime_context.trace_id,
            result=output,
        )
        self._stored_result = await self._execution.result_store.store_read_result(
            request=request,
            output=output,
        )
        if (
            self._stored_result.result_ref
            != f"operation://{request.operation_id}/result"
        ):
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                "The connector result could not be stored safely.",
                retryable=True,
            )
        return OperationRawResult(
            result_ref=self._stored_result.result_ref,
            safe_summary=f"Fetched {request.op} from {request.capability}.",
            activity_ref=self._stored_result.result_ref,
            result_payload={str(key): value for key, value in output.items()},
            latency_ms=dispatch_latency_ms,
        )

    async def build_proposal(self, request: OperationRequest) -> GatewayProposedEffect:
        """Stage canonical arguments without creating an MCP client."""

        context = OperationContext.require()
        if self._execution.stage_scope.run_id != request.run_id:
            raise OperationGatewayError(
                OperationGatewayErrorCode.IDENTITY_MISMATCH,
                "The requested change could not be staged safely.",
                retryable=False,
            )
        stored = context.arguments.get(request.canonical_args_ref)
        if stored is None:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ARGUMENTS_MISSING,
                "The requested change could not be staged safely.",
                retryable=True,
            )
        digest, canonical_bytes = stored
        if digest != request.args_digest or sha256_hex(canonical_bytes) != digest:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ARGUMENTS_DIGEST_MISMATCH,
                "The requested change could not be staged safely.",
                retryable=False,
            )
        try:
            decoded = json.loads(canonical_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ARGUMENTS_DIGEST_MISMATCH,
                "The requested change could not be staged safely.",
                retryable=False,
            ) from exc
        if (
            not isinstance(decoded, dict)
            or canonical_json_bytes(decoded) != canonical_bytes
        ):
            raise OperationGatewayError(
                OperationGatewayErrorCode.ARGUMENTS_DIGEST_MISMATCH,
                "The requested change could not be staged safely.",
                retryable=False,
            )

        classification = self._execution.classifier.classify(
            request,
            annotations=McpToolAnnotationsRegistry.get(request.capability, request.op),
        )
        if classification.effect_class in {
            EffectClass.NONE,
            EffectClass.INTERNAL_REVERSIBLE,
        }:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                "The connector operation is not stageable.",
                retryable=False,
            )
        target_ref = McpTargetRefCodec.format(
            capability=request.capability,
            op=request.op,
        )
        target = EffectTarget(
            executor=EffectExecutorKind.MCP,
            capability=request.capability,
            op=request.op,
            target_ref=target_ref,
            display_label=f"{request.op} on {request.capability}",
        )
        target_digest = sha256_hex(
            canonical_json_bytes({"capability": request.capability, "op": request.op})
        )
        snapshot = self._policy_snapshot(
            request=request,
            classification=classification,
        )
        staged = await self._execution.stager.stage(
            scope=self._execution.stage_scope,
            proposed_effect=ProposedEffect(
                operation_id=request.operation_id,
                executor=EffectExecutorKind.MCP,
                target=target,
                target_digest=target_digest,
                display_target=target.display_label,
                proposal_kind=EffectProposalKind.CANONICAL_ARGUMENTS,
                proposal_content_ref=request.canonical_args_ref,
                proposal_digest=request.args_digest,
                proposal_media_type=_CANONICAL_JSON_MEDIA_TYPE,
                effect_class=classification.effect_class,
                policy_snapshot_ref=snapshot.snapshot_ref,
            ),
            policy_snapshot=snapshot,
            actor=self._execution.stage_author,
            idempotency_key=f"mcp-stage:{request.operation_id}",
        )
        revision = staged.current_revision
        return GatewayProposedEffect(
            stage_id=staged.stage_id,
            proposal_ref=revision.proposal_ref,
            safe_summary=(
                f"Proposed {request.op} on {request.capability}; "
                "no external change has been made."
            ),
            activity_ref=revision.proposal_ref,
        )

    def _policy_snapshot(
        self,
        *,
        request: OperationRequest,
        classification: OperationClassification,
    ) -> EffectPolicySnapshot:
        context = OperationContext.require()
        action = self._action_for_policy(request, classification)
        effective = EffectiveActionPolicyResolver(
            snapshot=context.policy_snapshot,
            overrides=self._execution.connector_overrides,
        ).resolve(action)
        mode = {
            ToolUsePolicyMode.AUTO: EffectPolicy.AUTO,
            ToolUsePolicyMode.ASK: EffectPolicy.ASK,
            ToolUsePolicyMode.REQUIRE: EffectPolicy.REQUIRE,
            ToolUsePolicyMode.BLOCK: EffectPolicy.BLOCK,
        }[effective.mode]
        descriptor_known = (
            self._execution.descriptors.resolve(request.capability, request.op)
            is not None
        )
        return EffectPolicySnapshot(
            snapshot_ref=f"policy://runs/{request.run_id}/mcp",
            descriptor_known=descriptor_known,
            user_policy=mode,
            allow_always=effective.bypass,
        )

    @staticmethod
    def _action_for_policy(
        request: OperationRequest,
        classification: OperationClassification,
    ) -> ClassifiedAction:
        """Map the gateway verdict to C1's policy axes without loosening it."""

        if classification.effect_class in {
            EffectClass.NONE,
            EffectClass.INTERNAL_REVERSIBLE,
        }:
            return ClassifiedAction(
                connector=request.capability,
                op=request.op,
                action_class=ActionClass.READ,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=CatalogActionKind.READ,
            )
        if (
            classification.basis is OperationClassificationBasis.PROVIDER_ANNOTATION
            and classification.effect_class is EffectClass.EXTERNAL_DESTRUCTIVE
        ):
            return ClassifiedAction(
                connector=request.capability,
                op=request.op,
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.ANNOTATION,
            )
        if classification.basis in {
            OperationClassificationBasis.DESCRIPTOR,
            OperationClassificationBasis.CATALOG,
        }:
            kind = (
                CatalogActionKind.DESTRUCTIVE
                if classification.effect_class is EffectClass.EXTERNAL_DESTRUCTIVE
                else CatalogActionKind.WRITE
            )
            return ClassifiedAction(
                connector=request.capability,
                op=request.op,
                action_class=ActionClass.WRITE,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=kind,
            )
        return ClassifiedAction(
            connector=request.capability,
            op=request.op,
            action_class=ActionClass.WRITE,
            basis=ClassificationBasis.DEFAULT,
        )

    async def _resolve_authorized(self) -> RegisteredMcpServer:
        """Re-resolve the card immediately before a read client is created."""

        resolution = await self._registry.resolve_server(self._server_name)
        if isinstance(
            resolution, McpLoadError
        ) or not McpPermissionPolicy.is_server_card_authorized(
            self._runtime_context,
            resolution.card,
        ):
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_UNAVAILABLE,
                retryable=False,
            )
        return resolution

    async def _require_current_auth(self, card: McpServerCard) -> None:
        if self._gate is None:
            return
        gate_state = self._gate.gate_state(card)
        if gate_state is None:
            return
        resume = await self._gate.park(
            card=card,
            tool_name=self._tool_name,
            arguments=self._arguments,
            state=gate_state,
        )
        if not resume.approved:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _AUTH_REQUIRED,
                retryable=False,
            )

    async def _dispatch(self, resolution: RegisteredMcpServer) -> Mapping[str, object]:
        client = None
        cancel_client = True
        try:
            client = resolution.provider.create_client(resolution.card)
            output = await asyncio.wait_for(
                client.call_tool(
                    tool_name=self._tool_name,
                    arguments=self._arguments,
                ),
                timeout=self._timeout_seconds,
            )
            cancel_client = False
        except (McpTimeoutError, asyncio.TimeoutError, TimeoutError) as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_TIMEOUT,
                retryable=True,
            ) from exc
        except McpLeaseError as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_UNAVAILABLE,
                retryable=(
                    exc.acquisition_safe
                    and exc.code in _RETRYABLE_ACQUISITION_LEASE_CODES
                ),
            ) from exc
        except McpAmbiguousDispatchError as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_UNAVAILABLE,
                retryable=False,
            ) from exc
        # A 4xx is caught before the connection family: the connector answered
        # and refused, so "unavailable" is the wrong word and a retry is the
        # wrong advice. ``McpNotFoundError`` narrows to the missing-connector
        # case, which the user CAN act on by connecting the tool.
        except McpNotFoundError as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_NOT_FOUND,
                retryable=False,
            ) from exc
        except McpRequestRejectedError as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_REQUEST_REJECTED,
                retryable=False,
            ) from exc
        except McpAuthError as exc:
            # A connector can revoke credentials between the pre-dispatch gate
            # and this read.  Re-enter the same auth gate; a rejected resume
            # becomes a safe failed operation and never loops into dispatch.
            if self._gate is not None:
                await self._gate.park(
                    card=resolution.card,
                    tool_name=self._tool_name,
                    arguments=self._arguments,
                    state=GateAuthState.EXPIRED,
                )
            # Auth is the one failure class here the user can actually fix, and
            # calling it "unavailable" hid the only useful instruction —
            # reconnect — behind a word that suggests waiting.
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_AUTH_EXPIRED,
                retryable=False,
            ) from exc
        except (PermissionError, McpConnectionError, ConnectionError) as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_UNAVAILABLE,
                retryable=True,
            ) from exc
        except McpClientError as exc:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_UNAVAILABLE,
                retryable=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - unknown dispatch faults stay safe.
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_UNAVAILABLE,
                retryable=True,
            ) from exc
        finally:
            if client is not None:
                await aclose_mcp_client_safely(client, cancel=cancel_client)
        if not isinstance(output, Mapping):
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                _CONNECTOR_PROTOCOL_ERROR,
                retryable=False,
            )
        return {str(key): value for key, value in output.items()}


__all__ = [
    "McpOperationAdapter",
    "McpOperationArgumentMaterialResolver",
    "McpOperationGateResolver",
    "McpOperationArgumentStorePort",
    "McpOperationResultStorePort",
    "McpOperationStoredResult",
    "McpTargetRef",
    "McpTargetRefCodec",
]
