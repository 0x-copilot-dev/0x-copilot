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
