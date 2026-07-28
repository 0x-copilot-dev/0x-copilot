"""Deterministic immutable run assignment for signed harness manifests."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation_contracts import (
    HarnessManifest,
    HarnessManifestAssignment,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class ReleaseAssignmentSource(StrEnum):
    SIGNED_MANIFEST = "signed_manifest"
    CONTROL_FALLBACK = "control_fallback"
    DEVELOPMENT_OVERRIDE = "development_override"


class ReleaseAssignmentEligibility(RuntimeContract):
    """Verified task facts used only to tighten candidate eligibility."""

    task_family: Annotated[str, Field(min_length=1, max_length=80)]
    sensitivity: Annotated[str, Field(min_length=1, max_length=40)]
    effectful: bool = False


class ReleaseAssignmentPolicy(RuntimeContract):
    """Local experiment policy; disabled and protected tasks stay control."""

    revision: Annotated[str, Field(min_length=1, max_length=160)]
    online_assignment_enabled: bool = False
    candidate_task_families: frozenset[str] = frozenset()
    protected_task_families: frozenset[str] = frozenset()
    candidate_sensitivities: frozenset[str] = frozenset({"synthetic"})
    allow_effectful_candidate: bool = False

    def permits_candidate(self, eligibility: ReleaseAssignmentEligibility) -> bool:
        return (
            self.online_assignment_enabled
            and eligibility.task_family in self.candidate_task_families
            and eligibility.task_family not in self.protected_task_families
            and eligibility.sensitivity in self.candidate_sensitivities
            and (self.allow_effectful_candidate or not eligibility.effectful)
        )


class DevelopmentReleaseOverride(RuntimeContract):
    """Explicit local override; never accepted for a production profile."""

    profile: Annotated[str, Field(pattern=r"^(development|dogfood)$")]
    explicitly_enabled: bool
    variant_ref: Annotated[str, Field(min_length=1, max_length=256)]
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]

    @model_validator(mode="after")
    def _override_is_explicit(self) -> "DevelopmentReleaseOverride":
        if not self.explicitly_enabled:
            raise ValueError("development release override must be explicitly enabled")
        return self


class ReleaseAssignment(RuntimeContract):
    """Content-free run binding persisted before the first model call."""

    run_id: Annotated[str, Field(min_length=1, max_length=160)]
    manifest_ref: Annotated[str, Field(min_length=1, max_length=512)]
    assignment_revision: Annotated[str, Field(min_length=1, max_length=160)]
    assignment_policy_revision: Annotated[str, Field(min_length=1, max_length=160)]
    subject_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    bucket: Annotated[int, Field(ge=0, lt=10_000)]
    variant_ref: Annotated[str, Field(min_length=1, max_length=256)]
    variant_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    source: ReleaseAssignmentSource
    assignment_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def _assignment_digest_matches(self) -> "ReleaseAssignment":
        if self.assignment_digest != self.digest_for(
            run_id=self.run_id,
            manifest_ref=self.manifest_ref,
            assignment_revision=self.assignment_revision,
            assignment_policy_revision=self.assignment_policy_revision,
            subject_digest=self.subject_digest,
            bucket=self.bucket,
            variant_ref=self.variant_ref,
            variant_digest=self.variant_digest,
            source=self.source,
        ):
            raise ValueError("assignment_digest does not match immutable assignment")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        from pydantic_core import to_jsonable_python

        return canonical_json_sha256(to_jsonable_python(values))


class StableReleaseAssigner:
    """Assign one run without mutable counters or ambient randomness."""

    def assign(
        self,
        *,
        run_id: str,
        opaque_subject_key: str,
        manifest: HarnessManifest,
        policy: ReleaseAssignmentPolicy,
        eligibility: ReleaseAssignmentEligibility,
        override: DevelopmentReleaseOverride | None = None,
    ) -> ReleaseAssignment:
        if not opaque_subject_key:
            raise ValueError("opaque_subject_key must not be empty")

        assignments = {
            assignment.variant_ref: assignment for assignment in manifest.assignments
        }
        fallback = assignments.get(manifest.fallback_variant_ref)
        if fallback is None:
            raise ValueError("manifest fallback variant must be assigned")

        subject_digest = canonical_json_sha256(
            {
                "purpose": "release-assignment-subject-v1",
                "opaque_subject_key": opaque_subject_key,
            }
        )
        bucket = (
            int(
                canonical_json_sha256(
                    {
                        "purpose": "release-assignment-bucket-v1",
                        "subject_digest": subject_digest,
                        "assignment_revision": manifest.assignment_revision,
                    }
                )[:16],
                16,
            )
            % 10_000
        )

        selected: HarnessManifestAssignment
        source: ReleaseAssignmentSource
        if override is not None:
            override_assignment = assignments.get(override.variant_ref)
            if override_assignment is None:
                raise ValueError("override variant is not present in signed manifest")
            selected = override_assignment
            source = ReleaseAssignmentSource.DEVELOPMENT_OVERRIDE
        elif policy.permits_candidate(eligibility):
            selected = self._select_bucket(manifest.assignments, bucket=bucket)
            source = ReleaseAssignmentSource.SIGNED_MANIFEST
        else:
            selected = fallback
            source = ReleaseAssignmentSource.CONTROL_FALLBACK

        values: dict[str, object] = {
            "run_id": run_id,
            "manifest_ref": manifest.manifest_ref,
            "assignment_revision": manifest.assignment_revision,
            "assignment_policy_revision": policy.revision,
            "subject_digest": subject_digest,
            "bucket": bucket,
            "variant_ref": selected.variant_ref,
            "variant_digest": selected.variant_digest,
            "source": source,
        }
        return ReleaseAssignment(
            **values,
            assignment_digest=ReleaseAssignment.digest_for(**values),
        )

    @staticmethod
    def _select_bucket(
        assignments: tuple[HarnessManifestAssignment, ...],
        *,
        bucket: int,
    ) -> HarnessManifestAssignment:
        upper_bound = 0
        for assignment in assignments:
            upper_bound += assignment.allocation_basis_points
            if bucket < upper_bound:
                return assignment
        raise RuntimeError("manifest allocations do not cover assignment bucket")


__all__ = (
    "DevelopmentReleaseOverride",
    "ReleaseAssignment",
    "ReleaseAssignmentEligibility",
    "ReleaseAssignmentPolicy",
    "ReleaseAssignmentSource",
    "StableReleaseAssigner",
)
