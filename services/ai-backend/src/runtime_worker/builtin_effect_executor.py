"""Closed executor for the one currently stageable external builtin.

``stage_rowset_write`` is model-visible only as a proposal builder.  Once a
user approves its generic A4 stage, this worker-only executor is the sole path
that can turn the digest-pinned row-set bytes into connector calls.  It is not
a generic builtin dispatcher: every body except the typed row-set shape fails
during ``prepare`` before a connector is constructed or called.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Protocol

from agent_runtime.capabilities.mcp.operation_adapter import (
    McpOperationArgumentStorePort,
)
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
    reviewed_rowset_target,
)
from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.contracts import EffectDispatchRequest
from agent_runtime.effects.executor import (
    EffectExecutionAuthorization,
    EffectExecutionScope,
    EffectExecutorCapabilities,
    PreparedEffect,
)
from agent_runtime.surfaces_v2.canonical_json import (
    CanonicalJsonError,
    canonical_json,
    canonical_json_bytes,
    sha256_hex,
)
from agent_runtime.surfaces_v2.commit_engine import (
    StageCommitConnectorError,
    StageCommitRequest,
    StageCommitTimeout,
)
from agent_runtime.surfaces_v2.entities import EffectExecutionResult
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind, EffectOutcome
from agent_runtime.surfaces_v2.rowset import (
    RowsetValidationError,
    RowsetValidator,
    StagedRow,
)
from agent_runtime.surfaces_v2.mcp_connector import McpStageCommitConnector

_UNAVAILABLE = "Approved row-set content is unavailable; no external change was made."
_CONNECTOR_FAILED = "The connector could not apply the approved row-set."
_PARTIAL = "Some approved rows were not applied; held rows were not sent."
_UNKNOWN = "The row-set outcome is unknown; it will not be sent again automatically."


class BuiltinRowSetMaterialError(RuntimeError):
    """Typed, body-free failure raised before a connector call is possible."""

    safe_message = _UNAVAILABLE

    def __init__(self) -> None:
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class BuiltinRowSetEffectMaterial:
    """Exact, immutable row-set material authorised by one effect request."""

    target_connector: str
    target_op: str
    rows: tuple[StagedRow, ...]
    held_row_keys: frozenset[str]
    target_ref: str
    target_digest: str
    proposal_ref: str
    proposal_content_ref: str
    proposal_digest: str


class BuiltinRowSetMaterialResolver(Protocol):
    """Resolve only the one row-set proposal shape this executor understands."""

    async def resolve(
        self, request: EffectDispatchRequest
    ) -> BuiltinRowSetEffectMaterial | None:
        """Return digest-pinned material or ``None`` without leaking its body."""


@dataclass(frozen=True)
class RuntimeBuiltinRowSetMaterialResolver:
    """Reconstruct and verify typed row-set content from the immutable store."""

    arguments: McpOperationArgumentStorePort

    async def resolve(
        self, request: EffectDispatchRequest
    ) -> BuiltinRowSetEffectMaterial | None:
        try:
            body = await self.arguments.resolve(
                ref=request.proposal_content_ref,
                digest=request.proposal_digest,
            )
            if body is None or sha256_hex(body) != request.proposal_digest:
                return None
            parsed = json.loads(body)
            proposal = RowSetEffectProposal.model_validate(parsed)
            # Reject alternate JSON representations: a body can only reach the
            # connector when it is the canonical bytes the user approved.
            if canonical_json_bytes(proposal.model_dump(mode="json")) != body:
                return None
            RowsetValidator.validate(
                rows=proposal.rows, agent_holds=proposal.agent_holds
            )
            target = reviewed_rowset_target(
                target_connector=proposal.target_connector,
                target_op=proposal.target_op,
            )
            if target is None:
                return None
            connector, op = target
            expected_target_digest = sha256_hex(
                canonical_json_bytes({"capability": connector, "op": op})
            )
            if (
                request.target_digest != expected_target_digest
                or request.target_ref != f"rowset-target://{expected_target_digest}"
            ):
                return None
            return BuiltinRowSetEffectMaterial(
                target_connector=connector,
                target_op=op,
                rows=proposal.rows,
                held_row_keys=frozenset(hold.row_key for hold in proposal.agent_holds),
                target_ref=request.target_ref,
                target_digest=request.target_digest,
                proposal_ref=request.proposal_ref,
                proposal_content_ref=request.proposal_content_ref,
                proposal_digest=request.proposal_digest,
            )
        except (
            CanonicalJsonError,
            RowsetValidationError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ):
            return None
        except Exception:
            # Storage failures are intentionally indistinguishable from missing
            # material to a caller; no request is eligible for dispatch.
            return None


class BuiltinRowSetEffectExecutor:
    """Dispatch only exact, non-held rows after the coordinator has claimed them."""

    kind = EffectExecutorKind.BUILTIN
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
        material_resolver: BuiltinRowSetMaterialResolver,
    ) -> None:
        self._scope = scope
        self._connector = connector
        self._material_resolver = material_resolver

    async def prepare(self, request: EffectDispatchRequest) -> PreparedEffect:
        await self._resolve_exact_material(request)
        return PreparedEffect(
            request=request,
            prepared_ref=(
                f"builtin-rowset://effects/{request.stage_id}/{request.idempotency_key}"
            ),
        )

    async def apply(self, prepared: PreparedEffect) -> EffectExecutionResult:
        request = prepared.request
        try:
            material = await self._resolve_exact_material(request)
        except BuiltinRowSetMaterialError:
            return EffectExecutionResult(
                outcome=EffectOutcome.FAILED,
                retryable=False,
                safe_message=_UNAVAILABLE,
            )

        dispatched = 0
        for row in material.rows:
            # An agent hold is sticky until a later row-scoped canonical stage
            # exists. This generic stage has no authority to silently override
            # it merely because its parent was approved.
            if row.row_key in material.held_row_keys:
                continue
            try:
                await self._connector.execute(
                    self._request_for_row(request=request, material=material, row=row)
                )
            except asyncio.CancelledError:
                raise
            except StageCommitTimeout:
                return self._unknown_result()
            except StageCommitConnectorError:
                return EffectExecutionResult(
                    outcome=(
                        EffectOutcome.PARTIAL if dispatched else EffectOutcome.FAILED
                    ),
                    retryable=False,
                    safe_message=_PARTIAL if dispatched else _CONNECTOR_FAILED,
                )
            except Exception:
                return self._unknown_result()
            dispatched += 1

        if dispatched != len(material.rows):
            return EffectExecutionResult(
                outcome=EffectOutcome.PARTIAL,
                retryable=False,
                safe_message=_PARTIAL,
            )
        return EffectExecutionResult(
            outcome=EffectOutcome.APPLIED,
            retryable=False,
            safe_message="The approved row-set was applied.",
        )

    async def authorize(self, prepared: PreparedEffect) -> EffectExecutionAuthorization:
        """Observe current connector authority before any approved row sends."""

        try:
            material = await self._resolve_exact_material(prepared.request)
            first_sendable = next(
                (
                    row
                    for row in material.rows
                    if row.row_key not in material.held_row_keys
                ),
                None,
            )
            if first_sendable is not None:
                await self._connector.authorize(
                    self._request_for_row(
                        request=prepared.request,
                        material=material,
                        row=first_sendable,
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return EffectExecutionAuthorization(
                allowed=False,
                safe_code="builtin_authorization_revoked",
            )
        return EffectExecutionAuthorization(
            allowed=True,
            safe_code="builtin_authorized",
        )

    async def reconcile(self, claim: EffectClaim) -> EffectExecutionResult:
        del claim
        return self._unknown_result()

    async def abort(self, prepared: PreparedEffect) -> None:
        del prepared

    async def _resolve_exact_material(
        self, request: EffectDispatchRequest
    ) -> BuiltinRowSetEffectMaterial:
        try:
            material = await self._material_resolver.resolve(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BuiltinRowSetMaterialError() from exc
        if material is None or not self._material_matches(request, material):
            raise BuiltinRowSetMaterialError()
        return material

    @staticmethod
    def _material_matches(
        request: EffectDispatchRequest, material: BuiltinRowSetEffectMaterial
    ) -> bool:
        return (
            reviewed_rowset_target(
                target_connector=material.target_connector,
                target_op=material.target_op,
            )
            == (material.target_connector, material.target_op)
            and material.target_ref == request.target_ref
            and material.target_digest == request.target_digest
            and material.proposal_ref == request.proposal_ref
            and material.proposal_content_ref == request.proposal_content_ref
            and material.proposal_digest == request.proposal_digest
        )

    def _request_for_row(
        self,
        *,
        request: EffectDispatchRequest,
        material: BuiltinRowSetEffectMaterial,
        row: StagedRow,
    ) -> StageCommitRequest:
        try:
            args = json.loads(canonical_json(row.target_args))
        except (CanonicalJsonError, json.JSONDecodeError) as exc:
            raise BuiltinRowSetMaterialError() from exc
        if not isinstance(args, dict):
            raise BuiltinRowSetMaterialError()
        return StageCommitRequest(
            org_id=self._scope.org_id,
            user_id=self._scope.user_id,
            run_id=self._scope.run_id,
            conversation_id=self._scope.conversation_id or "",
            stage_id=request.stage_id,
            rev=request.revision,
            decision_seq=0,
            target_connector=material.target_connector,
            target_op=material.target_op,
            body="",
            row_key=row.row_key,
            row_args=args,  # type: ignore[arg-type] -- canonical JSON object.
        )

    @staticmethod
    def _unknown_result() -> EffectExecutionResult:
        return EffectExecutionResult(
            outcome=EffectOutcome.INDETERMINATE,
            retryable=False,
            safe_message=_UNKNOWN,
        )


__all__ = (
    "BuiltinRowSetEffectExecutor",
    "BuiltinRowSetEffectMaterial",
    "BuiltinRowSetMaterialError",
    "BuiltinRowSetMaterialResolver",
    "RuntimeBuiltinRowSetMaterialResolver",
)
