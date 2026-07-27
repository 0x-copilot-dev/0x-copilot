from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.harness_quality.evaluation_contracts import HarnessManifest
from agent_runtime.release.manifest import ReleaseManifestVerifier
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from runtime_worker.run_control import (
    RunControlAssignment,
    RunControlPlaneBuilder,
    StableUserProfileHmac,
)
from runtime_worker.run_control_release import (
    RunControlReleaseResolutionError,
    resolve_run_control_allocations,
)

from tests.unit.runtime_worker.test_run_control import (
    _SnapshotStore,
    _assignment,
    _run,
)


_NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def _signed_manifest(
    *,
    key: Ed25519PrivateKey,
    candidate: RunControlAssignment,
    control: RunControlAssignment,
    fallback: str | None = None,
    candidate_digest: str | None = None,
) -> HarnessManifest:
    payload: dict[str, object] = {
        "schema_version": 1,
        "manifest_id": "manifest-run-control-v1",
        "revision": "manifest-revision-v1",
        "assignments": [
            {
                "variant_ref": candidate.harness_variant_ref,
                "variant_digest": candidate_digest or candidate.digest,
                "allocation_basis_points": 0,
            },
            {
                "variant_ref": control.harness_variant_ref,
                "variant_digest": control.digest,
                "allocation_basis_points": 10_000,
            },
        ],
        "fallback_variant_ref": fallback or control.harness_variant_ref,
        "assignment_revision": "release-assignment-v1",
        "source_report_ref": "paired-report://report-v1",
        "previous_manifest_ref": None,
        "issued_at": (_NOW - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "not_before": (_NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (_NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "key_id": "release-key-v1",
        "signature_algorithm": "ed25519",
    }
    return HarnessManifest(
        **payload,
        payload_digest=canonical_json_sha256(payload),
        signature_b64=base64.b64encode(
            key.sign(canonical_json_bytes(payload))
        ).decode(),
    )


def _verified(
    manifest: HarnessManifest,
    *,
    key: Ed25519PrivateKey,
):
    return ReleaseManifestVerifier(
        verification_keys={"release-key-v1": key.public_key()},
        clock=lambda: _NOW,
    ).verify(manifest)


async def test_verified_manifest_weights_bind_the_step_one_snapshot() -> None:
    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate-v1", FeatureMode.OFF)
    control = _assignment("control-v1", FeatureMode.OFF)
    verified = _verified(
        _signed_manifest(key=key, candidate=candidate, control=control),
        key=key,
    )
    allocations = resolve_run_control_allocations(
        verified=verified,
        catalog={
            candidate.harness_variant_ref: candidate,
            control.harness_variant_ref: control,
        },
    )
    store = _SnapshotStore()
    builder = RunControlPlaneBuilder(
        store=store,
        deployment_profile="single_user_desktop",
        subject_hmac=StableUserProfileHmac(b"manifest-run-control-key-v1"),
        allocations=allocations,
        cutover_at=_NOW - timedelta(days=1),
    )

    snapshot = await builder.ensure_snapshot(
        run=_run(created_at=_NOW),
        trace_id="trace-release",
    )

    assert snapshot.harness_variant_ref == control.harness_variant_ref
    assert snapshot.assignment_revision == "release-assignment-v1"


def test_manifest_resolution_rejects_unknown_and_digest_mismatched_variants() -> None:
    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate-v1", FeatureMode.OFF)
    control = _assignment("control-v1", FeatureMode.OFF)
    mismatched = _verified(
        _signed_manifest(
            key=key,
            candidate=candidate,
            control=control,
            candidate_digest="f" * 64,
        ),
        key=key,
    )

    with pytest.raises(
        RunControlReleaseResolutionError,
        match="digest",
    ):
        resolve_run_control_allocations(
            verified=mismatched,
            catalog={
                candidate.harness_variant_ref: candidate,
                control.harness_variant_ref: control,
            },
        )

    with pytest.raises(
        RunControlReleaseResolutionError,
        match="unknown",
    ):
        resolve_run_control_allocations(
            verified=_verified(
                _signed_manifest(key=key, candidate=candidate, control=control),
                key=key,
            ),
            catalog={control.harness_variant_ref: control},
        )


def test_manifest_resolution_rejects_a_fallback_outside_assignments() -> None:
    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate-v1", FeatureMode.OFF)
    control = _assignment("control-v1", FeatureMode.OFF)
    verified = _verified(
        _signed_manifest(
            key=key,
            candidate=candidate,
            control=control,
            fallback="harness://missing",
        ),
        key=key,
    )

    with pytest.raises(
        RunControlReleaseResolutionError,
        match="fallback",
    ):
        resolve_run_control_allocations(
            verified=verified,
            catalog={
                candidate.harness_variant_ref: candidate,
                control.harness_variant_ref: control,
            },
        )
