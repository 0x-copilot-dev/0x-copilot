"""Dark, compatibility-only MCP executor for approved universal effects.

This adapter deliberately reuses the existing :class:`McpStageCommitConnector`
transport seam rather than opening another MCP client path.  It accepts only an
``EffectExecutionRequest`` constructed by the A5 coordinator, resolves the
server-held canonical argument material, verifies both immutable digests, and
then sends those arguments verbatim through the connector's compatibility
``row_args`` lane.

It has no policy, staging, decision, or event-emission dependency.  The
coordinator remains responsible for approval, durable claiming, receipt
allocation, and ledger emission.  In particular, the connector's provider
receipt is never returned to the caller; a claim-scoped ``receipt://`` reference
can only be minted later by the coordinator.

This module belongs to the worker layer because it owns a real external-client
import.  The pure ``agent_runtime.effects`` domain is intentionally forbidden
from importing MCP transport seams.
"""

from __future__ import annotations

import asyncio
import json
from typing import Protocol, cast

from pydantic import Field, field_validator, model_validator

from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.executor import (
    EffectExecutionScope,
    EffectExecutorCapabilities,
    PreparedEffect,
)
from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import (
    CanonicalJsonError,
    canonical_json,
    canonical_json_sha256,
)
from agent_runtime.surfaces_v2.commit_engine import (
    StageCommitConnectorError,
    StageCommitRequest,
    StageCommitTimeout,
)
from agent_runtime.surfaces_v2.entities import (
    EffectExecutionRequest,
    EffectExecutionResult,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectExecutorKind,
    EffectOutcome,
    Sha256Hex,
)
from agent_runtime.surfaces_v2.mcp_connector import McpStageCommitConnector

_DISABLED = "Legacy MCP effect execution is disabled."
_MATERIAL_UNAVAILABLE = (
    "Approved connector arguments are unavailable; no external change was made."
)
_CONNECTOR_FAILED = "The connector could not apply the approved change."
_CONNECTOR_UNKNOWN = (
    "The connector outcome is unknown; it will not be sent again automatically."
)
_APPLIED = "The connector applied the approved change."


