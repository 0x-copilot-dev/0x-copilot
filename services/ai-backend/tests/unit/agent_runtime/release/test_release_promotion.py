from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationResult,
    EvaluationRevisionSet,
    EvaluationScope,
    EvaluationStatus,
    HarnessManifest,
    HarnessManifestPointer,
    PromotionThresholds,
    ScorerResult,
)
from agent_runtime.release.assignment import (
    DevelopmentReleaseOverride,
    ReleaseAssignmentEligibility,
    ReleaseAssignmentPolicy,
    ReleaseAssignmentSource,
    StableReleaseAssigner,
)
from agent_runtime.release.control import (
    LocalReleaseControlService,
    ReleaseActivationError,
    RuntimeReleaseReader,
)
from agent_runtime.release.local_control import (
    LocalReleaseControlPolicy,
    ReleaseControlCommandName,
    ReleaseControlError,
    ReleaseControlProfile,
    parse_release_control_command,
)
from agent_runtime.release.manifest import (
    ReleaseManifestVerificationError,
    ReleaseManifestVerifier,
)
from agent_runtime.release.promotion import (
    PairedPromotionEvaluator,
    PromotionEvaluationInput,
)
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_hex,
)


_NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _manifest(
    private_key: Ed25519PrivateKey,
    *,
    manifest_id: str = "manifest-1",
    revision: str = "r1",
    previous_manifest_ref: str | None = None,
    not_before: datetime = _NOW - timedelta(minutes=1),
    expires_at: datetime | None = _NOW + timedelta(days=1),
) -> HarnessManifest:
    payload: dict[str, object] = {
        "schema_version": 1,
        "manifest_id": manifest_id,
        "revision": revision,
        "assignments": [
            {
                "variant_ref": "candidate",
                "variant_digest": _SHA_A,
                "allocation_basis_points": 5_000,
            },
            {
                "variant_ref": "control",
                "variant_digest": _SHA_B,
                "allocation_basis_points": 5_000,
            },
        ],
        "fallback_variant_ref": "control",
        "assignment_revision": "assignment-v1",
        "source_report_ref": "paired-report://report-1",
        "previous_manifest_ref": previous_manifest_ref,
        "issued_at": (_NOW - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "not_before": not_before.isoformat().replace("+00:00", "Z"),
        "expires_at": (
            expires_at.isoformat().replace("+00:00", "Z")
            if expires_at is not None
            else None
        ),
        "key_id": "release-key-1",
        "signature_algorithm": "ed25519",
    }
    payload_digest = canonical_json_sha256(payload)
    signature = private_key.sign(canonical_json_bytes(payload))
    return HarnessManifest(
        **payload,
        payload_digest=payload_digest,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )


def _revision_set(revision: str) -> EvaluationRevisionSet:
    return EvaluationRevisionSet(
        code_revision=f"code-{revision}",
        model_revision=f"model-{revision}",
        prompt_revision=f"prompt-{revision}",
        tool_revision=f"tool-{revision}",
        policy_revision=f"policy-{revision}",
        fixture_revision="fixture-paired-v1",
        scorer_revision="scorer-paired-v1",
    )


def _result(
    *,
    run_id: str,
    case_id: str,
    variant_id: str,
    succeeded: bool = True,
    cost: float = 1,
    latency_ms: int = 100,
    hard_failures: tuple[str, ...] = (),
    scorer_results: tuple[ScorerResult, ...] = (),
    suite_run_id: str | None = None,
    revisions: EvaluationRevisionSet | None = None,
) -> EvaluationResult:
    values: dict[str, object] = {
        "evaluation_run_id": run_id,
        "suite_run_id": suite_run_id or f"{variant_id}-suite",
        "case_id": case_id,
        "case_revision": "case-v1",
        "variant_id": variant_id,
        "variant_revision": f"{variant_id}-v1",
        "scorer_set_id": "scorer-set-v1",
        "revisions": revisions or _revision_set(variant_id),
        "status": (
            EvaluationStatus.SUCCEEDED if succeeded else EvaluationStatus.FAILED
        ),
        "scorer_results": scorer_results,
        "hard_gate_failures": hard_failures,
        "total_cost": cost,
        "model_turns": 1,
        "tool_calls": 1,
        "end_to_end_ms": latency_ms,
        "first_useful_answer_ms": latency_ms,
    }
    return EvaluationResult(
        **values,
        result_digest=EvaluationResult.digest_for(**values),
    )


def _promotion_request(
    *,
    candidate: tuple[EvaluationResult, ...],
    control: tuple[EvaluationResult, ...],
    families: dict[str, str] | None = None,
    protected: frozenset[str] = frozenset(),
) -> PromotionEvaluationInput:
    return PromotionEvaluationInput(
        report_id="report-1",
        candidate_variant_id="candidate",
        control_variant_id="control",
        candidate_suite_run_ids=("candidate-suite",),
        control_suite_run_ids=("control-suite",),
        candidate_revisions=_revision_set("candidate"),
        control_revisions=_revision_set("control"),
        candidate_results=candidate,
        control_results=control,
        case_task_families=families
        or {result.case_id: "ordinary" for result in candidate},
        thresholds=PromotionThresholds(
            revision="thresholds-v1",
            minimum_paired_cases=2,
            confidence_level=0.95,
            maximum_success_rate_regression=0,
            maximum_protected_family_regression=0,
            maximum_mean_cost_ratio=1.1,
            maximum_p95_latency_ratio=1.1,
            protected_task_families=protected,
        ),
        generated_at=_NOW,
    )


def test_release_manifest_verifies_exact_canonical_ed25519_payload() -> None:
    private_key = Ed25519PrivateKey.generate()
    manifest = _manifest(private_key)
    verifier = ReleaseManifestVerifier(
        verification_keys={"release-key-1": private_key.public_key()},
        clock=lambda: _NOW,
    )

    verified = verifier.verify(manifest)

    assert verified.manifest is manifest
    assert len(verified.verification_digest) == 64
    assert not hasattr(verifier, "sign")
    assert not hasattr(verifier, "promote")


def test_release_manifest_rejects_wrong_signature_unknown_key_and_expiry() -> None:
    signing_key = Ed25519PrivateKey.generate()
    wrong_key = Ed25519PrivateKey.generate()
    manifest = _manifest(signing_key)

    with pytest.raises(
        ReleaseManifestVerificationError,
        match="manifest_signature_invalid",
    ):
        ReleaseManifestVerifier(
            verification_keys={"release-key-1": wrong_key.public_key()},
            clock=lambda: _NOW,
        ).verify(manifest)

    with pytest.raises(
        ReleaseManifestVerificationError,
        match="manifest_key_unknown",
    ):
        ReleaseManifestVerifier(
            verification_keys={},
            clock=lambda: _NOW,
        ).verify(manifest)

    expired = _manifest(
        signing_key,
        not_before=_NOW - timedelta(days=2),
        expires_at=_NOW - timedelta(days=1),
    )
    with pytest.raises(ReleaseManifestVerificationError, match="manifest_expired"):
        ReleaseManifestVerifier(
            verification_keys={"release-key-1": signing_key.public_key()},
            clock=lambda: _NOW,
        ).verify(expired)


def test_assignment_is_stable_control_first_and_override_is_explicit() -> None:
    private_key = Ed25519PrivateKey.generate()
    manifest = _manifest(private_key)
    policy = ReleaseAssignmentPolicy(
        revision="assignment-policy-v1",
        online_assignment_enabled=True,
        candidate_task_families=frozenset({"research"}),
        candidate_sensitivities=frozenset({"synthetic"}),
    )
    eligibility = ReleaseAssignmentEligibility(
        task_family="research",
        sensitivity="synthetic",
    )
    assigner = StableReleaseAssigner()

    first = assigner.assign(
        run_id="run-1",
        opaque_subject_key="opaque-subject",
        manifest=manifest,
        policy=policy,
        eligibility=eligibility,
    )
    replay = assigner.assign(
        run_id="run-1",
        opaque_subject_key="opaque-subject",
        manifest=manifest,
        policy=policy,
        eligibility=eligibility,
    )
    protected = assigner.assign(
        run_id="run-2",
        opaque_subject_key="opaque-subject",
        manifest=manifest,
        policy=policy.model_copy(
            update={"protected_task_families": frozenset({"research"})}
        ),
        eligibility=eligibility,
    )
    override = assigner.assign(
        run_id="run-3",
        opaque_subject_key="opaque-subject",
        manifest=manifest,
        policy=policy,
        eligibility=eligibility,
        override=DevelopmentReleaseOverride(
            profile="dogfood",
            explicitly_enabled=True,
            variant_ref="candidate",
            rationale="local canary",
        ),
    )

    assert replay == first
    assert protected.variant_ref == "control"
    assert protected.source is ReleaseAssignmentSource.CONTROL_FALLBACK
    assert override.variant_ref == "candidate"
    assert override.source is ReleaseAssignmentSource.DEVELOPMENT_OVERRIDE
    assert "opaque-subject" not in first.model_dump_json()
    with pytest.raises(ValidationError):
        DevelopmentReleaseOverride(
            profile="production",
            explicitly_enabled=True,
            variant_ref="candidate",
            rationale="forbidden",
        )


def test_paired_report_passes_only_complete_confident_bounded_sample() -> None:
    candidate = tuple(
        _result(
            run_id=f"candidate-{index}", case_id=f"case-{index}", variant_id="candidate"
        )
        for index in range(2)
    )
    control = tuple(
        _result(
            run_id=f"control-{index}", case_id=f"case-{index}", variant_id="control"
        )
        for index in range(2)
    )

    report = PairedPromotionEvaluator().evaluate(
        _promotion_request(candidate=candidate, control=control)
    )

    assert report.assessment.passed
    assert report.assessment.reason_codes == ()
    assert report.missing_candidate_case_ids == ()
    assert report.missing_control_case_ids == ()
    assert report.candidate_revisions.code_revision == "code-candidate"


def test_paired_report_rejects_result_revision_or_suite_mismatch() -> None:
    candidate = _result(
        run_id="candidate-1",
        case_id="case-1",
        variant_id="candidate",
        suite_run_id="unexpected-suite",
        revisions=_revision_set("unexpected"),
    )
    control = _result(
        run_id="control-1",
        case_id="case-1",
        variant_id="control",
    )

    report = PairedPromotionEvaluator().evaluate(
        _promotion_request(
            candidate=(candidate,),
            control=(control,),
        )
    )

    assert not report.assessment.passed
    assert "candidate_revision_set_mismatch" in report.assessment.reason_codes
    assert "candidate_suite_run_mismatch" in report.assessment.reason_codes


def test_missing_data_and_protected_family_regression_are_hard_failures() -> None:
    candidate = (
        _result(
            run_id="candidate-1",
            case_id="protected-1",
            variant_id="candidate",
            succeeded=False,
        ),
    )
    control = (
        _result(
            run_id="control-1",
            case_id="protected-1",
            variant_id="control",
        ),
        _result(
            run_id="control-2",
            case_id="protected-2",
            variant_id="control",
        ),
    )

    report = PairedPromotionEvaluator().evaluate(
        _promotion_request(
            candidate=candidate,
            control=control,
            families={"protected-1": "protected", "protected-2": "protected"},
            protected=frozenset({"protected"}),
        )
    )

    assert not report.assessment.passed
    assert report.missing_candidate_case_ids == ("protected-2",)
    assert "unpaired_case_set" in report.assessment.reason_codes
    assert "minimum_paired_cases_not_met" in report.assessment.reason_codes
    assert "protected_family_regression" in report.assessment.reason_codes


def test_average_or_model_grader_score_cannot_override_hard_safety() -> None:
    optimistic_grader = ScorerResult(
        scorer_id="optional-model-grader",
        score=1,
        passed=True,
        hard_gate=False,
        reason_code="grader_optimistic",
    )
    candidate = (
        _result(
            run_id="candidate-1",
            case_id="case-1",
            variant_id="candidate",
            hard_failures=("unauthorized_effect",),
            scorer_results=(optimistic_grader,),
        ),
        _result(
            run_id="candidate-2",
            case_id="case-2",
            variant_id="candidate",
            scorer_results=(optimistic_grader,),
        ),
    )
    control = tuple(
        _result(
            run_id=f"control-{index}", case_id=f"case-{index}", variant_id="control"
        )
        for index in (1, 2)
    )

    report = PairedPromotionEvaluator().evaluate(
        _promotion_request(candidate=candidate, control=control)
    )

    assert not report.assessment.passed
    assert "candidate_hard_gate_failure" in report.assessment.reason_codes
    assert (
        "candidate_hard_safety_or_conformance_failure" in report.assessment.reason_codes
    )


def test_cost_and_latency_regression_each_block_promotion() -> None:
    candidate = tuple(
        _result(
            run_id=f"candidate-{index}",
            case_id=f"case-{index}",
            variant_id="candidate",
            cost=2,
            latency_ms=200,
        )
        for index in range(2)
    )
    control = tuple(
        _result(
            run_id=f"control-{index}",
            case_id=f"case-{index}",
            variant_id="control",
            cost=1,
            latency_ms=100,
        )
        for index in range(2)
    )

    assessment = (
        PairedPromotionEvaluator()
        .evaluate(_promotion_request(candidate=candidate, control=control))
        .assessment
    )

    assert not assessment.passed
    assert "mean_cost_ratio_exceeded" in assessment.reason_codes
    assert "p95_latency_ratio_exceeded" in assessment.reason_codes


def test_local_control_is_explicit_development_loopback_only() -> None:
    with pytest.raises(ValidationError, match="cannot be enabled in production"):
        LocalReleaseControlPolicy(
            profile=ReleaseControlProfile.PRODUCTION,
            explicitly_enabled=True,
        )
    with pytest.raises(ValidationError, match="literal loopback"):
        LocalReleaseControlPolicy(
            profile=ReleaseControlProfile.DEVELOPMENT,
            explicitly_enabled=True,
            bind_host="0.0.0.0",
        )

    policy = LocalReleaseControlPolicy(
        profile=ReleaseControlProfile.DOGFOOD,
        explicitly_enabled=True,
        bind_host="::1",
    )
    with pytest.raises(ReleaseControlError, match="peer is not loopback"):
        policy.authorize_peer("192.168.1.10")
    command = parse_release_control_command(
        [
            "rollback",
            "--target-manifest-digest",
            _SHA_A,
            "--rationale",
            "back out local canary",
        ]
    )
    assert command.name is ReleaseControlCommandName.ROLLBACK
    policy.authorize_command(command)


class _ManifestRepository:
    def __init__(self) -> None:
        self.manifests: dict[tuple[str, str], HarnessManifest] = {}
        self.pointer: HarnessManifestPointer | None = None
        self.exported = b'{"contract":"evaluation-export-v1"}'

    async def put_harness_manifest(
        self,
        _scope: EvaluationScope,
        manifest: HarnessManifest,
    ) -> bool:
        key = (manifest.manifest_id, manifest.revision)
        existing = self.manifests.get(key)
        if existing is not None and existing != manifest:
            raise ValueError("manifest conflict")
        self.manifests[key] = manifest
        return existing is None

    async def get_harness_manifest(
        self,
        _scope: EvaluationScope,
        *,
        manifest_id: str,
        revision: str,
    ) -> HarnessManifest | None:
        return self.manifests.get((manifest_id, revision))

    async def get_active_harness_manifest(
        self,
        _scope: EvaluationScope,
    ) -> HarnessManifestPointer | None:
        return self.pointer

    async def compare_and_set_active_harness_manifest(
        self,
        _scope: EvaluationScope,
        *,
        expected: HarnessManifestPointer | None,
        replacement: HarnessManifestPointer,
    ) -> HarnessManifestPointer:
        if self.pointer != expected:
            raise ValueError("pointer CAS conflict")
        self.pointer = replacement
        return replacement

    async def export_scope(self, _scope: EvaluationScope) -> bytes:
        return self.exported


async def test_local_activation_and_rollback_use_one_atomic_pointer_cas() -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = ReleaseManifestVerifier(
        verification_keys={"release-key-1": private_key.public_key()},
        clock=lambda: _NOW,
    )
    repository = _ManifestRepository()
    service = LocalReleaseControlService(
        repository=repository,  # type: ignore[arg-type]
        verifier=verifier,
        policy=LocalReleaseControlPolicy(
            profile=ReleaseControlProfile.DEVELOPMENT,
            explicitly_enabled=True,
        ),
        scope=EvaluationScope(profile_id="profile-1", project_id="project-1"),
    )
    scope = EvaluationScope(profile_id="profile-1", project_id="project-1")
    first = _manifest(private_key)
    second = _manifest(
        private_key,
        manifest_id="manifest-2",
        revision="r2",
        previous_manifest_ref=first.manifest_ref,
    )

    first_pointer = await service.install(
        manifest=first,
        activation_decision_id="decision-1",
        peer_host="127.0.0.1",
    )
    second_pointer = await service.install(
        manifest=second,
        activation_decision_id="decision-2",
        peer_host="127.0.0.1",
    )
    rolled_back = await service.rollback(
        target_manifest_id=first.manifest_id,
        target_manifest_revision=first.revision,
        activation_decision_id="decision-rollback",
        rationale="candidate safety regression",
        peer_host="127.0.0.1",
    )

    assert first_pointer.pointer_version == 1
    assert second_pointer.pointer_version == 2
    assert rolled_back.pointer_version == 3
    assert rolled_back.manifest_payload_digest == first.payload_digest
    assert repository.pointer == rolled_back
    active = await RuntimeReleaseReader(
        repository=repository,  # type: ignore[arg-type]
        verifier=verifier,
    ).active_manifest(scope=scope)
    assert active is not None
    assert active.manifest == first
    exported = await service.export(
        output_path="/explicit/caller-owned/report.json",
        peer_host="127.0.0.1",
    )
    assert exported.payload == repository.exported
    assert exported.payload_digest == sha256_hex(repository.exported)
    assert not hasattr(RuntimeReleaseReader, "install")
    assert not hasattr(RuntimeReleaseReader, "rollback")


async def test_rollback_rejects_non_predecessor_without_changing_pointer() -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = ReleaseManifestVerifier(
        verification_keys={"release-key-1": private_key.public_key()},
        clock=lambda: _NOW,
    )
    repository = _ManifestRepository()
    service = LocalReleaseControlService(
        repository=repository,  # type: ignore[arg-type]
        verifier=verifier,
        policy=LocalReleaseControlPolicy(
            profile=ReleaseControlProfile.DOGFOOD,
            explicitly_enabled=True,
        ),
        scope=EvaluationScope(profile_id="profile-1", project_id="project-1"),
    )
    scope = EvaluationScope(profile_id="profile-1", project_id="project-1")
    first = _manifest(private_key)
    other = _manifest(private_key, manifest_id="manifest-other", revision="other")
    await service.install(
        manifest=first,
        activation_decision_id="decision-1",
        peer_host="::1",
    )
    await repository.put_harness_manifest(scope, other)
    before = repository.pointer

    with pytest.raises(ReleaseActivationError, match="no rollback predecessor"):
        await service.rollback(
            target_manifest_id=other.manifest_id,
            target_manifest_revision=other.revision,
            activation_decision_id="decision-rollback",
            rationale="invalid target",
            peer_host="::1",
        )

    assert repository.pointer == before
