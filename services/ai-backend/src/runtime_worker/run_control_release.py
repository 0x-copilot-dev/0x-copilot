"""Resolve a verified signed harness manifest into Step 1 run controls."""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.release.manifest import VerifiedHarnessManifest
from runtime_worker.run_control import (
    RunControlAssignment,
    RunControlAssignmentAllocation,
)


class RunControlReleaseResolutionError(RuntimeError):
    """A signed release references an absent or digest-mismatched variant."""


def resolve_run_control_allocations(
    *,
    verified: VerifiedHarnessManifest,
    catalog: Mapping[str, RunControlAssignment],
) -> tuple[RunControlAssignmentAllocation, ...]:
    """Bind signed weights to deployment-owned, fully versioned assignments."""

    manifest = verified.manifest
    if manifest.fallback_variant_ref not in {
        item.variant_ref for item in manifest.assignments
    }:
        raise RunControlReleaseResolutionError(
            "manifest fallback variant is not assigned"
        )
    allocations: list[RunControlAssignmentAllocation] = []
    for signed in manifest.assignments:
        assignment = catalog.get(signed.variant_ref)
        if assignment is None:
            raise RunControlReleaseResolutionError(
                "manifest references an unknown run-control variant"
            )
        if assignment.harness_variant_ref != signed.variant_ref:
            raise RunControlReleaseResolutionError(
                "run-control catalog key does not match the variant ref"
            )
        if assignment.digest != signed.variant_digest:
            raise RunControlReleaseResolutionError(
                "signed variant digest does not match run-control catalog"
            )
        allocations.append(
            RunControlAssignmentAllocation(
                assignment=assignment,
                allocation_basis_points=signed.allocation_basis_points,
                release_assignment_revision=manifest.assignment_revision,
            )
        )
    return tuple(allocations)


__all__ = (
    "RunControlReleaseResolutionError",
    "resolve_run_control_allocations",
)