class LegacyMcpEffectExecutorDisabledError(RuntimeError):
    """Raised when composition attempts to enable the dark compatibility path."""

    safe_message = _DISABLED

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class LegacyMcpEffectMaterialError(RuntimeError):
    """Safe material-resolution failure before a connector call is possible."""

    safe_message = _MATERIAL_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class LegacyMcpEffectMaterial(RuntimeContract):
    """Server-resolved canonical arguments for exactly one approved MCP effect.

    The resolver is trusted composition infrastructure, not a model-facing
    service.  The adapter compares every immutable reference/digest to the
    coordinator's request before exposing these fields to the legacy connector.
    ``arguments`` remains a JSON object so it can be passed byte-for-byte through
    the existing connector's ``row_args`` compatibility lane.
    """

    target_connector: str = Field(min_length=1, max_length=255)
    target_op: str = Field(min_length=1, max_length=255)
    arguments: JsonObject
    target_ref: str = Field(min_length=1, max_length=2048)
    target_digest: Sha256Hex
    proposal_ref: str = Field(min_length=1, max_length=2048)
    proposal_digest: Sha256Hex

    @field_validator("target_connector", "target_op")
    @classmethod
    def _operation_names_are_stable(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("MCP target names must be stable identifiers")
        return value

    @model_validator(mode="after")
    def _arguments_match_canonical_digest(self) -> "LegacyMcpEffectMaterial":
        try:
            digest = canonical_json_sha256(self.arguments)
        except CanonicalJsonError as exc:
            raise ValueError("canonical MCP arguments are invalid") from exc
        if digest != self.proposal_digest:
            raise ValueError("canonical MCP arguments do not match proposal digest")
        return self


class LegacyMcpEffectMaterialResolver(Protocol):
    """Resolve immutable, server-held canonical arguments for one request."""

    async def resolve(
        self, request: EffectExecutionRequest
    ) -> LegacyMcpEffectMaterial | None:
        """Return the exact approved material, or ``None`` when unavailable."""


class LegacyMcpEffectExecutor:
    """Compatibility adapter around the existing ``McpStageCommitConnector``.

    It is deliberately constructor-gated and defaults to off.  Worker
    composition must opt in explicitly; model-facing code cannot accidentally
    create this executor through a prompt or operation descriptor.  The existing
    MCP seam does not expose provider-native idempotency metadata without
    modifying approved arguments, therefore durable A5 claims remain the sole
    idempotency authority for this adapter.
    """

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
        material_resolver: LegacyMcpEffectMaterialResolver,
        enabled: bool = False,
    ) -> None:
        if not enabled:
            raise LegacyMcpEffectExecutorDisabledError()
        self._scope = scope
        self._connector = connector
        self._material_resolver = material_resolver

    async def prepare(self, request: EffectExecutionRequest) -> PreparedEffect:
        """Validate immutable canonical material without touching the connector."""

        await self._resolve_exact_material(request)
        return PreparedEffect(request=request)

    async def apply(self, prepared: PreparedEffect) -> EffectExecutionResult:
        """Dispatch only exact, re-validated approved arguments once claimed."""

        request = prepared.request
        try:
            material = await self._resolve_exact_material(request)
        except LegacyMcpEffectMaterialError:
            # The coordinator has already claimed by this point, but no external
            # call has happened.  Keep the material failure safe and terminal.
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
        except Exception:  # noqa: BLE001 -- an unknown transport fault may be sent.
            return EffectExecutionResult(
                outcome=EffectOutcome.INDETERMINATE,
                retryable=False,
                safe_message=_CONNECTOR_UNKNOWN,
            )

        # ``external_ref`` remains private connector evidence.  A public,
        # claim-scoped receipt requires the claim id and is minted only by the
        # coordinator/result recorder after this return.
        del connector_result
        return EffectExecutionResult(
            outcome=EffectOutcome.APPLIED,
            retryable=False,
            safe_message=_APPLIED,
        )

    async def reconcile(self, claim: EffectClaim) -> EffectExecutionResult:
        """Never replay a legacy MCP request while its outcome is uncertain."""

        del claim
        return EffectExecutionResult(
            outcome=EffectOutcome.INDETERMINATE,
            retryable=False,
            safe_message=_CONNECTOR_UNKNOWN,
        )

    async def abort(self, prepared: PreparedEffect) -> None:
        """Prepare only reads canonical material, so it leaves nothing to release."""

        del prepared

    async def _resolve_exact_material(
        self, request: EffectExecutionRequest
    ) -> LegacyMcpEffectMaterial:
        try:
            material = await self._material_resolver.resolve(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- resolver detail is never public.
            raise LegacyMcpEffectMaterialError() from exc
        if material is None or not self._material_matches(request, material):
            raise LegacyMcpEffectMaterialError()
        return material

    @staticmethod
    def _material_matches(
        request: EffectExecutionRequest, material: LegacyMcpEffectMaterial
    ) -> bool:
        return (
            material.target_ref == request.target_ref
            and material.target_digest == request.target_digest
            and material.proposal_ref == request.proposal_ref
            and material.proposal_digest == request.proposal_digest
        )

    def _stage_commit_request(
        self,
        *,
        request: EffectExecutionRequest,
        material: LegacyMcpEffectMaterial,
    ) -> StageCommitRequest:
        """Bridge exact canonical JSON into the current connector unchanged.

        The D3 ``row_args`` lane is intentionally the generic-argument bridge:
        ``StageCommitRequest.tool_arguments`` returns it verbatim and skips the
        draft-specific body/title/metadata construction.
        """

        try:
            copied_arguments = json.loads(canonical_json(material.arguments))
        except (CanonicalJsonError, json.JSONDecodeError) as exc:
            raise LegacyMcpEffectMaterialError() from exc
        if not isinstance(copied_arguments, dict):  # defensive canonical boundary
            raise LegacyMcpEffectMaterialError()
        return StageCommitRequest(
            org_id=self._scope.org_id,
            user_id=self._scope.user_id,
            run_id=self._scope.run_id,
            conversation_id=self._scope.conversation_id or "",
            stage_id=request.stage_id,
            rev=request.revision,
            # This compatibility bridge does not use the v2 commit key.  The A5
            # durable effect claim remains the sole idempotency key.
            decision_seq=0,
            target_connector=material.target_connector,
            target_op=material.target_op,
            body="",
            row_args=cast(JsonObject, copied_arguments),
        )


__all__ = [
    "LegacyMcpEffectExecutor",
    "LegacyMcpEffectExecutorDisabledError",
    "LegacyMcpEffectMaterial",
    "LegacyMcpEffectMaterialError",
    "LegacyMcpEffectMaterialResolver",
]
