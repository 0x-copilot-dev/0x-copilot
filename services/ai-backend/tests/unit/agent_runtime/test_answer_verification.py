"""Focused tests for deterministic, content-safe answer verification."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.answer_verification import (
    AnswerClaim,
    AnswerConflictResolution,
    AnswerEnvelope,
    AnswerEvidenceBinding,
    AnswerRequirement,
    AnswerRequirementLedger,
    AnswerRequirementResult,
    AnswerSpan,
    AnswerVerificationFacts,
    AnswerVerifier,
    CitationIdentity,
    ClaimKind,
    ClaimMateriality,
    ConflictResolutionKind,
    EvidenceAccessState,
    EvidenceConflictFact,
    EvidenceFreshnessState,
    EvidenceLocatorState,
    EvidenceReference,
    EvidenceRelationship,
    EvidenceVerificationFact,
    RequirementStatus,
    SecretLeakFinding,
    VerificationIssueCode,
    VerificationStatus,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_CHECKED_AT = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _reference(name: str = "ev_1", ordinal: int = 1) -> EvidenceReference:
    return EvidenceReference(
        evidence_ref=name,
        citation=CitationIdentity(citation_id=f"c{ordinal}", ordinal=ordinal),
    )


def _ledger() -> AnswerRequirementLedger:
    return AnswerRequirementLedger(
        ledger_id="ledger_1",
        profile_revision="profile_1",
        source_request_digest="d" * 64,
        requirements=(
            AnswerRequirement(
                requirement_id="req_summary",
                description_ref="payload://requirements/summary",
            ),
        ),
    )


def _envelope(
    *,
    evidence: EvidenceReference | None = None,
    digest: str = _DIGEST_A,
    freshness_required: bool = False,
) -> AnswerEnvelope:
    reference = evidence or _reference()
    return AnswerEnvelope(
        envelope_revision="answer_v1",
        profile_revision="profile_1",
        answer_text="The supported answer.",
        spans=(AnswerSpan(span_id="span_1", start=0, end=20),),
        requirement_results=(
            AnswerRequirementResult(
                requirement_id="req_summary",
                status=RequirementStatus.SATISFIED,
                answer_span_ids=("span_1",),
                evidence_refs=(reference,),
            ),
        ),
        claims=(
            AnswerClaim(
                claim_id="claim_1",
                answer_span_id="span_1",
                kind=ClaimKind.OBSERVED,
                materiality=ClaimMateriality.MATERIAL,
                evidence_bindings=(
                    AnswerEvidenceBinding(
                        evidence=reference,
                        source_digest=digest,
                        locator_ref="locator_1",
                        relationship=EvidenceRelationship.SUPPORTS,
                    ),
                ),
                freshness_required=freshness_required,
            ),
        ),
    )


def _facts(
    *,
    evidence: EvidenceReference | None = None,
    digest: str = _DIGEST_A,
    access: EvidenceAccessState = EvidenceAccessState.AUTHORIZED,
    freshness: EvidenceFreshnessState = EvidenceFreshnessState.CURRENT,
    locator: EvidenceLocatorState = EvidenceLocatorState.VALID,
    additional_evidence: tuple[EvidenceVerificationFact, ...] = (),
    conflicts: tuple[EvidenceConflictFact, ...] = (),
    secrets: tuple[SecretLeakFinding, ...] = (),
) -> AnswerVerificationFacts:
    reference = evidence or _reference()
    return AnswerVerificationFacts(
        profile_revision="profile_1",
        checked_at=_CHECKED_AT,
        evidence=(
            EvidenceVerificationFact(
                evidence=reference,
                source_digest=digest,
                access_state=access,
                freshness_state=freshness,
                locator_state=locator,
                locator_ref="locator_1",
            ),
            *additional_evidence,
        ),
        conflicts=conflicts,
        secret_leak_findings=secrets,
    )


def test_valid_grounded_answer_passes_and_is_deterministic() -> None:
    envelope = _envelope()
    facts = _facts()

    first = AnswerVerifier.verify(
        envelope=envelope,
        requirements=_ledger(),
        facts=facts,
    )
    second = AnswerVerifier.verify(
        envelope=envelope,
        requirements=_ledger(),
        facts=facts,
    )

    assert first == second
    assert first.status is VerificationStatus.PASSED
    assert first.failures == ()
    assert first.verified_claim_count == 1
    serialized = first.model_dump_json()
    assert "The supported answer" not in serialized
    assert "locator_1" not in serialized


def test_missing_requirement_and_material_claim_support_are_repairable() -> None:
    envelope = AnswerEnvelope(
        envelope_revision="answer_v1",
        profile_revision="profile_1",
        answer_text="Unsupported.",
        spans=(AnswerSpan(span_id="span_1", start=0, end=12),),
        claims=(
            AnswerClaim(
                claim_id="claim_1",
                answer_span_id="span_1",
                kind=ClaimKind.OBSERVED,
                materiality=ClaimMateriality.MATERIAL,
            ),
        ),
    )

    report = AnswerVerifier.verify(
        envelope=envelope,
        requirements=_ledger(),
        facts=AnswerVerificationFacts(
            profile_revision="profile_1",
            checked_at=_CHECKED_AT,
        ),
    )

    assert report.status is VerificationStatus.REPAIRABLE
    assert {issue.code for issue in report.failures} == {
        VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED,
        VerificationIssueCode.MISSING_REQUIRED_REQUIREMENT_RESULT,
    }
    assert report.unsupported_claim_count == 1


def test_revoked_source_and_supplied_secret_finding_block_without_secret_value() -> (
    None
):
    report = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=_facts(
            access=EvidenceAccessState.REVOKED,
            secrets=(
                SecretLeakFinding(
                    finding_id="finding_1",
                    detector_code="provider_key",
                    answer_span_id="span_1",
                ),
            ),
        ),
    )

    assert report.status is VerificationStatus.BLOCKED
    assert {issue.code for issue in report.failures} >= {
        VerificationIssueCode.EVIDENCE_REVOKED,
        VerificationIssueCode.SECRET_LEAK_DETECTED,
    }
    assert "provider_key" not in report.model_dump_json()


def test_digest_locator_and_freshness_facts_are_not_trusted_from_envelope() -> None:
    report = AnswerVerifier.verify(
        envelope=_envelope(digest=_DIGEST_B, freshness_required=True),
        requirements=_ledger(),
        facts=_facts(
            digest=_DIGEST_A,
            freshness=EvidenceFreshnessState.STALE,
            locator=EvidenceLocatorState.INVALID,
        ),
    )

    assert report.status is VerificationStatus.BLOCKED
    assert {issue.code for issue in report.failures} >= {
        VerificationIssueCode.EVIDENCE_DIGEST_MISMATCH,
        VerificationIssueCode.EVIDENCE_LOCATOR_INVALID,
        VerificationIssueCode.EVIDENCE_STALE,
    }
    assert report.citation_error_count == 2
    assert report.freshness_error_count == 1


def test_required_conflict_must_be_declared_and_cover_exact_evidence_set() -> None:
    first = _reference("ev_1", 1)
    second = _reference("ev_2", 2)
    conflict = EvidenceConflictFact(
        conflict_set_id="conflict_1",
        evidence_refs=(first, second),
    )
    missing = AnswerVerifier.verify(
        envelope=_envelope(evidence=first),
        requirements=_ledger(),
        facts=_facts(evidence=first, conflicts=(conflict,)),
    )
    assert missing.status is VerificationStatus.REPAIRABLE
    assert VerificationIssueCode.CONFLICT_RESOLUTION_MISSING in {
        issue.code for issue in missing.failures
    }

    base = _envelope(evidence=first)
    resolved = base.model_copy(
        update={
            "conflict_resolutions": (
                AnswerConflictResolution(
                    conflict_set_id="conflict_1",
                    evidence_refs=(first, second),
                    resolution=ConflictResolutionKind.UNRESOLVED,
                    answer_span_ids=("span_1",),
                ),
            )
        }
    )
    second_fact = EvidenceVerificationFact(
        evidence=second,
        source_digest=_DIGEST_B,
        access_state=EvidenceAccessState.AUTHORIZED,
        freshness_state=EvidenceFreshnessState.CURRENT,
        locator_state=EvidenceLocatorState.NOT_REQUIRED,
    )
    passed = AnswerVerifier.verify(
        envelope=resolved,
        requirements=_ledger(),
        facts=_facts(
            evidence=first,
            additional_evidence=(second_fact,),
            conflicts=(conflict,),
        ),
    )
    assert passed.status is VerificationStatus.PASSED


def test_missing_evidence_fact_degrades_instead_of_claiming_verification() -> None:
    report = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=AnswerVerificationFacts(
            profile_revision="profile_1",
            checked_at=_CHECKED_AT,
        ),
    )

    assert report.status is VerificationStatus.DEGRADED
    assert report.verified_claim_count == 0
    assert {issue.code for issue in report.failures} == {
        VerificationIssueCode.EVIDENCE_FACT_MISSING
    }
