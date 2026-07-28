"""Read-only bootstrap from an active signed release to the run control plane."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_runtime.control_plane.ports import RunControlSnapshotStorePort
from agent_runtime.harness_quality.evaluation_contracts import EvaluationScope
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.release.assignment import DevelopmentReleaseOverride
from agent_runtime.release.control import RuntimeReleaseReader
from agent_runtime.release.local_control import ReleaseControlProfile
from agent_runtime.release.manifest import ReleaseManifestVerifier
from runtime_worker.run_control import (
    LiveConstraintsProvider,
    RunControlAssignment,
    RunControlAssignmentAllocation,
    RunControlPlaneBuilder,
    StableUserProfileHmac,
)
from runtime_worker.run_control_release import resolve_run_control_allocations


class RunControlReleaseOverrideError(RuntimeError):
    """A development override is not valid for the active signed release."""


@dataclass(frozen=True, slots=True)
class NoActiveRunControlRelease:
    """Typed safe-default result when the repository has no active pointer."""

    builder: RunControlPlaneBuilder
    reason: Literal["no_active_release"] = "no_active_release"


RunControlReleaseBootstrapResult = RunControlPlaneBuilder | NoActiveRunControlRelease


async def bootstrap_run_control_release(
    *,
    repository: EvaluationRepositoryPort,
    scope: EvaluationScope,
    verification_keys: Mapping[str, Ed25519PublicKey | bytes],
    catalog: Mapping[str, RunControlAssignment],
    store: RunControlSnapshotStorePort,
    deployment_profile: str,
    release_profile: ReleaseControlProfile,
    subject_hmac: StableUserProfileHmac,
    development_override: DevelopmentReleaseOverride | None = None,
    cutover_at: datetime | None = None,
    live_constraints: LiveConstraintsProvider | None = None,
    verification_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RunControlReleaseBootstrapResult:
    """Build run controls from the already-active, deployment-signed release.

    The runtime owns only public verification keys. It reads the active
    pointer through :class:`RuntimeReleaseReader` and intentionally exposes no
    promotion, signing, installation, rollback, or pointer mutation path.
    Verification and catalog-resolution errors propagate so an active pointer
    can never degrade silently to default controls.
    """

    # Freeze the deployment catalog before the repository await so a mutable
    # composition mapping cannot race manifest resolution during bootstrap.
    catalog_snapshot = dict(catalog)
    reader = RuntimeReleaseReader(
        repository=repository,
        verifier=ReleaseManifestVerifier(
            verification_keys=verification_keys,
            clock=verification_clock,
        ),
    )
    verified = await reader.active_manifest(scope=scope)
    if verified is None:
        if development_override is not None:
            raise RunControlReleaseOverrideError(
                "development override requires an active signed release"
            )
        return NoActiveRunControlRelease(
            builder=_builder(
                store=store,
                deployment_profile=deployment_profile,
                subject_hmac=subject_hmac,
                cutover_at=cutover_at,
                live_constraints=live_constraints,
            )
        )

    allocations = resolve_run_control_allocations(
        verified=verified,
        catalog=catalog_snapshot,
    )
    if development_override is not None:
        allocations = _development_override_allocations(
            allocations=allocations,
            override=development_override,
            release_profile=release_profile,
        )
    return _builder(
        store=store,
        deployment_profile=deployment_profile,
        subject_hmac=subject_hmac,
        allocations=allocations,
        cutover_at=cutover_at,
        live_constraints=live_constraints,
    )


def _development_override_allocations(
    *,
    allocations: tuple[RunControlAssignmentAllocation, ...],
    override: DevelopmentReleaseOverride,
    release_profile: ReleaseControlProfile,
) -> tuple[RunControlAssignmentAllocation, ...]:
    if release_profile not in {
        ReleaseControlProfile.DEVELOPMENT,
        ReleaseControlProfile.DOGFOOD,
    }:
        raise RunControlReleaseOverrideError(
            "development release override is unavailable in production"
        )
    if override.profile != release_profile.value:
        raise RunControlReleaseOverrideError(
            "development release override profile does not match runtime profile"
        )
    selected = next(
        (
            allocation
            for allocation in allocations
            if allocation.assignment.harness_variant_ref == override.variant_ref
        ),
        None,
    )
    if selected is None:
        raise RunControlReleaseOverrideError(
            "override variant is not present in the verified release catalog"
        )
    return (
        RunControlAssignmentAllocation(
            assignment=selected.assignment,
            allocation_basis_points=10_000,
            release_assignment_revision=selected.release_assignment_revision,
        ),
    )


def _builder(
    *,
    store: RunControlSnapshotStorePort,
    deployment_profile: str,
    subject_hmac: StableUserProfileHmac,
    allocations: tuple[RunControlAssignmentAllocation, ...] = (),
    cutover_at: datetime | None,
    live_constraints: LiveConstraintsProvider | None,
) -> RunControlPlaneBuilder:
    return RunControlPlaneBuilder(
        store=store,
        deployment_profile=deployment_profile,
        subject_hmac=subject_hmac,
        allocations=allocations,
        cutover_at=cutover_at,
        live_constraints=live_constraints,
    )


__all__ = (
    "NoActiveRunControlRelease",
    "RunControlReleaseBootstrapResult",
    "RunControlReleaseOverrideError",
    "bootstrap_run_control_release",
)
