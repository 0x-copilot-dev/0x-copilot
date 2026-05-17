"""Pydantic v2 models for the tier-2 adapter registry."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Final
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SCHEME_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_LAYOUT_VALUES: Final = frozenset({"form", "table", "kanban", "definition-list"})
_MAX_SOURCE_BYTES: Final = 256 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AdapterCandidateStatus(StrEnum):
    SUBMITTED = "submitted"
    IN_REVIEW = "in-review"
    CHANGES_REQUESTED = "changes-requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class AdapterReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request-changes"


class _RegistryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HarvestMetrics(_RegistryContract):
    """Anonymized metrics carried with a candidate submission.

    Reviewers see counts only — no payload bytes ever cross the
    tenant boundary; the desktop harvester (7B) strips identifying
    state before submission. ``zero_error_sessions`` is the §9.5.3
    success-criteria metric; the other fields are advisory.
    """

    zero_error_sessions: int = Field(ge=0)
    total_sessions: int = Field(ge=0)
    user_reported_issues: int = Field(ge=0, default=0)
    generator_model: str | None = Field(default=None, max_length=128)


class AdapterCandidateSubmission(_RegistryContract):
    """Wire shape for the desktop harvest -> facade -> backend hop."""

    scheme: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    layout: str = Field(min_length=1, max_length=32)
    source: str = Field(min_length=1, max_length=_MAX_SOURCE_BYTES)
    harvest_metrics: HarvestMetrics

    @field_validator("scheme")
    @classmethod
    def _validate_scheme(cls, value: str) -> str:
        text = value.strip()
        if not _SCHEME_PATTERN.fullmatch(text):
            raise ValueError(
                "scheme must match [A-Za-z][A-Za-z0-9._:-]{0,127}",
            )
        return text

    @field_validator("layout")
    @classmethod
    def _validate_layout(cls, value: str) -> str:
        text = value.strip().lower()
        if text not in _LAYOUT_VALUES:
            raise ValueError(
                f"layout must be one of {sorted(_LAYOUT_VALUES)}",
            )
        return text


class AdapterCandidateRecord(_RegistryContract):
    """Persistence record for an ``adapter_candidates`` row.

    ``tenant_id`` is the origin tenant; the route layer rebinds this
    from the verified bearer so a caller cannot spoof another
    tenant's id.
    """

    candidate_id: str = Field(default_factory=lambda: f"acan_{uuid4().hex}")
    tenant_id: str = Field(min_length=1, max_length=64)
    submitter_user_id: str = Field(min_length=1, max_length=128)
    scheme: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    layout: str = Field(min_length=1, max_length=32)
    storage_key: str = Field(min_length=1, max_length=512)
    source_digest: str = Field(min_length=64, max_length=64)
    source_bytes: int = Field(ge=1)
    harvest_metrics: HarvestMetrics
    status: AdapterCandidateStatus = AdapterCandidateStatus.SUBMITTED
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AdapterCandidateView(_RegistryContract):
    """Wire view returned by the admin queue.

    Reviewer never sees raw tenant data — the candidate is intentionally
    fetched alongside synthetic samples generated client-side (7C).
    ``source`` is included so the reviewer can read the code; the bytes
    contain no payload because the contract is pure-render (D28).
    """

    candidate_id: str
    tenant_id: str
    submitter_user_id: str
    scheme: str
    version: int
    layout: str
    source: str
    source_digest: str
    harvest_metrics: HarvestMetrics
    status: AdapterCandidateStatus
    created_at: datetime
    updated_at: datetime


class AdapterCandidateListResponse(_RegistryContract):
    candidates: tuple[AdapterCandidateView, ...] = ()


class AdapterReviewDecisionRequest(_RegistryContract):
    action: AdapterReviewAction
    notes: str | None = Field(default=None, max_length=2048)


class AdapterReviewRecord(_RegistryContract):
    review_id: str = Field(default_factory=lambda: f"arev_{uuid4().hex}")
    candidate_id: str
    reviewer_user_id: str = Field(min_length=1, max_length=128)
    reviewer_org_id: str = Field(min_length=1, max_length=64)
    action: AdapterReviewAction
    notes: str | None = None
    decided_at: datetime = Field(default_factory=_now)


class PromotedAdapterRecord(_RegistryContract):
    """Persistence record for a promoted adapter.

    Once approved a candidate is frozen into this table; the source
    bytes stay in the same ``SourceStorage`` slot as the candidate but
    cannot be mutated (the digest pins them).
    """

    promoted_id: str = Field(default_factory=lambda: f"aprm_{uuid4().hex}")
    scheme: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    schema_version: int = Field(ge=1)
    layout: str = Field(min_length=1, max_length=32)
    storage_key: str = Field(min_length=1, max_length=512)
    source_digest: str = Field(min_length=64, max_length=64)
    source_bytes: int = Field(ge=1)
    origin_tenant_id: str = Field(min_length=1, max_length=64)
    source_candidate_id: str
    promoted_by_user_id: str = Field(min_length=1, max_length=128)
    promoted_at: datetime = Field(default_factory=_now)


class PromotedAdapterView(_RegistryContract):
    promoted_id: str
    scheme: str
    version: int
    schema_version: int
    layout: str
    source: str
    source_digest: str
    origin: str = "community"
    promoted_at: datetime


class PromotedAdaptersResponse(_RegistryContract):
    adapters: tuple[PromotedAdapterView, ...] = ()


class TenantAdapterSettingsRecord(_RegistryContract):
    """One row per tenant; absence => default (opted in).

    Phase 7 only ships the boolean ``opted_out`` knob; a future row
    may add per-scheme blocklists without changing the route shape.
    """

    tenant_id: str = Field(min_length=1, max_length=64)
    opted_out: bool = False
    updated_at: datetime = Field(default_factory=_now)
    updated_by_user_id: str | None = Field(default=None, max_length=128)


class AdapterRegistryOptOutRequest(_RegistryContract):
    opted_out: bool


class AdapterRegistryOptOutResponse(_RegistryContract):
    tenant_id: str
    opted_out: bool
    updated_at: datetime


class AdapterRegistryAuditEventRecord(_RegistryContract):
    """One row in ``adapter_registry_audit_events``.

    Signed with the same `_AuditChain` machinery used by
    ``mcp_audit_events`` / ``skill_audit_events`` so retroactive
    tampering with a row breaks the chain at append-time.
    """

    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    tenant_id: str = Field(min_length=1, max_length=64)
    actor_user_id: str = Field(min_length=1, max_length=128)
    candidate_id: str | None = None
    promoted_id: str | None = None
    action: str = Field(min_length=1, max_length=64)
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    seq: int | None = None
    prev_hash: bytes | None = None
    signature: bytes | None = None
    key_version: int | None = None


__all__ = [
    "AdapterCandidateListResponse",
    "AdapterCandidateRecord",
    "AdapterCandidateStatus",
    "AdapterCandidateSubmission",
    "AdapterCandidateView",
    "AdapterRegistryAuditEventRecord",
    "AdapterRegistryOptOutRequest",
    "AdapterRegistryOptOutResponse",
    "AdapterReviewAction",
    "AdapterReviewDecisionRequest",
    "AdapterReviewRecord",
    "HarvestMetrics",
    "PromotedAdapterRecord",
    "PromotedAdapterView",
    "PromotedAdaptersResponse",
    "TenantAdapterSettingsRecord",
]
