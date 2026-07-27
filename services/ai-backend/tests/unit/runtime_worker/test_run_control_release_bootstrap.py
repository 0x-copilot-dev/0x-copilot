from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.ports import RunControlSnapshotWrite
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationScope,
    HarnessManifest,
    HarnessManifestPointer,
)
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.release.assignment import DevelopmentReleaseOverride
from agent_runtime.release.local_control import ReleaseControlProfile
from agent_runtime.release.manifest import ReleaseManifestVerificationError
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from runtime_api.schemas import AgentRunStatus, RunRecord
from runtime_worker.run_control import (
    RunControlAssignment,
    RunControlPlaneBuilder,
    StableUserProfileHmac,
)
from runtime_worker.run_control_release import RunControlReleaseResolutionError
from runtime_worker.run_control_release_bootstrap import (
    NoActiveRunControlRelease,
    RunControlReleaseOverrideError,
    bootstrap_run_control_release,
)


_NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
_SCOPE = EvaluationScope(profile_id="profile-1", project_id="project-1")


class _ReadOnlyManifestRepository:
    def __init__(
        self,
        *,
        manifest: HarnessManifest | None = None,
        pointer: HarnessManifestPointer | None = None,
    ) -> None:
        self.manifest = manifest
        self.pointer = pointer
        self.pointer_reads = 0
        self.manifest_reads = 0

    async def get_active_harness_manifest(
        self,
        _scope: EvaluationScope,
    ) -> HarnessManifestPointer | None:
        self.pointer_reads += 1
        return self.pointer

    async def get_harness_manifest(
        self,
        _scope: EvaluationScope,
        *,
        manifest_id: str,
        revision: str,
    ) -> HarnessManifest | None:
        self.manifest_reads += 1
        if (
            self.manifest is not None
            and self.manifest.manifest_id == manifest_id
            and self.manifest.revision == revision
        ):
            return self.manifest
        return None

    def __getattr__(self, name: str) -> object:
        if (
            name.startswith(("put_", "compare_and_set_", "delete_"))
            or name == "export_scope"
        ):
            raise AssertionError(f"bootstrap attempted repository mutation: {name}")
        raise AttributeError(name)


