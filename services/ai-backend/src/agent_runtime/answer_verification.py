"""Content-safe contracts and deterministic verification for final answers.

The verifier consumes facts already resolved by trusted runtime adapters.  It performs
no retrieval, authorization, secret scanning, persistence, or model invocation, and it
never carries source bodies, excerpts, URLs, or detected secret values.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from typing import TypeAlias

from pydantic import Field, PositiveInt, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_ANSWER_BYTES = 65_536


class RequirementStatus(StrEnum):
    """Declared completion state for one inspectable answer requirement."""

    SATISFIED = "satisfied"
    PARTIAL = "partial"
    UNSATISFIED = "unsatisfied"
    NOT_APPLICABLE = "not_applicable"


class RequirementCompletionSource(StrEnum):
    """Trusted origin from which a requirement was compiled."""

    EXPLICIT_REQUEST = "explicit_request"
    TASK_PLAN = "task_plan"
    OPERATION_RESULT = "operation_result"
    HARNESS_POLICY = "harness_policy"


class ClaimKind(StrEnum):
    """How a claim relates to evidence and the answer author."""

    OBSERVED = "observed"
    ATTRIBUTED = "attributed"
    INFERENCE = "inference"
    ESTIMATE = "estimate"
    RECOMMENDATION = "recommendation"
    USER_PROVIDED = "user_provided"
    UNKNOWN = "unknown"


class ClaimMateriality(StrEnum):
    """Whether an incorrect claim could materially change the user's decision."""

    MATERIAL = "material"
    SUPPORTING = "supporting"
    INCIDENTAL = "incidental"


class EvidenceRelationship(StrEnum):
    """Declared relationship between an evidence ref and a claim."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXTUALIZES = "contextualizes"


class EvidenceSourceClass(StrEnum):
    """Resolver-owned source class used for mechanical claim compatibility."""

    USER_INPUT = "user_input"
    CONVERSATION_RECORD = "conversation_record"
    WORKSPACE_CONTENT = "workspace_content"
    WEB_DOCUMENT = "web_document"
    SEARCH_SNIPPET = "search_snippet"
    CONNECTOR_RECORD = "connector_record"
    TOOL_RESULT = "tool_result"
    EFFECT_RECEIPT = "effect_receipt"
    MEMORY = "memory"
    SYSTEM_RECORD = "system_record"
    MODEL_OUTPUT = "model_output"


class EvidenceTrustClass(StrEnum):
    """Resolver-owned trust class; never accepted from answer-model output."""

    AUTHORITATIVE = "authoritative"
    PRIMARY = "primary"
    VERIFIED = "verified"
    USER_ASSERTED = "user_asserted"
    DERIVED = "derived"
    UNVERIFIED = "unverified"


class EvidenceAccessState(StrEnum):
    """Trusted resolver result for an opaque evidence reference."""

    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    REVOKED = "revoked"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class EvidenceFreshnessState(StrEnum):
    """Trusted freshness result; the model cannot supply this state."""

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"


class EvidenceLocatorState(StrEnum):
    """Trusted validation result for the binding's source locator."""

    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"


class ConflictResolutionKind(StrEnum):
    """Closed vocabulary for presenting a known evidence conflict."""

    PREFER_NEWER = "prefer_newer"
    PREFER_PRIMARY = "prefer_primary"
    SCOPED_DIFFERENCE = "scoped_difference"
    UNRESOLVED = "unresolved"
    NOT_MATERIAL = "not_material"


class VerificationStatus(StrEnum):
    """Overall deterministic verification outcome."""

    PASSED = "passed"
    REPAIRABLE = "repairable"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class VerificationDisposition(StrEnum):
    """Required handling for one verification finding."""

    REPAIR = "repair"
    DEGRADE = "degrade"
    BLOCK = "block"
    WARN = "warn"


class VerificationIssueCode(StrEnum):
    """Stable, content-free verifier findings."""

    ANSWER_TOO_LARGE = "answer_too_large"
    ANSWER_MATERIAL_MISMATCH = "answer_material_mismatch"
    RUN_BINDING_MISMATCH = "run_binding_mismatch"
    REQUIREMENT_LEDGER_MISMATCH = "requirement_ledger_mismatch"
    PROFILE_REVISION_MISMATCH = "profile_revision_mismatch"
    DUPLICATE_REQUIREMENT = "duplicate_requirement"
    DUPLICATE_REQUIREMENT_RESULT = "duplicate_requirement_result"
    UNKNOWN_REQUIREMENT_RESULT = "unknown_requirement_result"
    MISSING_REQUIRED_REQUIREMENT_RESULT = "missing_required_requirement_result"
    REQUIRED_REQUIREMENT_INCOMPLETE = "required_requirement_incomplete"
    REQUIREMENT_SUPPORT_MISSING = "requirement_support_missing"
    REQUIREMENT_RUN_MISMATCH = "requirement_run_mismatch"
    REQUIREMENT_SOURCE_MISMATCH = "requirement_source_mismatch"
    DUPLICATE_SPAN = "duplicate_span"
    SPAN_OUT_OF_BOUNDS = "span_out_of_bounds"
    UNKNOWN_SPAN = "unknown_span"
    DUPLICATE_CLAIM = "duplicate_claim"
    MATERIAL_CLAIM_UNSUPPORTED = "material_claim_unsupported"
    CLAIM_SOURCE_INCOMPATIBLE = "claim_source_incompatible"
    CLAIM_TRUST_INSUFFICIENT = "claim_trust_insufficient"
    CLAIM_CONFIDENCE_UNSUPPORTED = "claim_confidence_unsupported"
    DUPLICATE_EVIDENCE_FACT = "duplicate_evidence_fact"
    EVIDENCE_FACT_MISSING = "evidence_fact_missing"
    EVIDENCE_UNAUTHORIZED = "evidence_unauthorized"
    EVIDENCE_REVOKED = "evidence_revoked"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EVIDENCE_EXPIRED = "evidence_expired"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    EVIDENCE_DIGEST_MISMATCH = "evidence_digest_mismatch"
    EVIDENCE_CITATION_MISMATCH = "evidence_citation_mismatch"
    EVIDENCE_LOCATOR_INVALID = "evidence_locator_invalid"
    EVIDENCE_LOCATOR_UNKNOWN = "evidence_locator_unknown"
    EVIDENCE_STALE = "evidence_stale"
    EVIDENCE_FRESHNESS_UNKNOWN = "evidence_freshness_unknown"
    EVIDENCE_NOT_YET_VALID = "evidence_not_yet_valid"
    EVIDENCE_VALIDITY_EXPIRED = "evidence_validity_expired"
    EVIDENCE_OBSERVATION_IN_FUTURE = "evidence_observation_in_future"
    DUPLICATE_CONFLICT_FACT = "duplicate_conflict_fact"
    DUPLICATE_CONFLICT_RESOLUTION = "duplicate_conflict_resolution"
    UNKNOWN_CONFLICT_RESOLUTION = "unknown_conflict_resolution"
    CONFLICT_RESOLUTION_MISSING = "conflict_resolution_missing"
    CONFLICT_RESOLUTION_INVALID = "conflict_resolution_invalid"
    SECRET_LEAK_DETECTED = "secret_leak_detected"


