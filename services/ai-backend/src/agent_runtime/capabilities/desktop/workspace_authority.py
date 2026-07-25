"""C2 typed adapter from A5 effects to Electron's local workspace authority.

This module intentionally contains no host filesystem API and cannot mint a
commit permit.  It serialises only an already-verified effect request plus
immutable, server-held workspace material to the private Electron broker.
Electron main/native remains the sole process capable of touching a host path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.desktop.broker_client import (
    DesktopBrokerClient,
    WorkspaceCommitResult,
    WorkspacePreparedEffect,
)
from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.executor import (
    EffectExecutionScope,
    EffectExecutorCapabilities,
    PreparedEffect,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.entities import (
    EffectExecutionRequest,
    EffectExecutionResult,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectExecutorKind,
    EffectOutcome,
    Sha256Hex,
)

_OPAQUE_SLOT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
_SAFE_MESSAGE = "The workspace change could not be completed safely."


class WorkspacePrecondition(RuntimeContract):
    """One CAS baseline supplied by immutable proposal material."""

    exists: bool
    kind: Literal["file", "directory"] | None = None
    stable_id: str | None = Field(default=None, max_length=512)
    sha256: Sha256Hex | None = None


class WorkspaceChangeEntry(RuntimeContract):
    """A relative workspace mutation; never accepts a physical host path."""

    operation: Literal["create", "replace", "delete", "move", "mkdir"]
    relative_path: str = Field(min_length=1, max_length=4096)
    destination_relative_path: str | None = Field(default=None, max_length=4096)
    content_slot: str | None = Field(default=None, max_length=120)
    content_ref: str | None = Field(default=None, max_length=2048)
    content_digest: Sha256Hex | None = None
    content_size: int | None = Field(default=None, ge=0)
    precondition: WorkspacePrecondition

    @field_validator("relative_path", "destination_relative_path")
    @classmethod
    def _relative_path_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or "\x00" in normalized
            or any(segment in {"", ".", ".."} for segment in normalized.split("/"))
        ):
            raise ValueError("workspace paths must be safe relative paths")
        return normalized

    @field_validator("content_slot")
    @classmethod
    def _slot_is_opaque(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(character not in _OPAQUE_SLOT for character in value):
            raise ValueError("content_slot must be an opaque identifier")
        return value

    @model_validator(mode="after")
    def _operation_content_is_consistent(self) -> WorkspaceChangeEntry:
        has_content = (
            self.content_slot is not None
            or self.content_ref is not None
            or self.content_digest is not None
            or self.content_size is not None
        )
        needs_content = self.operation in {"create", "replace"}
        if needs_content != has_content:
            raise ValueError("workspace content declaration does not match operation")
        if needs_content and (
            self.content_slot is None
            or self.content_ref is None
            or self.content_digest is None
            or self.content_size is None
        ):
            raise ValueError("workspace content must be immutable and complete")
        if (self.operation == "move") != (self.destination_relative_path is not None):
            raise ValueError("workspace move destination does not match operation")
        return self


class WorkspaceProposalMaterial(RuntimeContract):
    """C1 material resolved server-side from the immutable approved proposal."""

    grant_id: str = Field(min_length=1, max_length=255)
    mount: str = Field(min_length=1, max_length=255)
    change_set_digest: Sha256Hex
    target_digest: Sha256Hex
    proposal_digest: Sha256Hex
    entries: tuple[WorkspaceChangeEntry, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _content_slots_are_unique(self) -> WorkspaceProposalMaterial:
        slots = [entry.content_slot for entry in self.entries if entry.content_slot]
        refs = [entry.content_ref for entry in self.entries if entry.content_ref]
        if len(slots) != len(set(slots)) or len(refs) != len(set(refs)):
            raise ValueError("workspace upload slots and content refs must be unique")
        return self

    def broker_wire(self, request: EffectExecutionRequest) -> dict[str, object]:
        """Render the exact broker schema, excluding server-only content refs."""

        if (
            self.target_digest != request.target_digest
            or self.proposal_digest != request.proposal_digest
        ):
            raise WorkspaceAuthorityContractError("approved workspace digests changed")
        return {
            "stage_id": request.stage_id,
            "revision": request.revision,
            "decision_ledger_id": request.decision_ledger_id,
            "grant_id": self.grant_id,
            "mount": self.mount,
            "change_set_digest": self.change_set_digest,
            "target_digest": self.target_digest,
            "proposal_digest": self.proposal_digest,
            "entries": [
                {
                    "operation": entry.operation,
                    "relative_path": entry.relative_path,
                    "destination_relative_path": entry.destination_relative_path,
                    "content_slot": entry.content_slot,
                    "content_digest": entry.content_digest,
                    "content_size": entry.content_size,
                    "precondition": entry.precondition.model_dump(exclude_none=True),
                }
                for entry in self.entries
            ],
        }


@dataclass(frozen=True)
class WorkspacePrepareCommand:
    """Opaque private host session plus server-resolved immutable material."""

    scope: EffectExecutionScope
    request: EffectExecutionRequest
    host_session_ref: str
    material: WorkspaceProposalMaterial


@runtime_checkable
class WorkspaceProposalResolver(Protocol):
    """Resolve exact C1 workspace material without exposing it to the model."""

    async def resolve(
        self, *, scope: EffectExecutionScope, request: EffectExecutionRequest
    ) -> WorkspacePrepareCommand:
        """Return server-held material and a main-issued read capability."""


@runtime_checkable
class ImmutableWorkspaceContentResolver(Protocol):
    """Stream an immutable server-held content reference without loading it all."""

    def open(
        self, *, scope: EffectExecutionScope, reference: str
    ) -> AsyncIterator[bytes]:
        """Yield immutable proposal bytes named by one opaque content ref."""


@runtime_checkable
class WorkspaceAuthorityPort(Protocol):
    """The C2 authority contract consumed by A5's workspace executor."""

    async def prepare(
        self, command: WorkspacePrepareCommand
    ) -> WorkspacePreparedEffect:
        """Prepare exactly one checked change set with no visible mutation."""

    async def upload(self, prepared_ref: str, content_ref: str) -> None:
        """Upload one immutable content reference into its prepared slot."""

    async def commit(self, prepared_ref: str) -> WorkspaceCommitResult:
        """Commit through the main-only approval/permit handoff exactly once."""

    async def reconcile(self, claim_id: str) -> WorkspaceCommitResult:
        """Determine a previous commit's result without blind replay."""

    async def abort(self, prepared_ref: str) -> None:
        """Release private staged state before commit."""


