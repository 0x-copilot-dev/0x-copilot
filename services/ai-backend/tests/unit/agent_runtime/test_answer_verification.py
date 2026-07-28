"""Focused tests for deterministic, content-safe answer verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.answer_verification import (
    AnswerClaim,
    AnswerConflictResolution,
    AnswerEnvelope,
    AnswerEvidenceBinding,
    AnswerRequirement,
    AnswerRequirementLedger,
    AnswerRequirementResult,
    AnswerSpan,
    AnswerMaterialVerificationFact,
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
    EvidenceSourceClass,
    EvidenceTrustClass,
    EvidenceVerificationFact,
    ProtectedAnswerContent,
    RequirementCompletionSource,
    RequirementStatus,
    SecretLeakFinding,
    VerificationIssueCode,
    VerificationStatus,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_CHECKED_AT = datetime(2026, 7, 27, tzinfo=timezone.utc)
_RUN_ID = "run_1"
_REQUEST_DIGEST = "d" * 64
_ANSWER_DIGEST = "e" * 64
_ANSWER_TEXT = "The supported answer."


def _reference(name: str = "ev_1", ordinal: int = 1) -> EvidenceReference:
    return EvidenceReference(
        evidence_ref=name,
        citation=CitationIdentity(citation_id=f"c{ordinal}", ordinal=ordinal),
    )


def _ledger() -> AnswerRequirementLedger:
    return AnswerRequirementLedger(
        ledger_id="ledger_1",
        run_id=_RUN_ID,
        profile_revision="profile_1",
        source_request_digest=_REQUEST_DIGEST,
        requirements=(
            AnswerRequirement(
                requirement_id="req_summary",
                description_ref="payload://requirements/summary",
                completion_source=RequirementCompletionSource.EXPLICIT_REQUEST,
                completion_source_digest=_REQUEST_DIGEST,
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
        run_id=_RUN_ID,
        envelope_revision="answer_v1",
        profile_revision="profile_1",
        requirement_ledger_id="ledger_1",
        answer_content=ProtectedAnswerContent(
            content_ref="payload://answers/run_1/final",
            content_digest=_ANSWER_DIGEST,
            size_bytes=len(_ANSWER_TEXT.encode()),
            character_count=len(_ANSWER_TEXT),
        ),
        spans=(AnswerSpan(span_id="span_1", start=0, end=20),),
        requirement_results=(
            AnswerRequirementResult(
                requirement_id="req_summary",
                completion_run_id=_RUN_ID,
                completion_source=RequirementCompletionSource.EXPLICIT_REQUEST,
                completion_source_digest=_REQUEST_DIGEST,
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
                confidence=900,
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
    source_class: EvidenceSourceClass = EvidenceSourceClass.WEB_DOCUMENT,
    trust_class: EvidenceTrustClass = EvidenceTrustClass.PRIMARY,
    max_supported_confidence: int = 950,
    observed_at: datetime | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> AnswerVerificationFacts:
    reference = evidence or _reference()
    return AnswerVerificationFacts(
        run_id=_RUN_ID,
        profile_revision="profile_1",
        checked_at=_CHECKED_AT,
        answer_material=AnswerMaterialVerificationFact(
            run_id=_RUN_ID,
            content_ref="payload://answers/run_1/final",
            content_digest=_ANSWER_DIGEST,
            size_bytes=len(_ANSWER_TEXT.encode()),
            character_count=len(_ANSWER_TEXT),
            resolved_at=_CHECKED_AT,
        ),
        evidence=(
            EvidenceVerificationFact(
                evidence=reference,
                source_digest=digest,
                source_class=source_class,
                trust_class=trust_class,
                max_supported_confidence=max_supported_confidence,
                access_state=access,
                freshness_state=freshness,
                locator_state=locator,
                locator_ref="locator_1",
                observed_at=observed_at or (_CHECKED_AT - timedelta(minutes=5)),
                valid_from=valid_from or (_CHECKED_AT - timedelta(hours=1)),
                valid_until=valid_until,
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
    assert _ANSWER_TEXT not in envelope.model_dump_json()
    assert _ANSWER_TEXT not in serialized
    assert "locator_1" not in serialized


def test_missing_requirement_and_material_claim_support_are_repairable() -> None:
    envelope = AnswerEnvelope(
        run_id=_RUN_ID,
        envelope_revision="answer_v1",
        profile_revision="profile_1",
        requirement_ledger_id="ledger_1",
        answer_content=ProtectedAnswerContent(
            content_ref="payload://answers/run_1/final",
            content_digest=_ANSWER_DIGEST,
            size_bytes=len("Unsupported.".encode()),
            character_count=len("Unsupported."),
        ),
        spans=(AnswerSpan(span_id="span_1", start=0, end=12),),
        claims=(
            AnswerClaim(
                claim_id="claim_1",
                answer_span_id="span_1",
                kind=ClaimKind.OBSERVED,
                materiality=ClaimMateriality.MATERIAL,
                confidence=900,
            ),
        ),
    )

    report = AnswerVerifier.verify(
        envelope=envelope,
        requirements=_ledger(),
        facts=AnswerVerificationFacts(
            run_id=_RUN_ID,
            profile_revision="profile_1",
            checked_at=_CHECKED_AT,
            answer_material=AnswerMaterialVerificationFact(
                run_id=_RUN_ID,
                content_ref="payload://answers/run_1/final",
                content_digest=_ANSWER_DIGEST,
                size_bytes=len("Unsupported.".encode()),
                character_count=len("Unsupported."),
                resolved_at=_CHECKED_AT,
            ),
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
        source_class=EvidenceSourceClass.WEB_DOCUMENT,
        trust_class=EvidenceTrustClass.PRIMARY,
        max_supported_confidence=950,
        access_state=EvidenceAccessState.AUTHORIZED,
        freshness_state=EvidenceFreshnessState.CURRENT,
        locator_state=EvidenceLocatorState.NOT_REQUIRED,
        observed_at=_CHECKED_AT - timedelta(minutes=5),
        valid_from=_CHECKED_AT - timedelta(hours=1),
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


def test_missing_evidence_fact_cannot_count_as_material_claim_support() -> None:
    report = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=AnswerVerificationFacts(
            run_id=_RUN_ID,
            profile_revision="profile_1",
            checked_at=_CHECKED_AT,
            answer_material=AnswerMaterialVerificationFact(
                run_id=_RUN_ID,
                content_ref="payload://answers/run_1/final",
                content_digest=_ANSWER_DIGEST,
                size_bytes=len(_ANSWER_TEXT.encode()),
                character_count=len(_ANSWER_TEXT),
                resolved_at=_CHECKED_AT,
            ),
        ),
    )

    assert report.status is VerificationStatus.REPAIRABLE
    assert report.verified_claim_count == 0
    assert {issue.code for issue in report.failures} == {
        VerificationIssueCode.EVIDENCE_FACT_MISSING,
        VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED,
    }


def test_source_class_and_trust_are_resolver_owned_and_claim_compatible() -> None:
    report = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=_facts(
            source_class=EvidenceSourceClass.SEARCH_SNIPPET,
            trust_class=EvidenceTrustClass.UNVERIFIED,
        ),
    )

    assert report.status is VerificationStatus.REPAIRABLE
    assert {issue.code for issue in report.failures} >= {
        VerificationIssueCode.CLAIM_SOURCE_INCOMPATIBLE,
        VerificationIssueCode.CLAIM_TRUST_INSUFFICIENT,
        VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED,
    }

    binding = _envelope().claims[0].evidence_bindings[0].model_dump(mode="json")
    binding["source_class"] = EvidenceSourceClass.WEB_DOCUMENT.value
    with pytest.raises(ValidationError):
        AnswerEvidenceBinding.model_validate(binding)


def test_claim_confidence_cannot_exceed_resolved_evidence_confidence() -> None:
    report = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=_facts(max_supported_confidence=700),
    )

    assert report.status is VerificationStatus.REPAIRABLE
    assert report.verified_claim_count == 0
    assert {issue.code for issue in report.failures} == {
        VerificationIssueCode.CLAIM_CONFIDENCE_UNSUPPORTED
    }


def test_evidence_validity_window_and_observation_time_are_enforced() -> None:
    not_yet_valid = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=_facts(valid_from=_CHECKED_AT + timedelta(seconds=1)),
    )
    assert not_yet_valid.status is VerificationStatus.BLOCKED
    assert {issue.code for issue in not_yet_valid.failures} >= {
        VerificationIssueCode.EVIDENCE_NOT_YET_VALID,
        VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED,
    }

    expired = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=_facts(valid_until=_CHECKED_AT - timedelta(seconds=1)),
    )
    assert {issue.code for issue in expired.failures} >= {
        VerificationIssueCode.EVIDENCE_VALIDITY_EXPIRED,
        VerificationIssueCode.MATERIAL_CLAIM_UNSUPPORTED,
    }

    future_observation = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=_facts(observed_at=_CHECKED_AT + timedelta(seconds=1)),
    )
    assert future_observation.status is VerificationStatus.BLOCKED
    assert VerificationIssueCode.EVIDENCE_OBSERVATION_IN_FUTURE in {
        issue.code for issue in future_observation.failures
    }

    fact_payload = _facts().evidence[0].model_dump(mode="json")
    fact_payload["valid_from"] = "2026-07-27T00:00:00"
    with pytest.raises(ValidationError, match="timezone-aware"):
        EvidenceVerificationFact.model_validate(fact_payload)


def test_requirement_completion_is_bound_to_compiled_source_and_run() -> None:
    wrong_run_payload = _envelope().model_dump(mode="json")
    wrong_run_payload["requirement_results"][0]["completion_run_id"] = "run_other"
    wrong_run = AnswerVerifier.verify(
        envelope=AnswerEnvelope.model_validate(wrong_run_payload),
        requirements=_ledger(),
        facts=_facts(),
    )
    assert VerificationIssueCode.REQUIREMENT_RUN_MISMATCH in {
        issue.code for issue in wrong_run.failures
    }

    wrong_source_payload = _envelope().model_dump(mode="json")
    wrong_source_payload["requirement_results"][0]["completion_source"] = (
        RequirementCompletionSource.TASK_PLAN.value
    )
    wrong_source_payload["requirement_results"][0]["completion_source_digest"] = (
        _DIGEST_B
    )
    wrong_source = AnswerVerifier.verify(
        envelope=AnswerEnvelope.model_validate(wrong_source_payload),
        requirements=_ledger(),
        facts=_facts(),
    )
    assert VerificationIssueCode.REQUIREMENT_SOURCE_MISMATCH in {
        issue.code for issue in wrong_source.failures
    }

    wrong_envelope_run_payload = _envelope().model_dump(mode="json")
    wrong_envelope_run_payload["run_id"] = "run_other"
    wrong_envelope_run = AnswerVerifier.verify(
        envelope=AnswerEnvelope.model_validate(wrong_envelope_run_payload),
        requirements=_ledger(),
        facts=_facts(),
    )
    assert wrong_envelope_run.status is VerificationStatus.BLOCKED
    assert VerificationIssueCode.RUN_BINDING_MISMATCH in {
        issue.code for issue in wrong_envelope_run.failures
    }


def test_answer_material_is_protected_and_resolver_bound() -> None:
    envelope_payload = _envelope().model_dump(mode="json")
    envelope_payload["answer_text"] = _ANSWER_TEXT
    with pytest.raises(ValidationError):
        AnswerEnvelope.model_validate(envelope_payload)

    facts_payload = _facts().model_dump(mode="json")
    facts_payload["answer_material"]["content_digest"] = _DIGEST_B
    report = AnswerVerifier.verify(
        envelope=_envelope(),
        requirements=_ledger(),
        facts=AnswerVerificationFacts.model_validate(facts_payload),
    )
    assert report.status is VerificationStatus.BLOCKED
    assert VerificationIssueCode.ANSWER_MATERIAL_MISMATCH in {
        issue.code for issue in report.failures
    }
    assert _ANSWER_TEXT not in report.model_dump_json()


def test_unique_claim_accounting_does_not_credit_duplicate_ids() -> None:
    envelope_payload = _envelope().model_dump(mode="json")
    envelope_payload["claims"].append(envelope_payload["claims"][0])
    report = AnswerVerifier.verify(
        envelope=AnswerEnvelope.model_validate(envelope_payload),
        requirements=_ledger(),
        facts=_facts(),
    )

    assert report.declared_claim_count == 2
    assert report.unique_claim_count == 1
    assert report.verified_claim_count == 0
    assert {issue.code for issue in report.failures} == {
        VerificationIssueCode.DUPLICATE_CLAIM
    }
