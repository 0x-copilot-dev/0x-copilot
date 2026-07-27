"""Portable, body-free contracts for universal effect staging.

These are deliberately domain contracts rather than ledger payload mirrors.  A later
integration PR maps their values into the A1 Work Ledger contract and supplies durable
adapters.  Proposal bytes, raw arguments, credentials, and physical paths are never
accepted here; every consequential value is a validated reference plus a SHA-256
digest.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal
from urllib.parse import unquote

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.rollout import RolloutCapability
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_ids import EffectStageIdCodec, ProposalUriCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
    OperationIdText,
    Sha256Hex,
    validate_immutable_content_ref,
)

_REF_MAX_LENGTH = 2048
_TEXT_MAX_LENGTH = 512
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class EffectStageStatus(StrEnum):
    """Canonical A4 read-state vocabulary, independent of legacy write stages."""

    PROPOSED = "proposed"
    HELD = "held"
    REVISED = "revised"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class EffectActorIdentity(RuntimeContract):
    """A safe actor kind plus opaque principal reference.

    ``EffectActor`` is the A1 wire vocabulary.  ``principal_ref`` remains opaque so
    the staging core can reject a foreign user without depending on auth models.
    """

    actor: EffectActor
    principal_ref: str

    @field_validator("principal_ref")
    @classmethod
    def _principal_ref_is_safe(cls, value: str) -> str:
        return _safe_reference(value, "principal_ref")


class EffectStageScope(RuntimeContract):
    """Trusted scope supplied by the caller, never model-authored proposal data."""

    run_id: str
    owner_ref: str

    @field_validator("run_id")
    @classmethod
    def _run_id_is_safe(cls, value: str) -> str:
        if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
            raise ValueError("run_id must be a stable opaque identifier")
        return value

    @field_validator("owner_ref")
    @classmethod
    def _owner_ref_is_safe(cls, value: str) -> str:
        return _safe_reference(value, "owner_ref")


class EffectPolicySnapshot(RuntimeContract):
    """Immutable policy facts evaluated once when a proposal becomes a stage."""

    snapshot_ref: str
    descriptor_known: bool
    deployment_policy: EffectPolicy | None = None
    organization_policy: EffectPolicy | None = None
    grant_policy: EffectPolicy | None = None
    capability_policy: EffectPolicy | None = None
    user_policy: EffectPolicy | None = None
    allow_always: bool = False
    sensitive_target: bool = False

    @field_validator("snapshot_ref")
    @classmethod
    def _snapshot_ref_is_safe(cls, value: str) -> str:
        return _safe_reference(value, "snapshot_ref")


class EffectPolicyResolution(RuntimeContract):
    """Pure policy result persisted in the stage event, never recomputed on replay."""

    policy: EffectPolicy
    auto_approval_allowed: bool
    reasons: tuple[str, ...]


class ProposedEffect(RuntimeContract):
    """One effect proposal without a stage id or trusted run identity.

    The caller creates the stage id later.  That prevents a model-visible proposal
    from selecting either its own run scope or an authoritative stage identity.
    """

    operation_id: OperationIdText
    executor: EffectExecutorKind
    target: EffectTarget
    target_digest: Sha256Hex
    display_target: str
    proposal_kind: EffectProposalKind
    proposal_content_ref: str
    proposal_digest: Sha256Hex
    proposal_media_type: str
    precondition_ref: str | None = None
    precondition_digest: Sha256Hex | None = None
    effect_class: EffectClass
    policy_snapshot_ref: str
    agent_hold: bool = False
    safe_summary_ref: str | None = None
    # A workspace overlay is a separate durable projection.  When this is set,
    # the stage cannot be approved until its exact current revision has a
    # matching ``effect.projection_bound`` ledger row.
    projection_required: bool = False

    @field_validator("display_target")
    @classmethod
    def _display_target_is_safe(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _TEXT_MAX_LENGTH
        ):
            raise ValueError("display_target must be a short non-empty safe label")
        if "\n" in value or "\r" in value:
            raise ValueError("display_target must not contain a body or path")
        return value

    @field_validator("policy_snapshot_ref", "safe_summary_ref")
    @classmethod
    def _refs_are_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_reference(value, "reference")

    @field_validator("proposal_content_ref")
    @classmethod
    def _proposal_content_ref_is_safe(cls, value: str) -> str:
        return validate_proposal_content_ref(value)

    @field_validator("proposal_media_type")
    @classmethod
    def _media_type_is_valid(cls, value: str) -> str:
        if not isinstance(value, str) or _MEDIA_TYPE.fullmatch(value) is None:
            raise ValueError("proposal_media_type must be a media type")
        return value.lower()

    @model_validator(mode="after")
    def _proposal_is_consistent(self) -> ProposedEffect:
        if self.target.executor is not self.executor:
            raise ValueError("target executor must match proposal executor")
        if self.target.display_label != self.display_target:
            raise ValueError("display_target must match target display_label")
        _require_ref_digest_pair(
            self.precondition_ref,
            self.precondition_digest,
            "precondition",
        )
        validate_proposal_executor_pair(self.proposal_kind, self.executor)
        return self


class EffectRevisionProposal(RuntimeContract):
    """A proposed replacement of the content for one immutable stage target."""

    proposal_kind: EffectProposalKind
    proposal_content_ref: str
    proposal_digest: Sha256Hex
    proposal_media_type: str
    target_ref: str
    target_digest: Sha256Hex
    display_target: str
    precondition_ref: str | None = None
    precondition_digest: Sha256Hex | None = None
    safe_diff_ref: str | None = None

    @field_validator(
        "target_ref",
        "precondition_ref",
        "safe_diff_ref",
    )
    @classmethod
    def _refs_are_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_reference(value, "reference")

    @field_validator("proposal_content_ref")
    @classmethod
    def _proposal_content_ref_is_safe(cls, value: str) -> str:
        return validate_proposal_content_ref(value)

    @field_validator("proposal_media_type")
    @classmethod
    def _media_type_is_valid(cls, value: str) -> str:
        if not isinstance(value, str) or _MEDIA_TYPE.fullmatch(value) is None:
            raise ValueError("proposal_media_type must be a media type")
        return value.lower()

    @field_validator("display_target")
    @classmethod
    def _display_target_is_safe(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _TEXT_MAX_LENGTH
        ):
            raise ValueError("display_target must be a short non-empty safe label")
        if "\n" in value or "\r" in value:
            raise ValueError("display_target must not contain a body or path")
        return value

    @model_validator(mode="after")
    def _precondition_is_pinned(self) -> EffectRevisionProposal:
        _require_ref_digest_pair(
            self.precondition_ref,
            self.precondition_digest,
            "precondition",
        )
        return self


class EffectStageRevision(RuntimeContract):
    """An immutable, exact content/target snapshot in a staged effect."""

    revision: int = Field(ge=1)
    proposal_kind: EffectProposalKind
    proposal_ref: str
    proposal_content_ref: str | None
    proposal_digest: Sha256Hex
    proposal_media_type: str
    target_ref: str
    target_digest: Sha256Hex
    display_target: str
    precondition_ref: str | None = None
    precondition_digest: Sha256Hex | None = None
    safe_diff_ref: str | None = None
    author: EffectActorIdentity
    created_at: str

    @field_validator(
        "target_ref",
        "precondition_ref",
        "safe_diff_ref",
    )
    @classmethod
    def _refs_are_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_reference(value, "reference")

    @field_validator("proposal_ref")
    @classmethod
    def _proposal_ref_is_canonical(cls, value: str) -> str:
        ProposalUriCodec.parse(value)
        return value

    @field_validator("proposal_content_ref")
    @classmethod
    def _proposal_content_ref_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_proposal_content_ref(value)

    @field_validator("proposal_media_type")
    @classmethod
    def _media_type_is_valid(cls, value: str) -> str:
        if not isinstance(value, str) or _MEDIA_TYPE.fullmatch(value) is None:
            raise ValueError("proposal_media_type must be a media type")
        return value.lower()

    @model_validator(mode="after")
    def _precondition_is_pinned(self) -> EffectStageRevision:
        _require_ref_digest_pair(
            self.precondition_ref,
            self.precondition_digest,
            "precondition",
        )
        return self

    @property
    def is_executable(self) -> bool:
        """Whether immutable proposal bytes can be resolved for execution."""

        return self.proposal_content_ref is not None


class EffectStageDecision(RuntimeContract):
    """A decision pinned to the exact current proposal and target digests."""

    revision: int = Field(ge=1)
    decision: EffectDecisionKind
    actor: EffectActorIdentity
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    decided_at: str
    ledger_id: str
    row_keys: tuple[str, ...] | None = None

    @field_validator("row_keys")
    @classmethod
    def _row_keys_are_unique(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is not None and (not value or len(value) != len(set(value))):
            raise ValueError("row_keys must contain unique row keys")
        return value


class EffectRowDecisionState(RuntimeContract):
    """Latest durable user posture for one row in a row-set revision."""

    row_key: str = Field(min_length=1, max_length=256)
    decision: Literal["approve", "hold"]
    actor: EffectActorIdentity
    decided_at: str
    ledger_id: str


class EffectProjectionBinding(RuntimeContract):
    """One immutable visible-projection binding for a stage revision.

    The reference names a retained projection version, never a mutable latest
    view or a physical path.  The fold compares both digests before treating
    this as approval readiness.
    """

    revision: int = Field(ge=1)
    projection_ref: str
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    bound_at: str
    ledger_id: str

    @field_validator("projection_ref")
    @classmethod
    def _projection_ref_is_safe(cls, value: str) -> str:
        return _safe_reference(value, "projection_ref")


class EffectStageState(RuntimeContract):
    """Pure fold result for one stage; it contains no ability to cause an effect."""

    stage_id: str
    scope: EffectStageScope
    operation_id: OperationIdText
    executor: EffectExecutorKind
    target: EffectTarget
    target_digest: Sha256Hex
    display_target: str
    effect_class: EffectClass
    policy_snapshot_ref: str
    policy: EffectPolicy
    agent_hold: bool
    revisions: tuple[EffectStageRevision, ...]
    status: EffectStageStatus
    projection_required: bool = False
    projection_binding: EffectProjectionBinding | None = None
    decision: EffectStageDecision | None = None
    row_decisions: tuple[EffectRowDecisionState, ...] = ()
    superseded_revision: int | None = None
    created_at: str
    updated_at: str

    @field_validator("stage_id")
    @classmethod
    def _stage_id_is_valid(cls, value: str) -> str:
        EffectStageIdCodec.parse(value)
        return value

    @model_validator(mode="after")
    def _stage_is_consistent(self) -> EffectStageState:
        if not self.revisions:
            raise ValueError("an effect stage requires at least one revision")
        latest = self.revisions[-1]
        initial = self.revisions[0]
        for revision in self.revisions:
            if (
                revision.target_ref != self.target.target_ref
                or revision.target_digest != self.target_digest
                or revision.display_target != self.display_target
            ):
                raise ValueError(
                    "every revision must retain the immutable stage target"
                )
            if (
                revision.precondition_ref != initial.precondition_ref
                or revision.precondition_digest != initial.precondition_digest
            ):
                raise ValueError(
                    "every revision must retain the immutable stage precondition"
                )
            parsed_ref = ProposalUriCodec.parse(revision.proposal_ref)
            if (
                parsed_ref.stage_id != self.stage_id
                or parsed_ref.revision != revision.revision
            ):
                raise ValueError(
                    "proposal_ref must identify its owning stage and revision"
                )
        if self.decision is not None and self.decision.revision != latest.revision:
            raise ValueError("decision must bind the current revision")
        if self.projection_binding is not None:
            binding = self.projection_binding
            if (
                binding.revision != latest.revision
                or binding.proposal_digest != latest.proposal_digest
                or binding.target_digest != self.target_digest
            ):
                raise ValueError(
                    "projection binding must identify the current revision"
                )
        return self

    @property
    def current_revision(self) -> EffectStageRevision:
        return self.revisions[-1]

    @property
    def approval_ready(self) -> bool:
        """Whether the current revision can receive an approval decision."""

        if not self.projection_required:
            return True
        binding = self.projection_binding
        current = self.current_revision
        return bool(
            binding is not None
            and binding.revision == current.revision
            and binding.proposal_digest == current.proposal_digest
            and binding.target_digest == self.target_digest
        )


class EffectCommitCommand(RuntimeContract):
    """Body-free A4 command.  A5 alone may claim and execute it."""

    run_id: str
    stage_id: str
    revision: int = Field(ge=1)
    decision_ledger_id: str
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    idempotency_key: str
    row_keys: tuple[str, ...] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    retry_basis_ledger_id: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    # E2 decision boundary copies this closed set onto newly governed work.
    # ``None`` remains the explicit compatibility shape for old A4 commands.
    governed_capabilities: tuple[RolloutCapability, ...] | None = Field(
        default=None,
        # The mark is additive for new governed work.  Do not alter the
        # serialized compatibility shape of pre-E2 commands.
        exclude_if=lambda value: value is None,
    )

    @field_validator("run_id")
    @classmethod
    def _run_id_is_safe(cls, value: str) -> str:
        if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
            raise ValueError("run_id must be a stable opaque identifier")
        return value

    @field_validator("stage_id")
    @classmethod
    def _stage_id_is_valid(cls, value: str) -> str:
        EffectStageIdCodec.parse(value)
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency_key_is_safe(cls, value: str) -> str:
        if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
            raise ValueError("idempotency_key must be a stable opaque key")
        return value

    @field_validator("row_keys")
    @classmethod
    def _row_keys_are_unique(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is not None and (not value or len(value) != len(set(value))):
            raise ValueError("row_keys must contain unique row keys")
        return value

    @field_validator("retry_basis_ledger_id")
    @classmethod
    def _retry_basis_is_opaque(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or len(value) > 255
            or value != value.strip()
            or "/" in value
            or "\\" in value
        ):
            raise ValueError("retry_basis_ledger_id must be an opaque identifier")
        return value


class EffectDispatchRequest(RuntimeContract):
    """Exact server-derived facts consumed by the shared effect dispatcher.

    A4 stages use ``proposal://`` references while the older staged-write
    ledger uses ``draft://`` / ``stage://`` references.  Both are accepted only
    here, after their respective approval folds have proved the exact revision.
    Executors receive this one contract, never a mutable stage or queue body.
    """

    stage_id: str = Field(min_length=1, max_length=255)
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    executor: EffectExecutorKind
    target_ref: str = Field(min_length=1, max_length=_REF_MAX_LENGTH)
    target_digest: Sha256Hex
    proposal_ref: str = Field(min_length=1, max_length=_REF_MAX_LENGTH)
    proposal_content_ref: str = Field(min_length=1, max_length=_REF_MAX_LENGTH)
    proposal_digest: Sha256Hex
    actor: EffectActor
    decision_ledger_id: str = Field(min_length=1, max_length=255)
    row_keys: tuple[str, ...] | None = None

    @field_validator("stage_id", "idempotency_key", "decision_ledger_id")
    @classmethod
    def _identifier_is_safe(cls, value: str) -> str:
        if value != value.strip() or "/" in value or "\\" in value:
            raise ValueError("effect dispatch identifier must be an opaque token")
        return value

    @field_validator("target_ref", "proposal_ref", "proposal_content_ref")
    @classmethod
    def _reference_is_safe(cls, value: str) -> str:
        return _safe_reference(value, "effect dispatch reference")

    @field_validator("row_keys")
    @classmethod
    def _row_keys_are_unique(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is not None and (not value or len(value) != len(set(value))):
            raise ValueError("row_keys must contain unique row keys")
        return value


def validate_idempotency_key(value: str) -> str:
    """Validate a mutation key before passing it to a persistence port."""

    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise ValueError("idempotency_key must be a stable opaque key")
    return value


def _safe_reference(value: str, label: str) -> str:
    decoded = value
    while isinstance(decoded, str):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    normalised = decoded.replace("\\", "/") if isinstance(decoded, str) else decoded
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _REF_MAX_LENGTH
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or "://" not in value
        or value.startswith(("/", "~", "\\"))
        or value.lower().startswith(("file://", "filesystem://", "data:"))
        or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
        or not isinstance(normalised, str)
        or normalised.startswith(("/", "~"))
        or normalised.lower().startswith(("file://", "filesystem://", "data:"))
        or (len(normalised) >= 3 and normalised[1:3] == ":/")
        or "\x00" in normalised
        or any(part in {".", ".."} for part in normalised.split("/"))
    ):
        raise ValueError(f"{label} must be an opaque safe URI reference")
    return value


def validate_proposal_content_ref(value: str) -> str:
    """Validate one server-owned immutable content locator.

    Schemes remain extensible; safety is structural and proposal identity is kept
    separate.  Physical paths and inline bodies are never accepted.
    """

    return validate_immutable_content_ref(value)


def _require_ref_digest_pair(
    reference: str | None,
    digest: str | None,
    label: str,
) -> None:
    if (reference is None) != (digest is None):
        raise ValueError(f"{label}_ref and {label}_digest must be supplied together")


def validate_proposal_executor_pair(
    kind: EffectProposalKind,
    executor: EffectExecutorKind,
) -> None:
    allowed: dict[EffectProposalKind, frozenset[EffectExecutorKind]] = {
        EffectProposalKind.CANONICAL_ARGUMENTS: frozenset(
            {EffectExecutorKind.MCP, EffectExecutorKind.BUILTIN}
        ),
        EffectProposalKind.ARTIFACT_REVISION: frozenset(EffectExecutorKind),
        EffectProposalKind.WORKSPACE_CHANGE_SET: frozenset(
            {EffectExecutorKind.WORKSPACE}
        ),
        EffectProposalKind.ROW_SET: frozenset(
            {EffectExecutorKind.MCP, EffectExecutorKind.BUILTIN}
        ),
        EffectProposalKind.BROWSER_SUBMISSION: frozenset({EffectExecutorKind.BROWSER}),
        EffectProposalKind.SANDBOX_PATCH: frozenset({EffectExecutorKind.SANDBOX}),
        EffectProposalKind.BUILTIN_PAYLOAD: frozenset({EffectExecutorKind.BUILTIN}),
    }
    if executor not in allowed[kind]:
        raise ValueError(f"proposal kind {kind.value} is incompatible with executor")


__all__ = [
    "EffectActorIdentity",
    "EffectCommitCommand",
    "EffectDispatchRequest",
    "EffectPolicyResolution",
    "EffectPolicySnapshot",
    "EffectProposalKind",
    "EffectRevisionProposal",
    "EffectRowDecisionState",
    "EffectStageDecision",
    "EffectStageRevision",
    "EffectStageScope",
    "EffectStageState",
    "EffectStageStatus",
    "ProposedEffect",
    "validate_proposal_content_ref",
    "validate_proposal_executor_pair",
    "validate_idempotency_key",
]