class WorkspaceAuthorityContractError(RuntimeError):
    """A local invariant failed before any broker operation was attempted."""


class BrokerWorkspaceAuthority(WorkspaceAuthorityPort):
    """Private broker implementation of :class:`WorkspaceAuthorityPort`."""

    def __init__(
        self,
        *,
        client: DesktopBrokerClient,
        content: ImmutableWorkspaceContentResolver,
    ) -> None:
        self._client = client
        self._content = content
        self._prepared: dict[
            str,
            tuple[EffectExecutionScope, dict[str, str], str],
        ] = {}

    async def prepare(
        self, command: WorkspacePrepareCommand
    ) -> WorkspacePreparedEffect:
        prepared = await self._client.workspace_prepare(
            host_session_ref=command.host_session_ref,
            change_set=command.material.broker_wire(command.request),
        )
        expected = {
            entry.content_slot: entry.content_ref
            for entry in command.material.entries
            if entry.content_slot is not None and entry.content_ref is not None
        }
        returned = {slot.slot: slot for slot in prepared.upload_slots}
        if set(returned) != set(expected):
            await self._client.workspace_abort(prepared_ref=prepared.prepared_ref)
            raise WorkspaceAuthorityContractError(
                "workspace upload slots do not match proposal"
            )
        for slot, content_ref in expected.items():
            item = returned[slot]
            entry = next(
                item for item in command.material.entries if item.content_slot == slot
            )
            if item.digest != entry.content_digest or item.size != entry.content_size:
                await self._client.workspace_abort(prepared_ref=prepared.prepared_ref)
                raise WorkspaceAuthorityContractError(
                    "workspace upload slot digest changed"
                )
            if content_ref is None:  # defensive narrowing for type-checkers.
                raise WorkspaceAuthorityContractError(
                    "workspace content reference missing"
                )
        self._prepared[prepared.prepared_ref] = (
            command.scope,
            expected,
            command.host_session_ref,
        )
        return prepared

    async def upload(self, prepared_ref: str, content_ref: str) -> None:
        state = self._prepared.get(prepared_ref)
        if state is None:
            raise WorkspaceAuthorityContractError(
                "workspace prepared state is unavailable"
            )
        scope, slots, _host_session_ref = state
        matches = [
            slot for slot, reference in slots.items() if reference == content_ref
        ]
        if len(matches) != 1:
            raise WorkspaceAuthorityContractError(
                "workspace content reference is not prepared"
            )
        await self._client.workspace_upload(
            prepared_ref=prepared_ref,
            slot=matches[0],
            content=self._content.open(scope=scope, reference=content_ref),
            final=True,
        )

    async def commit(self, prepared_ref: str) -> WorkspaceCommitResult:
        state = self._prepared.get(prepared_ref)
        if state is None:
            raise WorkspaceAuthorityContractError(
                "workspace prepared state is unavailable"
            )
        _scope, _slots, host_session_ref = state
        return await self._client.workspace_commit(
            prepared_ref=prepared_ref, host_session_ref=host_session_ref
        )

    async def reconcile(self, claim_id: str) -> WorkspaceCommitResult:
        return await self._client.workspace_reconcile(claim_id=claim_id)

    async def abort(self, prepared_ref: str) -> None:
        self._prepared.pop(prepared_ref, None)
        await self._client.workspace_abort(prepared_ref=prepared_ref)