class CitationIdentity(RuntimeContract):
    """Existing citation-ledger identity without source metadata or content."""

    citation_id: str = Field(min_length=2, max_length=16)
    ordinal: PositiveInt


class EvidenceReference(RuntimeContract):
    """Opaque evidence identity, optionally linked to a citation ledger ordinal."""

    evidence_ref: str = Field(min_length=1, max_length=512)
    citation: CitationIdentity | None = None


class AnswerRequirement(RuntimeContract):
    """One externally inspectable requirement; prose lives behind description_ref."""

    requirement_id: str = Field(min_length=1, max_length=128)
    description_ref: str = Field(min_length=1, max_length=512)
    completion_source: RequirementCompletionSource
    completion_source_digest: str = Field(pattern=_SHA256_PATTERN)
    required: bool = True


class AnswerRequirementLedger(RuntimeContract):
    """Bounded requirement inventory compiled before answer synthesis."""

    ledger_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=255)
    profile_revision: str = Field(min_length=1, max_length=128)
    source_request_digest: str = Field(pattern=_SHA256_PATTERN)
    requirements: tuple[AnswerRequirement, ...] = Field(max_length=50)


class AnswerSpan(RuntimeContract):
    """Character offsets into protected answer content."""

    span_id: str = Field(min_length=1, max_length=128)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def _end_follows_start(self) -> AnswerSpan:
        if self.end <= self.start:
            raise ValueError("answer span end must be greater than start")
        return self


