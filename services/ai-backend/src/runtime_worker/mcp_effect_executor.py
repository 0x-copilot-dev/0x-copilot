"""Production MCP executor for approved universal effects.

It owns the only MCP mutation transport: a claimed A5 execution request is
matched against server-held canonical material and those exact arguments are
sent once through the connector's generic ``row_args`` lane.  It has no model
text, policy, revision, staging, or event-emission capability.
"""

from __future__ import annotations

import asyncio
import json
from typing import Protocol, cast

from agent_runtime.capabilities.mcp.effect_material import McpEffectMaterial
from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.executor import (
    EffectExecutionScope,
    EffectExecutorCapabilities,
    PreparedEffect,
)
from agent_runtime.execution.contracts import JsonObject
from agent_runtime.surfaces_v2.canonical_json import CanonicalJsonError, canonical_json
from agent_runtime.surfaces_v2.commit_engine import (
    StageCommitConnectorError,
    StageCommitRequest,
    StageCommitTimeout,
)
from agent_runtime.surfaces_v2.entities import (
    EffectExecutionRequest,
    EffectExecutionResult,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind, EffectOutcome
from agent_runtime.surfaces_v2.mcp_connector import McpStageCommitConnector

_DISABLED = "MCP effect execution is disabled."
_MATERIAL_UNAVAILABLE = (
    "Approved connector arguments are unavailable; no external change was made."
)
_CONNECTOR_FAILED = "The connector could not apply the approved change."
_CONNECTOR_UNKNOWN = (
    "The connector outcome is unknown; it will not be sent again automatically."
)
_APPLIED = "The connector applied the approved change."


class McpEffectExecutorDisabledError(RuntimeError):
    """Raised until worker composition explicitly enables the executor."""

    safe_message = _DISABLED

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class McpEffectMaterialError(RuntimeError):
    """Safe failure before an MCP call is possible."""

    safe_message = _MATERIAL_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class McpEffectMaterialResolver(Protocol):
    """Resolve immutable, server-held canonical arguments for one request."""

    async def resolve(
        self, request: EffectExecutionRequest
    ) -> McpEffectMaterial | None:
        """Return exact approved material, or ``None`` when unavailable."""


class McpEffectExecutor:
    """Apply only a claimed, exact, revalidated MCP effect once."""

    kind = EffectExecutorKind.MCP
    capabilities = EffectExecutorCapabilities(
        supports_prepare=True,
        supports_reconcile=False,
        native_idempotency=False,
        prepare_performs_mutation=False,
    )

    def __init__(
        self,
        *,
        scope: EffectExecutionScope,
        connector: McpStageCommitConnector,
        material_resolver: McpEffectMaterialResolver,
        enabled: bool = False,
    ) -> None:
        if not enabled:
            raise McpEffectExecutorDisabledError()
        self._scope = scope
        self._connector = connector
        self._material_resolver = material_resolver

    async def prepare(self, request: EffectExecutionRequest) -> PreparedEffect:
        """Validate immutable canonical material without touching a connector."""

        await self._resolve_exact_material(request)
        return PreparedEffect(request=request)

    async def apply(self, prepared: PreparedEffect) -> EffectExecutionResult:
        """Dispatch exact approved bytes once, after A5 has claimed the work."""

        request = prepared.request
        try:
            material = await self._resolve_exact_material(request)
        except McpEffectMaterialError:
            return EffectExecutionResult(
                outcome=EffectOutcome.FAILED,
                retryable=False,
                safe_message=_MATERIAL_UNAVAILABLE,
            )
        try:
            connector_result = await self._connector.execute(
                self._stage_commit_request(request=request, material=material)
            )
        except asyncio.CancelledError:
            raise
        except StageCommitTimeout:
            return EffectExecutionResult(
                outcome=EffectOutcome.INDETERMINATE,
                retryable=False,
                safe_message=_CONNECTOR_UNKNOWN,
            )
        except StageCommitConnectorError:
            return EffectExecutionResult(
                outcome=EffectOutcome.FAILED,
                retryable=False,
                safe_message=_CONNECTOR_FAILED,
            )
        except Exception:  # noqa: BLE001 -- unknown sends are never replayed.
            return EffectExecutionResult(
                outcome=EffectOutcome.INDETERMINATE,
                retryable=False,
                safe_message=_CONNECTOR_UNKNOWN,
            )

        # Provider evidence remains private.  A later result recorder mints the
        # claim-scoped receipt reference only after this exact attempt returns.
        del connector_result
        return EffectExecutionResult(
            outcome=EffectOutcome.APPLIED,
            retryable=False,
            safe_message=_APPLIED,
        )

    async def reconcile(self, claim: EffectClaim) -> EffectExecutionResult:
        """Never replay a provider request while its outcome is uncertain."""

        del claim
        return EffectExecutionResult(
            outcome=EffectOutcome.INDETERMINATE,
            retryable=False,
            safe_message=_CONNECTOR_UNKNOWN,
        )

    async def abort(self, prepared: PreparedEffect) -> None:
        """Prepare is a read-only material validation, so no cleanup is needed."""

        del prepared

    async def _resolve_exact_material(
        self, request: EffectExecutionRequest
    ) -> McpEffectMaterial:
        try:
            material = await self._material_resolver.resolve(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- resolver detail is private.
            raise McpEffectMaterialError() from exc
        if material is None or not self._material_matches(request, material):
            raise McpEffectMaterialError()
        return material

    @staticmethod
    def _material_matches(
        request: EffectExecutionRequest, material: McpEffectMaterial
    ) -> bool:
        return (
            material.target_ref == request.target_ref
            and material.target_digest == request.target_digest
            and material.proposal_ref == request.proposal_ref
            and material.proposal_content_ref == request.proposal_content_ref
            and material.proposal_digest == request.proposal_digest
        )

    def _stage_commit_request(
        self,
        *,
        request: EffectExecutionRequest,
        material: McpEffectMaterial,
    ) -> StageCommitRequest:
        """Pass an independently re-canonicalized object through unchanged."""

        try:
            copied_arguments = json.loads(canonical_json(material.arguments))
        except (CanonicalJsonError, json.JSONDecodeError) as exc:
            raise McpEffectMaterialError() from exc
        if not isinstance(copied_arguments, dict):
            raise McpEffectMaterialError()
        return StageCommitRequest(
            org_id=self._scope.org_id,
            user_id=self._scope.user_id,
            run_id=self._scope.run_id,
            conversation_id=self._scope.conversation_id or "",
            stage_id=request.stage_id,
            rev=request.revision,
            # Durable A5 effect claims own idempotency.  The existing connector
            # bridge must receive no synthetic model-authored argument.
            decision_seq=0,
            target_connector=material.target_connector,
            target_op=material.target_op,
            body="",
            row_args=cast(JsonObject, copied_arguments),
        )


__all__ = [
    "McpEffectExecutor",
    "McpEffectExecutorDisabledError",
    "McpEffectMaterial",
    "McpEffectMaterialError",
    "McpEffectMaterialResolver",
]
