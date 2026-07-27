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

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agent_runtime.effects.contracts import validate_idempotency_key
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    EffectReceiptRefCodec,
    ProposalUriCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ClaimIdText,
    EffectActor,
    EffectExecutorKind,
    EffectOutcome,
    Sha256Hex,
    WriteAppliedRowResult,
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
    never occur in this record. ``proposal_ref`` is the canonical stage/revision
    identity, while ``proposal_content_ref`` identifies the server-held immutable
    bytes whose digest was approved.
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
    # ``None`` is accepted only when loading an old canonical-only file record.
    # Claim stores reject it for every new acquisition, so no new effect can
    # execute without an immutable content reference.
    proposal_content_ref: str | None = Field(default=None, max_length=_REF_MAX_LENGTH)
    actor: EffectActor
    decision_ledger_id: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH)
    row_keys: tuple[str, ...] | None = None
    row_results: tuple[WriteAppliedRowResult, ...] | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_proposal_reference(cls, value: object) -> object:
        """Accept an old overloaded input only at this durable boundary.

        A4/A5 callers created before the split omit ``proposal_content_ref`` and
        put an artifact/operation URI in ``proposal_ref``. Normalize that old
        shape once, before field validation, so persisted claims always retain
        canonical identity plus the immutable content locator. Supplying the new
        field opts into strict canonical validation below.
        """

        if isinstance(value, Mapping):
            return normalize_persisted_effect_claim_payload(value)
        return value

    @field_validator("stage_id")
    @classmethod
    def _stage_id_is_valid(cls, value: str) -> str:
        # A4 uses ``stg_`` ids; the pre-existing staged-write ledger uses a
        # canonical UUID token. Both reach the SAME durable claim protocol only
        # after their own approval fold has validated the exact revision.
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _IDENTIFIER_MAX_LENGTH
            or value != value.strip()
            or "/" in value
            or "\\" in value
        ):
            raise ValueError("stage_id must be a stable opaque identifier")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency_key_is_valid(cls, value: str) -> str:
        return validate_idempotency_key(value)

    @field_validator("proposal_ref")
    @classmethod
    def _proposal_ref_is_canonical(cls, value: str) -> str:
        # ``proposal://`` remains mandatory for A4.  Legacy write stages use
        # their immutable ``draft://`` / ``stage://`` revision references, so
        # the shared dispatch store validates a safe URI here and the caller's
        # approval fold supplies the revision proof.
        return _validate_safe_opaque_uri(
            value,
            field_name="effect claim proposal reference",
        )

    @field_validator("target_ref", "prepared_ref")
    @classmethod
    def _opaque_refs_are_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_safe_opaque_uri(value, field_name="effect claim reference")

    @field_validator("proposal_content_ref")
    @classmethod
    def _proposal_content_ref_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_safe_opaque_uri(
            value,
            field_name="proposal_content_ref",
            forbid_proposal_scheme=True,
        )

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

    @field_validator("row_keys")
    @classmethod
    def _row_keys_are_unique(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is not None and (
            not value
            or len(value) != len(set(value))
            or any(not key or len(key) > 256 for key in value)
        ):
            raise ValueError("row_keys must contain unique row keys")
        return value

    @model_validator(mode="after")
    def _state_is_consistent(self) -> "EffectClaim":
        # A4 stage ids are canonical ``stg_`` identifiers and must preserve
        # their proposal identity. Legacy staged writes reach this shared
        # claim protocol only with their established UUID-like stage token and
        # their exact revision ref (``draft://`` / ``stage://``).
        if self.stage_id.startswith("stg_") or self.proposal_ref.startswith(
            "proposal://"
        ):
            parsed_proposal = ProposalUriCodec.parse(self.proposal_ref)
            if (
                parsed_proposal.stage_id != self.stage_id
                or parsed_proposal.revision != self.revision
            ):
                raise ValueError("proposal_ref must reference this stage and revision")
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
        if self.row_results is not None:
            result_keys = tuple(item.row_key for item in self.row_results)
            if (
                self.row_keys is None
                or not result_keys
                or len(result_keys) != len(set(result_keys))
                or set(result_keys) != set(self.row_keys)
                or self.state is not EffectClaimState.COMPLETED
            ):
                raise ValueError(
                    "row_results must exactly cover one completed row-key scope"
                )
        if (
            self.state is EffectClaimState.COMPLETED
            and self.row_keys is not None
            and self.row_results is None
        ):
            raise ValueError("a completed row-set claim requires exact row outcomes")
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
            and self.proposal_content_ref == other.proposal_content_ref
            and self.actor is other.actor
            and self.decision_ledger_id == other.decision_ledger_id
            and self.row_keys == other.row_keys
        )