class _SnapshotStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, RunControlSnapshot] = {}

    async def get(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunControlSnapshot | None:
        del org_id
        snapshot = self.snapshots.get(run_id)
        if snapshot is not None and snapshot.subject_fingerprint != subject_fingerprint:
            raise AssertionError("test snapshot subject mismatch")
        return snapshot

    async def get_or_create(self, write: RunControlSnapshotWrite) -> RunControlSnapshot:
        return self.snapshots.setdefault(write.snapshot.run_id, write.snapshot)


def _assignment(name: str) -> RunControlAssignment:
    return RunControlAssignment.safe_active_v1().model_copy(
        update={
            "harness_variant_ref": f"harness://{name}",
            "assignment_revision": f"{name}-v1",
        }
    )


def _signed_manifest(
    *,
    key: Ed25519PrivateKey,
    candidate: RunControlAssignment,
    control: RunControlAssignment,
) -> HarnessManifest:
    payload: dict[str, object] = {
        "schema_version": 1,
        "manifest_id": "manifest-1",
        "revision": "manifest-r1",
        "assignments": [
            {
                "variant_ref": candidate.harness_variant_ref,
                "variant_digest": candidate.digest,
                "allocation_basis_points": 0,
            },
            {
                "variant_ref": control.harness_variant_ref,
                "variant_digest": control.digest,
                "allocation_basis_points": 10_000,
            },
        ],
        "fallback_variant_ref": control.harness_variant_ref,
        "assignment_revision": "release-assignment-v1",
        "source_report_ref": "paired-report://report-1",
        "previous_manifest_ref": None,
        "issued_at": (_NOW - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "not_before": (_NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (_NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "key_id": "release-key-1",
        "signature_algorithm": "ed25519",
    }
    return HarnessManifest(
        **payload,
        payload_digest=canonical_json_sha256(payload),
        signature_b64=base64.b64encode(key.sign(canonical_json_bytes(payload))).decode(
            "ascii"
        ),
    )


def _pointer(manifest: HarnessManifest) -> HarnessManifestPointer:
    values: dict[str, object] = {
        "pointer_version": 1,
        "manifest_id": manifest.manifest_id,
        "manifest_revision": manifest.revision,
        "manifest_payload_digest": manifest.payload_digest,
        "activation_decision_id": "decision-1",
        "previous_manifest_ref": None,
        "updated_at": _NOW,
    }
    return HarnessManifestPointer(
        **values,
        pointer_digest=HarnessManifestPointer.digest_for(**values),
    )


def _run(*, run_id: str = "run-1", user_id: str = "user-1") -> RunRecord:
    return RunRecord.model_construct(
        run_id=run_id,
        conversation_id=f"conversation-{run_id}",
        org_id="org-1",
        user_id=user_id,
        trace_id=f"trace-{run_id}",
        status=AgentRunStatus.QUEUED,
        created_at=_NOW,
    )


async def test_active_release_bootstrap_is_read_only_and_builds_signed_controls() -> (
    None
):
    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate")
    control = _assignment("control")
    manifest = _signed_manifest(key=key, candidate=candidate, control=control)
    repository = _ReadOnlyManifestRepository(
        manifest=manifest,
        pointer=_pointer(manifest),
    )
    store = _SnapshotStore()

    result = await bootstrap_run_control_release(
        repository=cast(EvaluationRepositoryPort, repository),
        scope=_SCOPE,
        verification_keys={"release-key-1": key.public_key()},
        catalog={
            candidate.harness_variant_ref: candidate,
            control.harness_variant_ref: control,
        },
        store=store,
        deployment_profile="single_user_desktop",
        release_profile=ReleaseControlProfile.PRODUCTION,
        subject_hmac=StableUserProfileHmac(b"release-bootstrap-hmac-key-v1"),
        cutover_at=_NOW - timedelta(days=1),
        verification_clock=lambda: _NOW,
    )

    assert isinstance(result, RunControlPlaneBuilder)
    snapshot = await result.ensure_snapshot(run=_run(), trace_id="trace-1")
    assert snapshot.harness_variant_ref == control.harness_variant_ref
    assert snapshot.assignment_revision == manifest.assignment_revision
    assert repository.pointer_reads == 1
    assert repository.manifest_reads == 1


async def test_no_active_release_is_typed_and_uses_only_safe_defaults() -> None:
    repository = _ReadOnlyManifestRepository()
    result = await bootstrap_run_control_release(
        repository=cast(EvaluationRepositoryPort, repository),
        scope=_SCOPE,
        verification_keys={},
        catalog={},
        store=_SnapshotStore(),
        deployment_profile="single_user_desktop",
        release_profile=ReleaseControlProfile.PRODUCTION,
        subject_hmac=StableUserProfileHmac(b"release-bootstrap-hmac-key-v1"),
        cutover_at=_NOW - timedelta(days=1),
        verification_clock=lambda: _NOW,
    )

    assert isinstance(result, NoActiveRunControlRelease)
    assert result.reason == "no_active_release"
    snapshot = await result.builder.ensure_snapshot(run=_run(), trace_id="trace-1")
    assert snapshot.harness_variant_ref == "harness://active-safe-v1"
    assert snapshot.assignment_revision == "active-safe-v1"
    assert repository.pointer_reads == 1
    assert repository.manifest_reads == 0


async def test_active_pointer_fails_closed_on_signature_verification_error() -> None:
    signing_key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate")
    control = _assignment("control")
    manifest = _signed_manifest(
        key=signing_key,
        candidate=candidate,
        control=control,
    )
    repository = _ReadOnlyManifestRepository(
        manifest=manifest,
        pointer=_pointer(manifest),
    )

    with pytest.raises(
        ReleaseManifestVerificationError,
        match="manifest_signature_invalid",
    ):
        await bootstrap_run_control_release(
            repository=cast(EvaluationRepositoryPort, repository),
            scope=_SCOPE,
            verification_keys={
                "release-key-1": Ed25519PrivateKey.generate().public_key()
            },
            catalog={
                candidate.harness_variant_ref: candidate,
                control.harness_variant_ref: control,
            },
            store=_SnapshotStore(),
            deployment_profile="single_user_desktop",
            release_profile=ReleaseControlProfile.PRODUCTION,
            subject_hmac=StableUserProfileHmac(b"release-bootstrap-hmac-key-v1"),
            verification_clock=lambda: _NOW,
        )


async def test_active_pointer_fails_closed_on_catalog_resolution_error() -> None:
    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate")
    control = _assignment("control")
    manifest = _signed_manifest(key=key, candidate=candidate, control=control)
    repository = _ReadOnlyManifestRepository(
        manifest=manifest,
        pointer=_pointer(manifest),
    )

    with pytest.raises(RunControlReleaseResolutionError, match="unknown"):
        await bootstrap_run_control_release(
            repository=cast(EvaluationRepositoryPort, repository),
            scope=_SCOPE,
            verification_keys={"release-key-1": key.public_key()},
            catalog={control.harness_variant_ref: control},
            store=_SnapshotStore(),
            deployment_profile="single_user_desktop",
            release_profile=ReleaseControlProfile.PRODUCTION,
            subject_hmac=StableUserProfileHmac(b"release-bootstrap-hmac-key-v1"),
            verification_clock=lambda: _NOW,
        )


@pytest.mark.parametrize(
    ("release_profile", "override_profile"),
    [
        (ReleaseControlProfile.DEVELOPMENT, "development"),
        (ReleaseControlProfile.DOGFOOD, "dogfood"),
    ],
)
async def test_local_override_becomes_one_full_weight_builder_allocation(
    release_profile: ReleaseControlProfile,
    override_profile: str,
) -> None:
    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate")
    control = _assignment("control")
    manifest = _signed_manifest(key=key, candidate=candidate, control=control)
    repository = _ReadOnlyManifestRepository(
        manifest=manifest,
        pointer=_pointer(manifest),
    )

    result = await bootstrap_run_control_release(
        repository=cast(EvaluationRepositoryPort, repository),
        scope=_SCOPE,
        verification_keys={"release-key-1": key.public_key()},
        catalog={
            candidate.harness_variant_ref: candidate,
            control.harness_variant_ref: control,
        },
        store=_SnapshotStore(),
        deployment_profile="single_user_desktop",
        release_profile=release_profile,
        subject_hmac=StableUserProfileHmac(b"release-bootstrap-hmac-key-v1"),
        development_override=DevelopmentReleaseOverride(
            profile=override_profile,
            explicitly_enabled=True,
            variant_ref=candidate.harness_variant_ref,
            rationale="exercise local candidate",
        ),
        cutover_at=_NOW - timedelta(days=1),
        verification_clock=lambda: _NOW,
    )

    assert isinstance(result, RunControlPlaneBuilder)
    first = await result.ensure_snapshot(run=_run(), trace_id="trace-1")
    second = await result.ensure_snapshot(
        run=_run(run_id="run-2", user_id="user-2"),
        trace_id="trace-2",
    )
    assert first.harness_variant_ref == candidate.harness_variant_ref
    assert second.harness_variant_ref == candidate.harness_variant_ref
    assert first.assignment_revision == manifest.assignment_revision


async def test_production_rejects_development_override() -> None:
    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate")
    control = _assignment("control")
    manifest = _signed_manifest(key=key, candidate=candidate, control=control)
    repository = _ReadOnlyManifestRepository(
        manifest=manifest,
        pointer=_pointer(manifest),
    )

    with pytest.raises(
        RunControlReleaseOverrideError,
        match="unavailable in production",
    ):
        await bootstrap_run_control_release(
            repository=cast(EvaluationRepositoryPort, repository),
            scope=_SCOPE,
            verification_keys={"release-key-1": key.public_key()},
            catalog={
                candidate.harness_variant_ref: candidate,
                control.harness_variant_ref: control,
            },
            store=_SnapshotStore(),
            deployment_profile="saas_multi_tenant",
            release_profile=ReleaseControlProfile.PRODUCTION,
            subject_hmac=StableUserProfileHmac(b"release-bootstrap-hmac-key-v1"),
            development_override=DevelopmentReleaseOverride(
                profile="development",
                explicitly_enabled=True,
                variant_ref=candidate.harness_variant_ref,
                rationale="must not cross into production",
            ),
            verification_clock=lambda: _NOW,
        )


async def test_override_requires_active_release_and_verified_catalog_membership() -> (
    None
):
    override = DevelopmentReleaseOverride(
        profile="development",
        explicitly_enabled=True,
        variant_ref="harness://missing",
        rationale="invalid local target",
    )
    common = {
        "scope": _SCOPE,
        "store": _SnapshotStore(),
        "deployment_profile": "single_user_desktop",
        "release_profile": ReleaseControlProfile.DEVELOPMENT,
        "subject_hmac": StableUserProfileHmac(b"release-bootstrap-hmac-key-v1"),
        "development_override": override,
        "verification_clock": lambda: _NOW,
    }

    with pytest.raises(
        RunControlReleaseOverrideError,
        match="requires an active signed release",
    ):
        await bootstrap_run_control_release(
            repository=cast(
                EvaluationRepositoryPort,
                _ReadOnlyManifestRepository(),
            ),
            verification_keys={},
            catalog={},
            **common,  # type: ignore[arg-type]
        )

    key = Ed25519PrivateKey.generate()
    candidate = _assignment("candidate")
    control = _assignment("control")
    manifest = _signed_manifest(key=key, candidate=candidate, control=control)
    with pytest.raises(
        RunControlReleaseOverrideError,
        match="verified release catalog",
    ):
        await bootstrap_run_control_release(
            repository=cast(
                EvaluationRepositoryPort,
                _ReadOnlyManifestRepository(
                    manifest=manifest,
                    pointer=_pointer(manifest),
                ),
            ),
            verification_keys={"release-key-1": key.public_key()},
            catalog={
                candidate.harness_variant_ref: candidate,
                control.harness_variant_ref: control,
            },
            **common,  # type: ignore[arg-type]
        )
