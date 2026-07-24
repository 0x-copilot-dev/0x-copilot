"""Contract parsing + typed-error tests for the sandbox capability."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    ManagedSandboxSession,
    SandboxError,
    SandboxErrorCode,
    SandboxEgressPolicy,
    SandboxIsolationAttestation,
    SandboxProviderId,
    WorkspaceTransferEntry,
)


class TestArtifactRef:
    def test_rejects_short_sha(self) -> None:
        with pytest.raises(ValidationError):
            ArtifactRef(artifact_id="a", sha256="abc", size_bytes=1)

    def test_accepts_valid(self) -> None:
        ref = ArtifactRef(artifact_id="a", sha256="a" * 64, size_bytes=10)
        assert ref.size_bytes == 10


class TestWorkspaceTransferEntry:
    def test_requires_payload_ref(self) -> None:
        with pytest.raises(ValidationError):
            WorkspaceTransferEntry(
                path="/workspace/a", sha256="a" * 64, size_bytes=1, executable=False
            )  # type: ignore[call-arg]

    def test_rejects_mismatched_content_metadata(self) -> None:
        with pytest.raises(ValidationError):
            WorkspaceTransferEntry(
                path="/workspace/a",
                sha256="a" * 64,
                size_bytes=1,
                payload_ref=ArtifactRef(artifact_id="a", sha256="b" * 64, size_bytes=1),
            )


class TestEgressAndAttestation:
    @pytest.mark.parametrize(
        "destination",
        ["*.example.com", "https://example.com", "127.0.0.1", "example.com:443"],
    )
    def test_egress_policy_rejects_broadened_destination(
        self, destination: str
    ) -> None:
        with pytest.raises(ValidationError):
            SandboxEgressPolicy(mode="allowlist", destinations=(destination,))

    def test_deny_all_cannot_smuggle_destination(self) -> None:
        with pytest.raises(ValidationError):
            SandboxEgressPolicy(mode="deny_all", destinations=("api.example.com",))

    def test_process_only_attestation_is_not_a_security_boundary(self) -> None:
        attestation = SandboxIsolationAttestation(
            provider=SandboxProviderId.LANGSMITH,
            isolation="process",
            process_isolated=True,
            filesystem_fresh=True,
            teardown_guaranteed=True,
            host_credentials_absent=True,
            cpu_quota_enforced=True,
            memory_quota_enforced=True,
            wall_clock_quota_enforced=True,
            process_quota_enforced=True,
            file_quota_enforced=True,
            egress_mode="deny_all",
            attestation_ref="attestation://test/process-only",
        )
        assert attestation.satisfies(SandboxEgressPolicy()) is False


class TestSandboxError:
    def test_carries_code_and_message(self) -> None:
        err = SandboxError(SandboxErrorCode.SANDBOX_DISABLED, "off")
        assert err.code is SandboxErrorCode.SANDBOX_DISABLED
        assert err.message == "off"
        assert "sandbox_disabled" in str(err)


class TestManagedSandboxSession:
    def _session(self, expires_at: datetime) -> ManagedSandboxSession:
        return ManagedSandboxSession(
            session_id="s1",
            provider=SandboxProviderId.LANGSMITH,
            provider_session_ref="ref-1",
            owner_tag="owner",
            expires_at=expires_at,
        )

    def test_with_state_is_immutable_copy(self) -> None:
        session = self._session(datetime.now(timezone.utc) + timedelta(minutes=5))
        moved = session.with_state("deleted")
        assert session.cleanup_state == "active"
        assert moved.cleanup_state == "deleted"

    def test_is_expired(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        assert self._session(past).is_expired() is True
        assert self._session(future).is_expired() is False
