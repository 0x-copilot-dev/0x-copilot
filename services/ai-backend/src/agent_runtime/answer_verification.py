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
    PROFILE_REVISION_MISMATCH = "profile_revision_mismatch"
    DUPLICATE_REQUIREMENT = "duplicate_requirement"
    DUPLICATE_REQUIREMENT_RESULT = "duplicate_requirement_result"
    UNKNOWN_REQUIREMENT_RESULT = "unknown_requirement_result"
    MISSING_REQUIRED_REQUIREMENT_RESULT = "missing_required_requirement_result"
    REQUIRED_REQUIREMENT_INCOMPLETE = "required_requirement_incomplete"
    REQUIREMENT_SUPPORT_MISSING = "requirement_support_missing"
    DUPLICATE_SPAN = "duplicate_span"
    SPAN_OUT_OF_BOUNDS = "span_out_of_bounds"
    UNKNOWN_SPAN = "unknown_span"
    DUPLICATE_CLAIM = "duplicate_claim"
    MATERIAL_CLAIM_UNSUPPORTED = "material_claim_unsupported"
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
    required: bool = True


class AnswerRequirementLedger(RuntimeContract):
    """Bounded requirement inventory compiled before answer synthesis."""

    ledger_id: str = Field(min_length=1, max_length=128)
    profile_revision: str = Field(min_length=1, max_length=128)
    source_request_digest: str = Field(pattern=_SHA256_PATTERN)
    requirements: tuple[AnswerRequirement, ...] = Field(max_length=50)


class AnswerSpan(RuntimeContract):
    """Character offsets into ``AnswerEnvelope.answer_text``."""

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


class AnswerEnvelope(RuntimeContract):
    """Versioned final-answer candidate produced by synthesis."""

    envelope_revision: str = Field(min_length=1, max_length=128)
    profile_revision: str = Field(min_length=1, max_length=128)
    answer_text: str = Field(max_length=262_144)
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
    access_state: EvidenceAccessState
    freshness_state: EvidenceFreshnessState
    locator_state: EvidenceLocatorState
    locator_ref: str | None = Field(default=None, max_length=512)


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

    profile_revision: str = Field(min_length=1, max_length=128)
    checked_at: datetime
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
        }
    )
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
        if len(envelope.answer_text.encode("utf-8")) > _MAX_ANSWER_BYTES:
            add(
                VerificationIssueCode.ANSWER_TOO_LARGE,
                VerificationDisposition.REPAIR,
            )

        span_by_id = cls._verify_spans(envelope, add)
        cls._verify_requirements(envelope, requirements, span_by_id, add)
        evidence_by_ref = cls._index_evidence(facts, add)
        cls._verify_claims(envelope, span_by_id, evidence_by_ref, add)
        cls._verify_requirement_evidence(envelope, evidence_by_ref, add)
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
            verified_claim_count=len(envelope.claims) - len(claim_failure_ids),
            unsupported_claim_count=sum(
                issue.code is VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED
                for issue in failures
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
    def _verify_spans(envelope: AnswerEnvelope, add: object) -> dict[str, AnswerSpan]:
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
            if span.end > len(envelope.answer_text):
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
        result_by_id: dict[str, AnswerRequirementResult] = {}
        for result in envelope.requirement_results:
            result_by_id.setdefault(result.requirement_id, result)
            if result.requirement_id not in known:
                add(  # type: ignore[operator]
                    VerificationIssueCode.UNKNOWN_REQUIREMENT_RESULT,
                    VerificationDisposition.WARN,
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
            supporting = tuple(
                binding
                for binding in claim.evidence_bindings
                if binding.relationship is EvidenceRelationship.SUPPORTS
            )
            if (
                claim.materiality is ClaimMateriality.MATERIAL
                and claim.kind in cls._CLAIMS_REQUIRING_SUPPORT
                and not supporting
            ):
                add(  # type: ignore[operator]
                    VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED,
                    VerificationDisposition.REPAIR,
                    claim_id=claim.claim_id,
                    answer_span_id=claim.answer_span_id,
                )
            for binding in claim.evidence_bindings:
                cls._verify_binding(
                    binding=binding,
                    claim=claim,
                    fact=evidence_by_ref.get(binding.evidence.evidence_ref),
                    add=add,
                )

    @classmethod
    def _verify_binding(
        cls,
        *,
        binding: AnswerEvidenceBinding,
        claim: AnswerClaim,
        fact: EvidenceVerificationFact | None,
        add: object,
    ) -> None:
        evidence_ref = binding.evidence.evidence_ref
        if fact is None:
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_FACT_MISSING,
                VerificationDisposition.DEGRADE,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
            return
        cls._verify_access(fact, claim_id=claim.claim_id, add=add)
        if binding.source_digest != fact.source_digest:
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
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_CITATION_MISMATCH,
                VerificationDisposition.REPAIR,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
        if fact.locator_state is EvidenceLocatorState.INVALID or (
            binding.locator_ref is not None and binding.locator_ref != fact.locator_ref
        ):
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_LOCATOR_INVALID,
                VerificationDisposition.REPAIR,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
        elif fact.locator_state is EvidenceLocatorState.UNKNOWN:
            add(  # type: ignore[operator]
                VerificationIssueCode.EVIDENCE_LOCATOR_UNKNOWN,
                VerificationDisposition.DEGRADE,
                claim_id=claim.claim_id,
                evidence_refs=(evidence_ref,),
            )
        if claim.freshness_required:
            if fact.freshness_state is EvidenceFreshnessState.STALE:
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
                add(  # type: ignore[operator]
                    VerificationIssueCode.EVIDENCE_FRESHNESS_UNKNOWN,
                    VerificationDisposition.DEGRADE,
                    claim_id=claim.claim_id,
                    evidence_refs=(evidence_ref,),
                )

    @classmethod
    def _verify_requirement_evidence(
        cls,
        envelope: AnswerEnvelope,
        evidence_by_ref: dict[str, EvidenceVerificationFact],
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
    "EvidenceVerificationFact",
    "RequirementStatus",
    "SecretLeakFinding",
    "VerificationDisposition",
    "VerificationIssue",
    "VerificationIssueCode",
    "VerificationStatus",
)