class EffectClaimScanCursor(RuntimeContract):
    """Opaque keyset position for a bounded unresolved-claim scan.

    This cursor carries no effect target, proposal body, URI, or receipt. It
    lets a planning worker traverse a large durable claim set without
    repeatedly treating its oldest bounded page as the whole enumeration.
    """

    after_created_at: datetime
    after_org_id: str = Field(
        min_length=1,
        max_length=_IDENTIFIER_MAX_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    after_claim_id: ClaimIdText

    @field_validator("after_created_at")
    @classmethod
    def _cursor_timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "effect claim scan cursor timestamp must be timezone-aware"
            )
        return value.astimezone(UTC)


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

    async def list_incomplete_after(
        self,
        *,
        cursor: EffectClaimScanCursor | None,
        limit: int = 100,
    ) -> Sequence[EffectClaim]:
        """Return one global page after an opaque durable keyset cursor.

        Rows are ordered by ``(created_at, org_id, claim_id)`` ascending. This
        read-only method never updates, reconciles, or executes an effect.
        """


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


def require_persistable_effect_claim(claim: EffectClaim) -> None:
    """Fail closed before a store creates a claim without content provenance.

    ``EffectClaim`` deliberately keeps ``proposal_content_ref`` nullable so a
    file adapter can read historical canonical-only records.  That leniency is
    read compatibility only: a new claim must always carry both the canonical
    proposal identity and the immutable content locator.
    """

    if claim.proposal_content_ref is None:
        raise EffectClaimStorageError(
            "The effect attempt lacks an immutable proposal content reference."
        )


def normalize_persisted_effect_claim_payload(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Normalize a pre-``proposal_content_ref`` file/database record safely.

    The old A4/A5 shape overloaded ``proposal_ref`` with an artifact or
    operation content URI.  Preserve that immutable locator as
    ``proposal_content_ref`` and derive the canonical identity from the stored
    stage id and revision.  Canonical-only historical records remain readable
    with a ``None`` content ref, which keeps them incapable of new execution.

    This function is intentionally for persisted input only. New claim writers
    must supply both fields and are checked by
    :func:`require_persistable_effect_claim`.
    """

    normalized = dict(payload)
    if "proposal_content_ref" in normalized:
        return normalized

    old_ref = normalized.get("proposal_ref")
    if not isinstance(old_ref, str):
        raise ValueError("stored effect claim has no proposal reference")

    stage_id = normalized.get("stage_id")
    revision = normalized.get("revision")
    if (
        not isinstance(stage_id, str)
        or not isinstance(revision, int)
        or isinstance(revision, bool)
    ):
        raise ValueError("stored effect claim has an invalid stage identity")

    try:
        parsed = ProposalUriCodec.parse(old_ref)
    except ValueError:
        _validate_safe_opaque_uri(
            old_ref,
            field_name="proposal_content_ref",
            forbid_proposal_scheme=True,
        )
        normalized["proposal_ref"] = ProposalUriCodec.format(stage_id, revision)
        normalized["proposal_content_ref"] = old_ref
        return normalized

    if parsed.stage_id != stage_id or parsed.revision != revision:
        raise ValueError("stored canonical proposal_ref does not match its stage")
    normalized["proposal_content_ref"] = None
    return normalized


def _validate_safe_opaque_uri(
    value: str,
    *,
    field_name: str,
    forbid_proposal_scheme: bool = False,
) -> str:
    """Validate a server-held logical URI without accepting a host path.

    Content and target locators are opaque references resolved through trusted
    adapters.  They must never be a filesystem path, a data URL, or traversal
    disguised through percent encoding.  We deliberately do not maintain a
    closed scheme allow-list here: new server-owned locator families can be
    introduced without weakening the physical-path boundary. Direct web URLs
    are not server-held content references and remain forbidden.
    """

    if (
        not value
        or len(value) > _REF_MAX_LENGTH
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or "\x00" in value
        or value.startswith(("/", "~", "\\"))
        or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
    ):
        raise ValueError(f"{field_name} must be an opaque safe URI")

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an opaque safe URI") from exc
    scheme = parsed.scheme.lower()
    forbidden_schemes = {"file", "filesystem", "data", "http", "https"}
    if forbid_proposal_scheme:
        forbidden_schemes.add("proposal")
    if (
        not scheme
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or scheme in forbidden_schemes
    ):
        raise ValueError(f"{field_name} must be an opaque safe URI")

    decoded = value
    # Each successful unquote consumes at least one percent escape, and input
    # length is bounded above, so this terminates while catching arbitrarily
    # nested encoded traversal rather than only a fixed number of layers.
    while True:
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    try:
        decoded_parts = urlsplit(decoded)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an opaque safe URI") from exc
    if (
        "\\" in decoded
        or "\x00" in decoded
        or decoded_parts.query
        or decoded_parts.fragment
        or decoded_parts.path.startswith("//")
    ):
        raise ValueError(f"{field_name} must be an opaque safe URI")
    for component in (
        decoded_parts.netloc,
        decoded_parts.path,
        decoded_parts.query,
        decoded_parts.fragment,
    ):
        if any(part in {".", ".."} for part in component.split("/")):
            raise ValueError(f"{field_name} must be an opaque safe URI")
    return value


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
    "EffectClaimScanCursor",
    "EffectClaimConflict",
    "EffectClaimError",
    "EffectClaimInvalidTransition",
    "EffectClaimNotFound",
    "EffectClaimState",
    "EffectClaimStorageError",
    "EffectClaimStore",
    "normalize_persisted_effect_claim_payload",
    "require_persistable_effect_claim",
    "validate_claim_transition",
]
