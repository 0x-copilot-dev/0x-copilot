"""Immutable contracts for one run-bound quality control plane.

The snapshot contains only authority-neutral references, revisions, digests,
closed feature modes, and aggregate budget limits. It is not an authorization
cache: connector, workspace, effect, and evidence access remain call-time
decisions at their existing owners.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureModeSet,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_REF_LENGTH = 256
_BUDGET_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$"
_BUDGET_REF_PATTERN = (
    r"^budget://[A-Za-z0-9][A-Za-z0-9._:-]{0,159}/sha256/[0-9a-f]{64}$"
)


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class RunPolicyRevisions(RuntimeContract):
    """Closed revision references frozen before the first model call."""

    prompt: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    capability: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    context: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    tool_controller: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    concurrency: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    dataflow: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    mcp_freshness: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    delegation: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    model_route: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    workspace_edit: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    answer_verification: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)

    @field_validator("*")
    @classmethod
    def _strip_revision(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy revision references must be non-empty")
        return normalized


class BudgetEnvelope(RuntimeContract):
    """Aggregate run ceilings referenced by an immutable snapshot.

    ``None`` means that this envelope does not introduce a limit for that
    dimension; existing platform/provider limits still apply. This record
    contains no reservation or mutable usage state.
    """

    schema_version: Literal[1] = 1
    budget_envelope_id: str = Field(pattern=_BUDGET_ID_PATTERN)
    revision: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    max_model_turns: PositiveInt | None = None
    max_tool_calls: PositiveInt | None = None
    max_subagent_calls: PositiveInt | None = None
    max_input_tokens: PositiveInt | None = None
    max_output_tokens: PositiveInt | None = None
    max_cost_microusd: NonNegativeInt | None = None
    deadline_at: datetime | None = None
    envelope_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("deadline_at")
    @classmethod
    def _aware_deadline(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value, "deadline_at")

    @model_validator(mode="after")
    def _digest_matches(self) -> "BudgetEnvelope":
        if self.envelope_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError("budget envelope digest does not match its canonical body")
        return self

    def digest_payload(self) -> dict[str, object]:
        """Return the complete immutable body covered by ``envelope_digest``."""

        return self.model_dump(
            mode="json",
            exclude={"envelope_digest"},
            exclude_none=False,
        )

    @classmethod
    def create(
        cls,
        *,
        budget_envelope_id: str,
        revision: str,
        max_model_turns: int | None = None,
        max_tool_calls: int | None = None,
        max_subagent_calls: int | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_microusd: int | None = None,
        deadline_at: datetime | None = None,
    ) -> "BudgetEnvelope":
        payload = {
            "schema_version": 1,
            "budget_envelope_id": budget_envelope_id,
            "revision": revision,
            "max_model_turns": max_model_turns,
            "max_tool_calls": max_tool_calls,
            "max_subagent_calls": max_subagent_calls,
            "max_input_tokens": max_input_tokens,
            "max_output_tokens": max_output_tokens,
            "max_cost_microusd": max_cost_microusd,
            "deadline_at": deadline_at,
        }
        digest_payload = cls.model_construct(
            **payload,
            envelope_digest="0" * 64,
        ).digest_payload()
        return cls(
            **payload,
            envelope_digest=canonical_json_sha256(digest_payload),
        )

    @property
    def revision_ref(self) -> str:
        """Return the self-authenticating logical revision reference.

        A budget envelope is small immutable control metadata, not a protected
        CAS body. The reference identifies its reviewed revision and digest; it
        must not be registered with or deleted from an unrelated blob store.
        """

        return f"budget://{self.budget_envelope_id}/sha256/{self.envelope_digest}"


class RunControlSnapshot(RuntimeContract):
    """One immutable, replayable policy assignment for a run."""

    schema_version: Literal[1] = 1
    snapshot_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(min_length=1, max_length=160)
    subject_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    deployment_profile: str = Field(min_length=1, max_length=80)
    harness_variant_ref: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    task_policy_selection_ref: str = Field(
        min_length=1,
        max_length=_MAX_REF_LENGTH,
    )
    policy_revisions: RunPolicyRevisions
    feature_modes: FeatureModeSet = Field(default_factory=FeatureModeSet)
    budget_envelope_ref: str = Field(pattern=_BUDGET_REF_PATTERN)
    assignment_revision: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    created_at: datetime
    snapshot_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        return _aware(value, "created_at")

    @field_validator(
        "deployment_profile",
        "harness_variant_ref",
        "task_policy_selection_ref",
        "budget_envelope_ref",
        "assignment_revision",
    )
    @classmethod
    def _strip_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("snapshot references must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _digest_matches(self) -> "RunControlSnapshot":
        if self.snapshot_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError(
                "run control snapshot digest does not match canonical body"
            )
        return self

    def digest_payload(self) -> dict[str, object]:
        """Return semantic assignment fields covered by ``snapshot_digest``.

        ``snapshot_id`` and ``created_at`` are record identity/observation
        fields, so concurrent builders with the same assignment retain the same
        semantic digest and converge on the first durable record.
        """

        return self.model_dump(
            mode="json",
            exclude={"snapshot_id", "created_at", "snapshot_digest"},
        )

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        subject_fingerprint: str,
        deployment_profile: str,
        harness_variant_ref: str,
        task_policy_selection_ref: str,
        policy_revisions: RunPolicyRevisions,
        feature_modes: FeatureModeSet,
        budget_envelope_ref: str,
        assignment_revision: str,
        snapshot_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "RunControlSnapshot":
        payload = {
            "schema_version": 1,
            "snapshot_id": snapshot_id or uuid4().hex,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "subject_fingerprint": subject_fingerprint,
            "deployment_profile": deployment_profile,
            "harness_variant_ref": harness_variant_ref,
            "task_policy_selection_ref": task_policy_selection_ref,
            "policy_revisions": policy_revisions,
            "feature_modes": feature_modes,
            "budget_envelope_ref": budget_envelope_ref,
            "assignment_revision": assignment_revision,
            "created_at": created_at or datetime.now(timezone.utc),
        }
        provisional = cls.model_construct(
            **payload,
            snapshot_digest="0" * 64,
        )
        return cls(
            **payload,
            snapshot_digest=canonical_json_sha256(provisional.digest_payload()),
        )


class RunControlDecision(RuntimeContract):
    """Append-only lineage for one post-bind feature decision."""

    schema_version: Literal[1] = 1
    decision_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    snapshot_id: str = Field(min_length=1, max_length=160)
    phase: str = Field(min_length=1, max_length=80)
    feature: AgentQualityFeature
    policy_revision: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    input_digest: str = Field(pattern=_SHA256_PATTERN)
    outcome_code: str = Field(min_length=1, max_length=120)
    record_ref: str | None = Field(default=None, max_length=512)
    parent_decision_refs: tuple[str, ...] = Field(default=(), max_length=64)
    created_at: datetime
    decision_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        return _aware(value, "created_at")

    @field_validator("phase", "policy_revision", "outcome_code")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("decision fields must be non-empty")
        return normalized

    @field_validator("record_ref")
    @classmethod
    def _strip_optional_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("record_ref must be non-empty when supplied")
        return normalized

    @field_validator("parent_decision_refs")
    @classmethod
    def _unique_parent_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item or len(item) > 160 for item in normalized):
            raise ValueError("parent decision refs must be bounded non-empty ids")
        if len(set(normalized)) != len(normalized):
            raise ValueError("parent decision refs must be unique")
        return normalized

    @model_validator(mode="after")
    def _digest_matches(self) -> "RunControlDecision":
        if self.decision_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError(
                "run control decision digest does not match canonical body"
            )
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"created_at", "decision_digest"},
        )

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        run_id: str,
        snapshot_id: str,
        phase: str,
        feature: AgentQualityFeature,
        policy_revision: str,
        input_digest: str,
        outcome_code: str,
        record_ref: str | None = None,
        parent_decision_refs: tuple[str, ...] = (),
        created_at: datetime | None = None,
    ) -> "RunControlDecision":
        payload = {
            "schema_version": 1,
            "decision_id": decision_id,
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "phase": phase,
            "feature": feature,
            "policy_revision": policy_revision,
            "input_digest": input_digest,
            "outcome_code": outcome_code,
            "record_ref": record_ref,
            "parent_decision_refs": parent_decision_refs,
            "created_at": created_at or datetime.now(timezone.utc),
        }
        provisional = cls.model_construct(
            **payload,
            decision_digest="0" * 64,
        )
        return cls(
            **payload,
            decision_digest=canonical_json_sha256(provisional.digest_payload()),
        )


__all__ = [
    "BudgetEnvelope",
    "RunControlDecision",
    "RunControlSnapshot",
    "RunPolicyRevisions",
]
