"""Durable, tenant-scoped claims for the effect coordinator.

An :class:`EffectClaim` is the persistent boundary between validation/prepare and
an external mutation.  Stores must atomically create a claim before an executor
can call ``apply``.  A redelivery with the same semantic request returns the
existing claim; a changed request is a hard conflict rather than an opportunity
to reuse an idempotency key for different bytes.

This module deliberately contains contracts and store protocols only.  The
in-memory, file, and Postgres implementations live in ``runtime_adapters`` so
the domain never learns about a storage backend.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agent_runtime.effects.contracts import validate_idempotency_key
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    EffectReceiptRefCodec,
    EffectStageIdCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ClaimIdText,
    EffectActor,
    EffectExecutorKind,
    EffectOutcome,
    Sha256Hex,
)

_IDENTIFIER_MAX_LENGTH = 255
_REF_MAX_LENGTH = 2048
_SAFE_MESSAGE_MAX_LENGTH = 512


class EffectClaimState(StrEnum):
    """Durable state of an exactly-once effect attempt."""

    CLAIMED = "claimed"
    COMPLETED = "completed"
    INDETERMINATE = "indeterminate"
    CANCELLED = "cancelled"


class EffectClaim(RuntimeContract):
    """One durable claim, containing references and safe result facts only.

    The unique identity is ``(org_id, executor, idempotency_key)``.  ``claim_id``
    identifies the attempt for ledger and reconciliation work, while the
    idempotency key identifies the mutation a caller is asking to perform.
    Proposal and target bytes, provider response bodies, credentials, and paths
    never occur in this record.
    """

    org_id: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH)
    run_id: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH)
    stage_id: str
    revision: int = Field(ge=1)
    claim_id: ClaimIdText = Field(default_factory=lambda: f"clm_{uuid4().hex}")
    idempotency_key: str
    executor: EffectExecutorKind
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    state: EffectClaimState = EffectClaimState.CLAIMED
    attempt: int = Field(default=1, ge=1)
    prepared_ref: str | None = Field(default=None, max_length=_REF_MAX_LENGTH)
    receipt_ref: str | None = Field(default=None, max_length=_REF_MAX_LENGTH)
    outcome: EffectOutcome | None = None
    result_digest: Sha256Hex | None = None
    safe_message: str | None = Field(default=None, max_length=_SAFE_MESSAGE_MAX_LENGTH)
    # These server-held references let reconciliation reconstruct the exact
    # request without re-folding mutable state.  They are opaque, bounded refs.
    target_ref: str
    proposal_ref: str
    actor: EffectActor
    decision_ledger_id: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("stage_id")
    @classmethod
    def _stage_id_is_valid(cls, value: str) -> str:
        EffectStageIdCodec.parse(value)
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency_key_is_valid(cls, value: str) -> str:
        return validate_idempotency_key(value)

    @field_validator("target_ref", "proposal_ref", "prepared_ref")
    @classmethod
    def _opaque_refs_are_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or len(value) > _REF_MAX_LENGTH
            or value != value.strip()
            or "\n" in value
            or "\r" in value
            or "://" not in value
            or value.startswith(("/", "~", "\\"))
            or value.lower().startswith(("file://", "filesystem://", "data:"))
            or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
            or any(part in {".", ".."} for part in value.split("/"))
        ):
            raise ValueError("effect claim references must be opaque safe URIs")
        return value

    @field_validator("receipt_ref")
    @classmethod
    def _receipt_ref_is_valid(cls, value: str | None) -> str | None:
        if value is not None:
            EffectReceiptRefCodec.parse(value)
        return value

    @field_validator("safe_message")
    @classmethod
    def _safe_message_is_bounded(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("safe_message must be a short single-line message")
        return value

    @model_validator(mode="after")
    def _state_is_consistent(self) -> "EffectClaim":
        if self.state is EffectClaimState.CLAIMED:
            if self.outcome is not None:
                raise ValueError("a claimed effect cannot have a terminal outcome")
        elif self.state is EffectClaimState.COMPLETED:
            if self.outcome is None or self.outcome is EffectOutcome.INDETERMINATE:
                raise ValueError("a completed effect requires a certain outcome")
        elif self.state is EffectClaimState.INDETERMINATE:
            if self.outcome is not EffectOutcome.INDETERMINATE:
                raise ValueError("an indeterminate claim must record indeterminate")
        elif self.state is EffectClaimState.CANCELLED:
            if self.outcome is not EffectOutcome.CANCELLED:
                raise ValueError("a cancelled claim must record cancelled")
        if self.receipt_ref is not None:
            parsed = EffectReceiptRefCodec.parse(self.receipt_ref)
            if parsed.stage_id != self.stage_id or parsed.claim_id != self.claim_id:
                raise ValueError("receipt_ref must reference this stage and claim")
        return self

    def same_request_as(self, other: "EffectClaim") -> bool:
        """Whether two claims represent the same approved external mutation.

        Mutable completion fields and generated identifiers intentionally do not
        participate.  A retry is allowed to observe the prior claim, whereas any
        changed approved bytes/target/revision is a hard security conflict.
        """

        return (
            self.org_id == other.org_id
            and self.run_id == other.run_id
            and self.stage_id == other.stage_id
            and self.revision == other.revision
            and self.idempotency_key == other.idempotency_key
            and self.executor is other.executor
            and self.proposal_digest == other.proposal_digest
            and self.target_digest == other.target_digest
            and self.target_ref == other.target_ref
            and self.proposal_ref == other.proposal_ref
            and self.actor is other.actor
            and self.decision_ledger_id == other.decision_ledger_id
        )


class EffectClaimAcquisition(RuntimeContract):
    """Result of atomically creating or reading an idempotency claim."""

    created: bool
    claim: EffectClaim


@runtime_checkable
class EffectClaimStore(Protocol):
    """Durable claim persistence shared by all effect executor families."""

    async def claim(self, *, claim: EffectClaim) -> EffectClaimAcquisition:
        """Atomically create ``claim`` or return its exact pre-existing claim.

        Implementations must raise :class:`EffectClaimConflict` if the unique
        idempotency key exists for a different semantic request.
        """

    async def get(
        self,
        *,
        org_id: str,
        executor: EffectExecutorKind,
        idempotency_key: str,
    ) -> EffectClaim | None:
        """Return one claim by its tenant/executor idempotency identity."""

    async def get_by_claim_id(
        self, *, org_id: str, claim_id: str
    ) -> EffectClaim | None:
        """Return one tenant-scoped claim for reconciliation."""

    async def update(self, *, claim: EffectClaim) -> EffectClaim:
        """Persist a monotonic state transition and return the stored record."""

    async def list_incomplete(
        self, *, org_id: str | None = None, limit: int = 100
    ) -> Sequence[EffectClaim]:
        """List unresolved claimed/indeterminate attempts for recovery sweeps."""


class EffectClaimError(Exception):
    """Base typed error with a safe public code."""

    code = "effect_claim_error"
    safe_message = "The effect attempt could not be recorded safely."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class EffectClaimConflict(EffectClaimError):
    """Raised when an idempotency key is reused for different approved data."""

    code = "effect_claim_conflict"
    safe_message = (
        "This effect idempotency key belongs to a different approved request."
    )


class EffectClaimNotFound(EffectClaimError):
    """Raised when a state transition references no durable claim."""

    code = "effect_claim_not_found"
    safe_message = "The effect attempt is no longer available."


class EffectClaimInvalidTransition(EffectClaimError):
    """Raised when a durable claim would move backwards or change its facts."""

    code = "effect_claim_invalid_transition"
    safe_message = "The effect attempt cannot move to that state."


class EffectClaimStorageError(EffectClaimError):
    """A fail-closed persistence error; callers must not apply an effect."""

    code = "effect_claim_storage_error"
    safe_message = "The effect attempt could not be persisted safely."


def validate_claim_transition(
    *, previous: EffectClaim, replacement: EffectClaim
) -> None:
    """Validate the monotonic transition every store applies before writing.

    A completed outcome is immutable.  Reconciliation may turn an uncertain
    claim into a certain completion, but no code may move a claim back to a
    state that would permit a blind resend.
    """

    if (
        previous.org_id != replacement.org_id
        or previous.executor is not replacement.executor
        or previous.idempotency_key != replacement.idempotency_key
        or previous.claim_id != replacement.claim_id
        or not previous.same_request_as(replacement)
        or previous.created_at != replacement.created_at
    ):
        raise EffectClaimInvalidTransition()
    allowed: dict[EffectClaimState, frozenset[EffectClaimState]] = {
        EffectClaimState.CLAIMED: frozenset(
            {
                EffectClaimState.CLAIMED,
                EffectClaimState.COMPLETED,
                EffectClaimState.INDETERMINATE,
                EffectClaimState.CANCELLED,
            }
        ),
        EffectClaimState.INDETERMINATE: frozenset(
            {EffectClaimState.INDETERMINATE, EffectClaimState.COMPLETED}
        ),
        EffectClaimState.CANCELLED: frozenset({EffectClaimState.CANCELLED}),
        EffectClaimState.COMPLETED: frozenset({EffectClaimState.COMPLETED}),
    }
    if replacement.state not in allowed[previous.state]:
        raise EffectClaimInvalidTransition()
    if previous.state in {EffectClaimState.COMPLETED, EffectClaimState.CANCELLED}:
        if replacement != previous:
            raise EffectClaimInvalidTransition()
    if previous.state is EffectClaimState.INDETERMINATE and (
        replacement.state is EffectClaimState.INDETERMINATE and replacement != previous
    ):
        raise EffectClaimInvalidTransition()


__all__ = [
    "EffectClaim",
    "EffectClaimAcquisition",
    "EffectClaimConflict",
    "EffectClaimError",
    "EffectClaimInvalidTransition",
    "EffectClaimNotFound",
    "EffectClaimState",
    "EffectClaimStorageError",
    "EffectClaimStore",
    "validate_claim_transition",
]