class AnswerRequirementResult(RuntimeContract):
    """Answer-declared outcome for a requirement."""

    requirement_id: str = Field(min_length=1, max_length=128)
    completion_run_id: str = Field(min_length=1, max_length=255)
    completion_source: RequirementCompletionSource
    completion_source_digest: str = Field(pattern=_SHA256_PATTERN)
    status: RequirementStatus
    answer_span_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    evidence_refs: tuple[EvidenceReference, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    explanation_code: str | None = Field(default=None, max_length=128)


class AnswerEvidenceBinding(RuntimeContract):
    """Claim binding to an opaque, digest-bound evidence locator."""

    evidence: EvidenceReference
    source_digest: str = Field(pattern=_SHA256_PATTERN)
    locator_ref: str | None = Field(default=None, max_length=512)
    relationship: EvidenceRelationship


class AnswerClaim(RuntimeContract):
    """One answer claim and its declared evidence linkage."""

    claim_id: str = Field(min_length=1, max_length=128)
    answer_span_id: str = Field(min_length=1, max_length=128)
    kind: ClaimKind
    materiality: ClaimMateriality
    confidence: int = Field(ge=0, le=1_000)
    evidence_bindings: tuple[AnswerEvidenceBinding, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    freshness_required: bool = False


class AnswerConflictResolution(RuntimeContract):
    """Answer-declared handling of one trusted conflict set."""

    conflict_set_id: str = Field(min_length=1, max_length=128)
    evidence_refs: tuple[EvidenceReference, ...] = Field(min_length=2, max_length=32)
    resolution: ConflictResolutionKind
    answer_span_ids: tuple[str, ...] = Field(min_length=1, max_length=32)


class ProtectedAnswerContent(RuntimeContract):
    """Protected answer identity; raw answer text never enters verifier records."""

    content_ref: str = Field(
        min_length=11,
        max_length=512,
        pattern=r"^payload://[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    content_digest: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=0, le=_MAX_ANSWER_BYTES)
    character_count: int = Field(ge=0, le=_MAX_ANSWER_BYTES)


class AnswerEnvelope(RuntimeContract):
    """Versioned metadata envelope for protected final-answer content."""

    run_id: str = Field(min_length=1, max_length=255)
    envelope_revision: str = Field(min_length=1, max_length=128)
    profile_revision: str = Field(min_length=1, max_length=128)
    requirement_ledger_id: str = Field(min_length=1, max_length=128)
    answer_content: ProtectedAnswerContent
    spans: tuple[AnswerSpan, ...] = Field(default_factory=tuple, max_length=256)
    requirement_results: tuple[AnswerRequirementResult, ...] = Field(
        default_factory=tuple,
        max_length=50,
    )
    claims: tuple[AnswerClaim, ...] = Field(default_factory=tuple, max_length=100)
    conflict_resolutions: tuple[AnswerConflictResolution, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )
    limitation_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    unresolved_item_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=50)


class EvidenceVerificationFact(RuntimeContract):
    """Trusted source-lifecycle facts; deliberately excludes all evidence content."""

    evidence: EvidenceReference
    source_digest: str = Field(pattern=_SHA256_PATTERN)
    source_class: EvidenceSourceClass
    trust_class: EvidenceTrustClass
    max_supported_confidence: int = Field(ge=0, le=1_000)
    access_state: EvidenceAccessState
    freshness_state: EvidenceFreshnessState
    locator_state: EvidenceLocatorState
    locator_ref: str | None = Field(default=None, max_length=512)
    observed_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None

    @field_validator("observed_at", "valid_from", "valid_until")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("evidence timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validity_window_is_ordered(self) -> EvidenceVerificationFact:
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        return self


class AnswerMaterialVerificationFact(RuntimeContract):
    """Trusted resolution metadata for protected final-answer bytes."""

    run_id: str = Field(min_length=1, max_length=255)
    content_ref: str = Field(min_length=1, max_length=512)
    content_digest: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    character_count: int = Field(ge=0)
    resolved_at: datetime

    @field_validator("resolved_at")
    @classmethod
    def _resolved_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("resolved_at must be timezone-aware")
        return value


class EvidenceConflictFact(RuntimeContract):
    """Trusted declaration that a bounded set of evidence refs conflicts."""

    conflict_set_id: str = Field(min_length=1, max_length=128)
    evidence_refs: tuple[EvidenceReference, ...] = Field(min_length=2, max_length=32)
    material: bool = True
    resolution_required: bool = True


class SecretLeakFinding(RuntimeContract):
    """Safe output from a separate scanner; never includes the matched secret."""

    finding_id: str = Field(min_length=1, max_length=128)
    detector_code: str = Field(min_length=1, max_length=128)
    answer_span_id: str | None = Field(default=None, max_length=128)


class AnswerVerificationFacts(RuntimeContract):
    """Complete trusted fact snapshot consumed by the pure verifier."""

    run_id: str = Field(min_length=1, max_length=255)
    profile_revision: str = Field(min_length=1, max_length=128)
    checked_at: datetime
    answer_material: AnswerMaterialVerificationFact
    evidence: tuple[EvidenceVerificationFact, ...] = Field(
        default_factory=tuple,
        max_length=200,
    )
    conflicts: tuple[EvidenceConflictFact, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )
    secret_leak_findings: tuple[SecretLeakFinding, ...] = Field(
        default_factory=tuple,
        max_length=50,
    )

    @field_validator("checked_at")
    @classmethod
    def _checked_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("checked_at must be timezone-aware")
        return value


class VerificationIssue(RuntimeContract):
    """One deterministic finding with IDs only and no answer/source content."""

    issue_id: str = Field(pattern=r"^avi_[0-9a-f]{24}$")
    code: VerificationIssueCode
    disposition: VerificationDisposition
    requirement_id: str | None = None
    claim_id: str | None = None
    answer_span_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    conflict_set_id: str | None = None


class AnswerVerificationReport(RuntimeContract):
    """Content-free, deterministic result safe for replay and persistence."""

    report_id: str = Field(pattern=r"^avr_[0-9a-f]{24}$")
    envelope_digest: str = Field(pattern=_SHA256_PATTERN)
    requirement_ledger_digest: str = Field(pattern=_SHA256_PATTERN)
    evidence_snapshot_digest: str = Field(pattern=_SHA256_PATTERN)
    profile_revision: str
    status: VerificationStatus
    failures: tuple[VerificationIssue, ...]
    warnings: tuple[VerificationIssue, ...]
    declared_claim_count: int = Field(ge=0)
    unique_claim_count: int = Field(ge=0)
    verified_claim_count: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)
    citation_error_count: int = Field(ge=0)
    freshness_error_count: int = Field(ge=0)
    conflict_error_count: int = Field(ge=0)
    checked_at: datetime


_IssueKey: TypeAlias = tuple[
    VerificationIssueCode,
    VerificationDisposition,
    str | None,
    str | None,
    str | None,
    tuple[str, ...],
    str | None,
]


class AnswerVerifier:
    """Linear-time structural verifier over answer, requirements, and trusted facts."""

    _CLAIMS_REQUIRING_SUPPORT = frozenset(
        {
            ClaimKind.OBSERVED,
            ClaimKind.ATTRIBUTED,
            ClaimKind.INFERENCE,
            ClaimKind.ESTIMATE,
            ClaimKind.USER_PROVIDED,
        }
    )
    _CLAIM_SOURCE_COMPATIBILITY = {
        ClaimKind.OBSERVED: frozenset(
            {
                EvidenceSourceClass.WORKSPACE_CONTENT,
                EvidenceSourceClass.WEB_DOCUMENT,
                EvidenceSourceClass.CONNECTOR_RECORD,
                EvidenceSourceClass.TOOL_RESULT,
                EvidenceSourceClass.EFFECT_RECEIPT,
                EvidenceSourceClass.SYSTEM_RECORD,
            }
        ),
        ClaimKind.ATTRIBUTED: frozenset(
            {
                EvidenceSourceClass.USER_INPUT,
                EvidenceSourceClass.CONVERSATION_RECORD,
                EvidenceSourceClass.WORKSPACE_CONTENT,
                EvidenceSourceClass.WEB_DOCUMENT,
                EvidenceSourceClass.CONNECTOR_RECORD,
                EvidenceSourceClass.TOOL_RESULT,
                EvidenceSourceClass.SYSTEM_RECORD,
            }
        ),
        ClaimKind.INFERENCE: frozenset(
            source
            for source in EvidenceSourceClass
            if source is not EvidenceSourceClass.MODEL_OUTPUT
        ),
        ClaimKind.ESTIMATE: frozenset(
            source
            for source in EvidenceSourceClass
            if source
            not in {
                EvidenceSourceClass.MODEL_OUTPUT,
                EvidenceSourceClass.SEARCH_SNIPPET,
            }
        ),
        ClaimKind.RECOMMENDATION: frozenset(
            source
            for source in EvidenceSourceClass
            if source is not EvidenceSourceClass.MODEL_OUTPUT
        ),
        ClaimKind.USER_PROVIDED: frozenset(
            {
                EvidenceSourceClass.USER_INPUT,
                EvidenceSourceClass.CONVERSATION_RECORD,
            }
        ),
        ClaimKind.UNKNOWN: frozenset(),
    }
    _MATERIAL_TRUST_COMPATIBILITY = {
        ClaimKind.OBSERVED: frozenset(
            {
                EvidenceTrustClass.AUTHORITATIVE,
                EvidenceTrustClass.PRIMARY,
                EvidenceTrustClass.VERIFIED,
            }
        ),
        ClaimKind.ATTRIBUTED: frozenset(
            {
                EvidenceTrustClass.AUTHORITATIVE,
                EvidenceTrustClass.PRIMARY,
                EvidenceTrustClass.VERIFIED,
            }
        ),
        ClaimKind.INFERENCE: frozenset(
            {
                EvidenceTrustClass.AUTHORITATIVE,
                EvidenceTrustClass.PRIMARY,
                EvidenceTrustClass.VERIFIED,
            }
        ),
        ClaimKind.ESTIMATE: frozenset(
            {
                EvidenceTrustClass.AUTHORITATIVE,
                EvidenceTrustClass.PRIMARY,
                EvidenceTrustClass.VERIFIED,
            }
        ),
        ClaimKind.RECOMMENDATION: frozenset(EvidenceTrustClass),
        ClaimKind.USER_PROVIDED: frozenset(
            {
                EvidenceTrustClass.VERIFIED,
                EvidenceTrustClass.USER_ASSERTED,
            }
        ),
        ClaimKind.UNKNOWN: frozenset(),
    }
    _CITATION_CODES = frozenset(
        {
            VerificationIssueCode.DUPLICATE_EVIDENCE_FACT,
            VerificationIssueCode.EVIDENCE_FACT_MISSING,
            VerificationIssueCode.EVIDENCE_UNAUTHORIZED,
            VerificationIssueCode.EVIDENCE_REVOKED,
            VerificationIssueCode.EVIDENCE_NOT_FOUND,
            VerificationIssueCode.EVIDENCE_EXPIRED,
            VerificationIssueCode.EVIDENCE_UNAVAILABLE,
            VerificationIssueCode.EVIDENCE_DIGEST_MISMATCH,
            VerificationIssueCode.EVIDENCE_CITATION_MISMATCH,
            VerificationIssueCode.EVIDENCE_LOCATOR_INVALID,
            VerificationIssueCode.EVIDENCE_LOCATOR_UNKNOWN,
        }
    )
    _FRESHNESS_CODES = frozenset(
        {
            VerificationIssueCode.EVIDENCE_STALE,
            VerificationIssueCode.EVIDENCE_FRESHNESS_UNKNOWN,
            VerificationIssueCode.EVIDENCE_NOT_YET_VALID,
            VerificationIssueCode.EVIDENCE_VALIDITY_EXPIRED,
            VerificationIssueCode.EVIDENCE_OBSERVATION_IN_FUTURE,
        }
    )
    _CONFLICT_CODES = frozenset(
        {
            VerificationIssueCode.DUPLICATE_CONFLICT_FACT,
            VerificationIssueCode.DUPLICATE_CONFLICT_RESOLUTION,
            VerificationIssueCode.UNKNOWN_CONFLICT_RESOLUTION,
            VerificationIssueCode.CONFLICT_RESOLUTION_MISSING,
            VerificationIssueCode.CONFLICT_RESOLUTION_INVALID,
        }
    )

    @classmethod
    def verify(
        cls,
        *,
        envelope: AnswerEnvelope,
        requirements: AnswerRequirementLedger,
        facts: AnswerVerificationFacts,
    ) -> AnswerVerificationReport:
        """Return the same typed report for the same three immutable inputs."""

        issue_keys: set[_IssueKey] = set()

        def add(
            code: VerificationIssueCode,
            disposition: VerificationDisposition,
            *,
            requirement_id: str | None = None,
            claim_id: str | None = None,
            answer_span_id: str | None = None,
            evidence_refs: tuple[str, ...] = (),
            conflict_set_id: str | None = None,
        ) -> None:
            issue_keys.add(
                (
                    code,
                    disposition,
                    requirement_id,
                    claim_id,
                    answer_span_id,
                    tuple(sorted(set(evidence_refs))),
                    conflict_set_id,
                )
            )

        cls._verify_profiles(envelope, requirements, facts, add)
        cls._verify_run_and_material_bindings(envelope, requirements, facts, add)

        span_by_id = cls._verify_spans(
            envelope,
            answer_character_count=facts.answer_material.character_count,
            add=add,
        )
        cls._verify_requirements(envelope, requirements, span_by_id, add)
        evidence_by_ref = cls._index_evidence(facts, add)
        cls._verify_claims(
            envelope,
            span_by_id,
            evidence_by_ref,
            checked_at=facts.checked_at,
            add=add,
        )
        cls._verify_requirement_evidence(
            envelope,
            evidence_by_ref,
            checked_at=facts.checked_at,
            add=add,
        )
        cls._verify_conflicts(envelope, facts, span_by_id, evidence_by_ref, add)

        for finding in facts.secret_leak_findings:
            add(
                VerificationIssueCode.SECRET_LEAK_DETECTED,
                VerificationDisposition.BLOCK,
                answer_span_id=finding.answer_span_id,
            )

        issues = cls._materialize_issues(issue_keys)
        failures = tuple(
            issue
            for issue in issues
            if issue.disposition is not VerificationDisposition.WARN
        )
        warnings = tuple(
            issue
            for issue in issues
            if issue.disposition is VerificationDisposition.WARN
        )
        status = cls._status(failures)
        unique_claim_ids = {claim.claim_id for claim in envelope.claims}
        claim_failure_ids = {
            issue.claim_id for issue in failures if issue.claim_id is not None
        }
        envelope_digest = _digest(envelope)
        requirement_digest = _digest(requirements)
        evidence_digest = _digest(facts)
        report_seed = "|".join(
            (
                envelope_digest,
                requirement_digest,
                evidence_digest,
                status,
                ",".join(issue.issue_id for issue in issues),
            )
        )
        return AnswerVerificationReport(
            report_id=f"avr_{hashlib.sha256(report_seed.encode()).hexdigest()[:24]}",
            envelope_digest=envelope_digest,
            requirement_ledger_digest=requirement_digest,
            evidence_snapshot_digest=evidence_digest,
            profile_revision=facts.profile_revision,
            status=status,
            failures=failures,
            warnings=warnings,
            declared_claim_count=len(envelope.claims),
            unique_claim_count=len(unique_claim_ids),
            verified_claim_count=len(unique_claim_ids - claim_failure_ids),
            unsupported_claim_count=len(
                {
                    issue.claim_id
                    for issue in failures
                    if issue.code is VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED
                    and issue.claim_id is not None
                }
            ),
            citation_error_count=sum(
                issue.code in cls._CITATION_CODES for issue in failures
            ),
            freshness_error_count=sum(
                issue.code in cls._FRESHNESS_CODES for issue in failures
            ),
            conflict_error_count=sum(
                issue.code in cls._CONFLICT_CODES for issue in failures
            ),
            checked_at=facts.checked_at,
        )

    @staticmethod
    def _verify_profiles(
        envelope: AnswerEnvelope,
        requirements: AnswerRequirementLedger,
        facts: AnswerVerificationFacts,
        add: object,
    ) -> None:
        if not (
            envelope.profile_revision
            == requirements.profile_revision
            == facts.profile_revision
        ):
            add(  # type: ignore[operator]
                VerificationIssueCode.PROFILE_REVISION_MISMATCH,
                VerificationDisposition.BLOCK,
            )

    @staticmethod
    def _verify_run_and_material_bindings(
        envelope: AnswerEnvelope,
        requirements: AnswerRequirementLedger,
        facts: AnswerVerificationFacts,
        add: object,
    ) -> None:
        if not (envelope.run_id == requirements.run_id == facts.run_id):
            add(  # type: ignore[operator]
                VerificationIssueCode.RUN_BINDING_MISMATCH,
                VerificationDisposition.BLOCK,
            )
        if envelope.requirement_ledger_id != requirements.ledger_id:
            add(  # type: ignore[operator]
                VerificationIssueCode.REQUIREMENT_LEDGER_MISMATCH,
                VerificationDisposition.BLOCK,
            )
        material = facts.answer_material
        declared = envelope.answer_content
        if (
            material.run_id != facts.run_id
            or material.content_ref != declared.content_ref
            or material.content_digest != declared.content_digest
            or material.size_bytes != declared.size_bytes
            or material.character_count != declared.character_count
            or material.resolved_at > facts.checked_at
        ):
            add(  # type: ignore[operator]
                VerificationIssueCode.ANSWER_MATERIAL_MISMATCH,
                VerificationDisposition.BLOCK,
            )
        if material.size_bytes > _MAX_ANSWER_BYTES:
            add(  # type: ignore[operator]
                VerificationIssueCode.ANSWER_TOO_LARGE,
                VerificationDisposition.REPAIR,
            )

    @staticmethod
    def _verify_spans(
        envelope: AnswerEnvelope,
        *,
        answer_character_count: int,
        add: object,
    ) -> dict[str, AnswerSpan]:
        counts = Counter(span.span_id for span in envelope.spans)
        for span_id, count in counts.items():
            if count > 1:
                add(  # type: ignore[operator]
                    VerificationIssueCode.DUPLICATE_SPAN,
                    VerificationDisposition.REPAIR,
                    answer_span_id=span_id,
                )
        span_by_id: dict[str, AnswerSpan] = {}
        for span in envelope.spans:
            span_by_id.setdefault(span.span_id, span)
            if span.end > answer_character_count:
                add(  # type: ignore[operator]
                    VerificationIssueCode.SPAN_OUT_OF_BOUNDS,
                    VerificationDisposition.REPAIR,
                    answer_span_id=span.span_id,
                )
        return span_by_id

    @staticmethod
    def _verify_requirements(
        envelope: AnswerEnvelope,
        ledger: AnswerRequirementLedger,
        span_by_id: dict[str, AnswerSpan],
        add: object,
    ) -> None:
        requirement_counts = Counter(
            item.requirement_id for item in ledger.requirements
        )
        result_counts = Counter(
            item.requirement_id for item in envelope.requirement_results
        )
        for requirement_id, count in requirement_counts.items():
            if count > 1:
                add(  # type: ignore[operator]
                    VerificationIssueCode.DUPLICATE_REQUIREMENT,
                    VerificationDisposition.BLOCK,
                    requirement_id=requirement_id,
                )
        for requirement_id, count in result_counts.items():
            if count > 1:
                add(  # type: ignore[operator]
                    VerificationIssueCode.DUPLICATE_REQUIREMENT_RESULT,
                    VerificationDisposition.REPAIR,
                    requirement_id=requirement_id,
                )

        known = set(requirement_counts)
        requirement_by_id = {
            item.requirement_id: item
            for item in ledger.requirements
            if requirement_counts[item.requirement_id] == 1
        }
        result_by_id: dict[str, AnswerRequirementResult] = {}
        for result in envelope.requirement_results:
            result_by_id.setdefault(result.requirement_id, result)
            if result.requirement_id not in known:
                add(  # type: ignore[operator]
                    VerificationIssueCode.UNKNOWN_REQUIREMENT_RESULT,
                    VerificationDisposition.WARN,
                    requirement_id=result.requirement_id,
                )
            requirement = requirement_by_id.get(result.requirement_id)
            if result.completion_run_id != ledger.run_id:
                add(  # type: ignore[operator]
                    VerificationIssueCode.REQUIREMENT_RUN_MISMATCH,
                    VerificationDisposition.BLOCK,
                    requirement_id=result.requirement_id,
                )
            if requirement is not None and (
                result.completion_source != requirement.completion_source
                or result.completion_source_digest
                != requirement.completion_source_digest
            ):
                add(  # type: ignore[operator]
                    VerificationIssueCode.REQUIREMENT_SOURCE_MISMATCH,
                    VerificationDisposition.BLOCK,
                    requirement_id=result.requirement_id,
                )
            if (
                result.status is RequirementStatus.SATISFIED
                and not result.answer_span_ids
                and not result.evidence_refs
            ):
                add(  # type: ignore[operator]
                    VerificationIssueCode.REQUIREMENT_SUPPORT_MISSING,
                    VerificationDisposition.REPAIR,
                    requirement_id=result.requirement_id,
                )
            for span_id in result.answer_span_ids:
                if span_id not in span_by_id:
                    add(  # type: ignore[operator]
                        VerificationIssueCode.UNKNOWN_SPAN,
                        VerificationDisposition.REPAIR,
                        requirement_id=result.requirement_id,
                        answer_span_id=span_id,
                    )

        for requirement in ledger.requirements:
            result = result_by_id.get(requirement.requirement_id)
            if result is None:
                if requirement.required:
                    add(  # type: ignore[operator]
                        VerificationIssueCode.MISSING_REQUIRED_REQUIREMENT_RESULT,
                        VerificationDisposition.REPAIR,
                        requirement_id=requirement.requirement_id,
                    )
                continue
            if (
                requirement.required
                and result.status is not RequirementStatus.SATISFIED
            ):
                add(  # type: ignore[operator]
                    VerificationIssueCode.REQUIRED_REQUIREMENT_INCOMPLETE,
                    VerificationDisposition.REPAIR,
                    requirement_id=requirement.requirement_id,
                )

    @staticmethod
    def _index_evidence(
        facts: AnswerVerificationFacts,
        add: object,
    ) -> dict[str, EvidenceVerificationFact]:
        counts = Counter(item.evidence.evidence_ref for item in facts.evidence)
        for evidence_ref, count in counts.items():
            if count > 1:
                add(  # type: ignore[operator]
                    VerificationIssueCode.DUPLICATE_EVIDENCE_FACT,
                    VerificationDisposition.BLOCK,
                    evidence_refs=(evidence_ref,),
                )
        indexed: dict[str, EvidenceVerificationFact] = {}
        for fact in facts.evidence:
            if counts[fact.evidence.evidence_ref] == 1:
                indexed[fact.evidence.evidence_ref] = fact
        return indexed

    @classmethod
    def _verify_claims(
        cls,
        envelope: AnswerEnvelope,
        span_by_id: dict[str, AnswerSpan],
        evidence_by_ref: dict[str, EvidenceVerificationFact],
        *,
        checked_at: datetime,
        add: object,
    ) -> None:
        claim_counts = Counter(claim.claim_id for claim in envelope.claims)
        for claim_id, count in claim_counts.items():
            if count > 1:
                add(  # type: ignore[operator]
                    VerificationIssueCode.DUPLICATE_CLAIM,
                    VerificationDisposition.REPAIR,
                    claim_id=claim_id,
                )
        for claim in envelope.claims:
            if claim.answer_span_id not in span_by_id:
                add(  # type: ignore[operator]
                    VerificationIssueCode.UNKNOWN_SPAN,
                    VerificationDisposition.REPAIR,
                    claim_id=claim.claim_id,
                    answer_span_id=claim.answer_span_id,
                )
            usable_support: list[EvidenceVerificationFact] = []
            for binding in claim.evidence_bindings:
                usable = cls._verify_binding(
                    binding=binding,
                    claim=claim,
                    fact=evidence_by_ref.get(binding.evidence.evidence_ref),
                    checked_at=checked_at,
                    add=add,
                )
                if (
                    binding.relationship is EvidenceRelationship.SUPPORTS
                    and usable is not None
                    and cls._verify_claim_source_compatibility(
                        claim=claim,
                        fact=usable,
                        add=add,
                    )
                ):
                    usable_support.append(usable)
            requires_support = (
                claim.materiality is ClaimMateriality.MATERIAL
                and claim.kind in cls._CLAIMS_REQUIRING_SUPPORT
            )
            if requires_support and not usable_support:
                add(  # type: ignore[operator]
                    VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED,
                    VerificationDisposition.REPAIR,
                    claim_id=claim.claim_id,
                    answer_span_id=claim.answer_span_id,
                )
            elif usable_support and claim.confidence > max(
                fact.max_supported_confidence for fact in usable_support
            ):
                add(  # type: ignore[operator]
                    VerificationIssueCode.CLAIM_CONFIDENCE_UNSUPPORTED,
                    VerificationDisposition.REPAIR,
                    claim_id=claim.claim_id,
                    answer_span_id=claim.answer_span_id,
                    evidence_refs=tuple(
                        fact.evidence.evidence_ref for fact in usable_support
                    ),
                )

    @classmethod
    def _verify_binding(
        cls,
        *,
        binding: AnswerEvidenceBinding,
        claim: AnswerClaim,
        fact: EvidenceVerificationFact | None,
        checked_at: datetime,
        add: object,
    ) -> EvidenceVerificationFact | None:
        evidence_ref = binding.evidence.evidence_ref
        if fact is None:
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_FACT_MISSING,
                VerificationDisposition.DEGRADE,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
            return None
        usable = fact.access_state is EvidenceAccessState.AUTHORIZED
        cls._verify_access(fact, claim_id=claim.claim_id, add=add)
        if binding.source_digest != fact.source_digest:
            usable = False
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_DIGEST_MISMATCH,
                VerificationDisposition.BLOCK,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
        if (
            binding.evidence.citation is not None
            and binding.evidence.citation != fact.evidence.citation
        ):
            usable = False
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_CITATION_MISMATCH,
                VerificationDisposition.REPAIR,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
        if fact.locator_state is EvidenceLocatorState.INVALID or (
            binding.locator_ref is not None and binding.locator_ref != fact.locator_ref
        ):
            usable = False
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_LOCATOR_INVALID,
                VerificationDisposition.REPAIR,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
        elif fact.locator_state is EvidenceLocatorState.UNKNOWN:
            usable = False
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_LOCATOR_UNKNOWN,
                VerificationDisposition.DEGRADE,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
        if not cls._verify_fact_validity(
            fact,
            checked_at=checked_at,
            claim_id=claim.claim_id,
            add=add,
        ):
            usable = False
        if claim.freshness_required:
            if fact.freshness_state is EvidenceFreshnessState.STALE:
                usable = False
                add(  # type: ignore[operator]
                    VerificationIssueCode.EVIDENCE_STALE,
                    VerificationDisposition.REPAIR,
                    claim_id=claim.claim_id,
                    evidence_refs=(evidence_ref,),
                )
            elif fact.freshness_state in {
                EvidenceFreshnessState.UNKNOWN,
                EvidenceFreshnessState.NOT_REQUIRED,
            }:
                usable = False
                add(  # type: ignore[operator]
                    VerificationIssueCode.EVIDENCE_FRESHNESS_UNKNOWN,
                    VerificationDisposition.DEGRADE,
                    claim_id=claim.claim_id,
                    evidence_refs=(evidence_ref,),
                )
        return fact if usable else None

    @classmethod
    def _verify_claim_source_compatibility(
        cls,
        *,
        claim: AnswerClaim,
        fact: EvidenceVerificationFact,
        add: object,
    ) -> bool:
        compatible = True
        if fact.source_class not in cls._CLAIM_SOURCE_COMPATIBILITY[claim.kind]:
            compatible = False
            add(  # type: ignore[operator]
                VerificationIssueCode.CLAIM_SOURCE_INCOMPATIBLE,
                VerificationDisposition.REPAIR,
                claim_id=claim.claim_id,
                answer_span_id=claim.answer_span_id,
                evidence_refs=(fact.evidence.evidence_ref,),
            )
        if (
            claim.materiality is ClaimMateriality.MATERIAL
            and fact.source_class is EvidenceSourceClass.SEARCH_SNIPPET
        ):
            compatible = False
            add(  # type: ignore[operator]
                VerificationIssueCode.CLAIM_SOURCE_INCOMPATIBLE,
                VerificationDisposition.REPAIR,
                claim_id=claim.claim_id,
                answer_span_id=claim.answer_span_id,
                evidence_refs=(fact.evidence.evidence_ref,),
            )
        if (
            claim.materiality is ClaimMateriality.MATERIAL
            and fact.trust_class not in cls._MATERIAL_TRUST_COMPATIBILITY[claim.kind]
        ):
            compatible = False
            add(  # type: ignore[operator]
                VerificationIssueCode.CLAIM_TRUST_INSUFFICIENT,
                VerificationDisposition.REPAIR,
                claim_id=claim.claim_id,
                answer_span_id=claim.answer_span_id,
                evidence_refs=(fact.evidence.evidence_ref,),
            )
        return compatible

    @classmethod
    def _verify_requirement_evidence(
        cls,
        envelope: AnswerEnvelope,
        evidence_by_ref: dict[str, EvidenceVerificationFact],
        *,
        checked_at: datetime,
        add: object,
    ) -> None:
        for result in envelope.requirement_results:
            for evidence in result.evidence_refs:
                fact = evidence_by_ref.get(evidence.evidence_ref)
                if fact is None:
                    add(  # type: ignore[operator]
                        VerificationIssueCode.EVIDENCE_FACT_MISSING,
                        VerificationDisposition.DEGRADE,
                        requirement_id=result.requirement_id,
                        evidence_refs=(evidence.evidence_ref,),
                    )
                else:
                    cls._verify_access(
                        fact,
                        requirement_id=result.requirement_id,
                        add=add,
                    )
                    cls._verify_fact_validity(
                        fact,
                        checked_at=checked_at,
                        requirement_id=result.requirement_id,
                        add=add,
                    )

    @staticmethod
    def _verify_fact_validity(
        fact: EvidenceVerificationFact,
        *,
        checked_at: datetime,
        add: object,
        requirement_id: str | None = None,
        claim_id: str | None = None,
    ) -> bool:
        valid = True
        if fact.observed_at > checked_at:
            valid = False
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_OBSERVATION_IN_FUTURE,
                VerificationDisposition.BLOCK,
                requirement_id=requirement_id,
                claim_id=claim_id,
                evidence_refs=(fact.evidence.evidence_ref,),
            )
        if checked_at < fact.valid_from:
            valid = False
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_NOT_YET_VALID,
                VerificationDisposition.BLOCK,
                requirement_id=requirement_id,
                claim_id=claim_id,
                evidence_refs=(fact.evidence.evidence_ref,),
            )
        if fact.valid_until is not None and checked_at > fact.valid_until:
            valid = False
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_VALIDITY_EXPIRED,
                VerificationDisposition.DEGRADE,
                requirement_id=requirement_id,
                claim_id=claim_id,
                evidence_refs=(fact.evidence.evidence_ref,),
            )
        return valid

    @staticmethod
    def _verify_access(
        fact: EvidenceVerificationFact,
        *,
        add: object,
        requirement_id: str | None = None,
        claim_id: str | None = None,
    ) -> None:
        mapping = {
            EvidenceAccessState.UNAUTHORIZED: (
                VerificationIssueCode.EVIDENCE_UNAUTHORIZED,
                VerificationDisposition.BLOCK,
            ),
            EvidenceAccessState.REVOKED: (
                VerificationIssueCode.EVIDENCE_REVOKED,
                VerificationDisposition.BLOCK,
            ),
            EvidenceAccessState.NOT_FOUND: (
                VerificationIssueCode.EVIDENCE_NOT_FOUND,
                VerificationDisposition.DEGRADE,
            ),
            EvidenceAccessState.EXPIRED: (
                VerificationIssueCode.EVIDENCE_EXPIRED,
                VerificationDisposition.DEGRADE,
            ),
            EvidenceAccessState.UNAVAILABLE: (
                VerificationIssueCode.EVIDENCE_UNAVAILABLE,
                VerificationDisposition.DEGRADE,
            ),
        }
        finding = mapping.get(fact.access_state)
        if finding is not None:
            add(  # type: ignore[operator]
                finding[0],
                finding[1],
                requirement_id=requirement_id,
                claim_id=claim_id,
                evidence_refs=(fact.evidence.evidence_ref,),
            )

    @staticmethod
    def _verify_conflicts(
        envelope: AnswerEnvelope,
        facts: AnswerVerificationFacts,
        span_by_id: dict[str, AnswerSpan],
        evidence_by_ref: dict[str, EvidenceVerificationFact],
        add: object,
    ) -> None:
        fact_counts = Counter(item.conflict_set_id for item in facts.conflicts)
        resolution_counts = Counter(
            item.conflict_set_id for item in envelope.conflict_resolutions
        )
        for conflict_set_id, count in fact_counts.items():
            if count > 1:
                add(  # type: ignore[operator]
                    VerificationIssueCode.DUPLICATE_CONFLICT_FACT,
                    VerificationDisposition.BLOCK,
                    conflict_set_id=conflict_set_id,
                )
        for conflict_set_id, count in resolution_counts.items():
            if count > 1:
                add(  # type: ignore[operator]
                    VerificationIssueCode.DUPLICATE_CONFLICT_RESOLUTION,
                    VerificationDisposition.REPAIR,
                    conflict_set_id=conflict_set_id,
                )
        facts_by_id = {
            item.conflict_set_id: item
            for item in facts.conflicts
            if fact_counts[item.conflict_set_id] == 1
        }
        resolutions_by_id: dict[str, AnswerConflictResolution] = {}
        for resolution in envelope.conflict_resolutions:
            resolutions_by_id.setdefault(resolution.conflict_set_id, resolution)
            if resolution.conflict_set_id not in facts_by_id:
                add(  # type: ignore[operator]
                    VerificationIssueCode.UNKNOWN_CONFLICT_RESOLUTION,
                    VerificationDisposition.WARN,
                    conflict_set_id=resolution.conflict_set_id,
                )
            for span_id in resolution.answer_span_ids:
                if span_id not in span_by_id:
                    add(  # type: ignore[operator]
                        VerificationIssueCode.UNKNOWN_SPAN,
                        VerificationDisposition.REPAIR,
                        answer_span_id=span_id,
                        conflict_set_id=resolution.conflict_set_id,
                    )
            for evidence in resolution.evidence_refs:
                fact = evidence_by_ref.get(evidence.evidence_ref)
                if fact is None:
                    add(  # type: ignore[operator]
                        VerificationIssueCode.EVIDENCE_FACT_MISSING,
                        VerificationDisposition.DEGRADE,
                        evidence_refs=(evidence.evidence_ref,),
                        conflict_set_id=resolution.conflict_set_id,
                    )
                else:
                    AnswerVerifier._verify_access(fact, add=add)
                    AnswerVerifier._verify_fact_validity(
                        fact,
                        checked_at=facts.checked_at,
                        add=add,
                    )

        for conflict in facts_by_id.values():
            if not conflict.resolution_required:
                continue
            resolution = resolutions_by_id.get(conflict.conflict_set_id)
            if resolution is None:
                add(  # type: ignore[operator]
                    VerificationIssueCode.CONFLICT_RESOLUTION_MISSING,
                    VerificationDisposition.REPAIR,
                    conflict_set_id=conflict.conflict_set_id,
                    evidence_refs=tuple(
                        item.evidence_ref for item in conflict.evidence_refs
                    ),
                )
                continue
            expected = {item.evidence_ref for item in conflict.evidence_refs}
            actual = {item.evidence_ref for item in resolution.evidence_refs}
            if expected != actual or (
                conflict.material
                and resolution.resolution is ConflictResolutionKind.NOT_MATERIAL
            ):
                add(  # type: ignore[operator]
                    VerificationIssueCode.CONFLICT_RESOLUTION_INVALID,
                    VerificationDisposition.REPAIR,
                    conflict_set_id=conflict.conflict_set_id,
                    evidence_refs=tuple(sorted(expected | actual)),
                )

    @staticmethod
    def _materialize_issues(
        issue_keys: set[_IssueKey],
    ) -> tuple[VerificationIssue, ...]:
        ordered = sorted(
            issue_keys,
            key=lambda item: tuple(
                "" if value is None else str(value) for value in item
            ),
        )
        issues: list[VerificationIssue] = []
        for key in ordered:
            serialized = json.dumps(
                [
                    key[0],
                    key[1],
                    key[2],
                    key[3],
                    key[4],
                    key[5],
                    key[6],
                ],
                separators=(",", ":"),
            )
            issues.append(
                VerificationIssue(
                    issue_id=f"avi_{hashlib.sha256(serialized.encode()).hexdigest()[:24]}",
                    code=key[0],
                    disposition=key[1],
                    requirement_id=key[2],
                    claim_id=key[3],
                    answer_span_id=key[4],
                    evidence_refs=key[5],
                    conflict_set_id=key[6],
                )
            )
        return tuple(issues)

    @staticmethod
    def _status(failures: tuple[VerificationIssue, ...]) -> VerificationStatus:
        dispositions = {issue.disposition for issue in failures}
        if VerificationDisposition.BLOCK in dispositions:
            return VerificationStatus.BLOCKED
        if VerificationDisposition.REPAIR in dispositions:
            return VerificationStatus.REPAIRABLE
        if VerificationDisposition.DEGRADE in dispositions:
            return VerificationStatus.DEGRADED
        return VerificationStatus.PASSED


def _digest(contract: RuntimeContract) -> str:
    payload = json.dumps(
        contract.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = (
    "AnswerClaim",
    "AnswerConflictResolution",
    "AnswerEnvelope",
    "AnswerEvidenceBinding",
    "AnswerRequirement",
    "AnswerRequirementLedger",
    "AnswerRequirementResult",
    "AnswerSpan",
    "AnswerMaterialVerificationFact",
    "AnswerVerificationFacts",
    "AnswerVerificationReport",
    "AnswerVerifier",
    "CitationIdentity",
    "ClaimKind",
    "ClaimMateriality",
    "ConflictResolutionKind",
    "EvidenceAccessState",
    "EvidenceConflictFact",
    "EvidenceFreshnessState",
    "EvidenceLocatorState",
    "EvidenceReference",
    "EvidenceRelationship",
    "EvidenceSourceClass",
    "EvidenceTrustClass",
    "EvidenceVerificationFact",
    "ProtectedAnswerContent",
    "RequirementCompletionSource",
    "RequirementStatus",
    "SecretLeakFinding",
    "VerificationDisposition",
    "VerificationIssue",
    "VerificationIssueCode",
    "VerificationStatus",
)