class WorkspaceEffectExecutor:
    """A5 executor whose commit uses only the private opaque host session."""

    kind = EffectExecutorKind.WORKSPACE
    capabilities = EffectExecutorCapabilities(
        supports_prepare=True,
        supports_reconcile=True,
        native_idempotency=True,
        prepare_performs_mutation=False,
    )

    def __init__(
        self,
        *,
        scope: EffectExecutionScope,
        authority: WorkspaceAuthorityPort,
        proposal_resolver: WorkspaceProposalResolver,
    ) -> None:
        self._scope = scope
        self._authority = authority
        self._proposal_resolver = proposal_resolver
        self._prepared_requests: dict[str, EffectExecutionRequest] = {}

    async def prepare(self, request: EffectExecutionRequest) -> PreparedEffect:
        command = await self._proposal_resolver.resolve(
            scope=self._scope, request=request
        )
        if command.scope != self._scope or command.request != request:
            raise WorkspaceAuthorityContractError(
                "workspace preparation scope mismatch"
            )
        prepared = await self._authority.prepare(command)
        try:
            for entry in command.material.entries:
                if entry.content_ref is not None:
                    await self._authority.upload(
                        prepared.prepared_ref, entry.content_ref
                    )
        except Exception:
            await self._abort_quietly(prepared.prepared_ref)
            raise
        self._prepared_requests[prepared.prepared_ref] = request
        return PreparedEffect(
            request=request,
            prepared_ref=prepared.prepared_ref,
            observed_precondition_digest=prepared.observed_target_digest,
            expires_at=datetime.fromtimestamp(
                prepared.expires_at / 1000, UTC
            ).isoformat(),
        )

    async def apply(self, prepared: PreparedEffect) -> EffectExecutionResult:
        if prepared.prepared_ref is None:
            return _failed(_SAFE_MESSAGE)
        request = self._prepared_requests.get(prepared.prepared_ref)
        if request is None or request != prepared.request:
            return _failed(_SAFE_MESSAGE)
        result = await self._authority.commit(prepared.prepared_ref)
        return _to_effect_result(result)

    async def reconcile(self, claim: EffectClaim) -> EffectExecutionResult:
        if claim.executor is not EffectExecutorKind.WORKSPACE:
            return _failed(_SAFE_MESSAGE)
        return _to_effect_result(await self._authority.reconcile(claim.claim_id))

    async def abort(self, prepared: PreparedEffect) -> None:
        if prepared.prepared_ref is not None:
            self._prepared_requests.pop(prepared.prepared_ref, None)
            await self._authority.abort(prepared.prepared_ref)

    async def _abort_quietly(self, prepared_ref: str) -> None:
        try:
            await self._authority.abort(prepared_ref)
        except Exception:  # noqa: BLE001 - reservation cleanup is best-effort.
            return


def _to_effect_result(result: WorkspaceCommitResult) -> EffectExecutionResult:
    outcome = EffectOutcome(result.outcome)
    # The native receipt is local/private. A5's durable claim id is the
    # exportable receipt identity, so never copy a local workspace URI into the
    # generic effect result or ledger.
    return EffectExecutionResult(
        outcome=outcome,
        receipt_ref=None,
        result_digest=result.result_digest,
        retryable=outcome is EffectOutcome.INDETERMINATE,
        safe_message=result.safe_message,
    )


def _failed(message: str) -> EffectExecutionResult:
    return EffectExecutionResult(
        outcome=EffectOutcome.FAILED,
        retryable=False,
        safe_message=message,
    )


__all__ = [
    "BrokerWorkspaceAuthority",
    "ImmutableWorkspaceContentResolver",
    "WorkspaceAuthorityContractError",
    "WorkspaceAuthorityPort",
    "WorkspaceChangeEntry",
    "WorkspaceEffectExecutor",
    "WorkspacePrecondition",
    "WorkspacePrepareCommand",
    "WorkspaceProposalMaterial",
    "WorkspaceProposalResolver",
]
