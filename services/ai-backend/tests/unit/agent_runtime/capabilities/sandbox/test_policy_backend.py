"""Policy-enforced sandbox backend tests (budget, timeout, truncation, guards)."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfile
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
)
from agent_runtime.capabilities.sandbox.policy_backend import (
    PolicyEnforcedSandboxBackend,
)
from tests.unit.agent_runtime.capabilities.sandbox.fakes import FakeSandboxBackend


def _backend(**overrides) -> tuple[PolicyEnforcedSandboxBackend, FakeSandboxBackend]:
    limits = SandboxLimitProfile(name="t", **overrides)
    delegate = FakeSandboxBackend("fake-1")
    return PolicyEnforcedSandboxBackend(delegate=delegate, limits=limits), delegate


class TestExecutePolicy:
    def test_is_deep_agents_sandbox_backend(self) -> None:
        from deepagents.backends.protocol import SandboxBackendProtocol

        backend, _ = _backend()
        assert isinstance(backend, SandboxBackendProtocol)

    def test_command_budget_exhausts(self) -> None:
        backend, _ = _backend(commands_per_session=2)
        backend.execute("echo:a")
        backend.execute("echo:b")
        with pytest.raises(SandboxError) as excinfo:
            backend.execute("echo:c")
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_COMMAND_BUDGET_EXCEEDED
        assert backend.commands_used == 2

    def test_timeout_clamped_to_ceiling(self) -> None:
        backend, delegate = _backend(command_timeout_s=30)
        backend.execute("echo:a", timeout=9999)
        assert delegate.last_timeout == 30

    def test_timeout_default_when_none(self) -> None:
        backend, delegate = _backend(command_timeout_s=45)
        backend.execute("echo:a", timeout=None)
        assert delegate.last_timeout == 45

    def test_output_truncated_to_preview_ceiling(self) -> None:
        backend, _ = _backend(combined_command_preview_bytes=16)
        response = backend.execute("big:100")
        assert response.truncated is True
        assert "truncated" in response.output
        # First 16 bytes preserved before the note.
        assert response.output.startswith("x" * 16)

    def test_exit_code_passthrough(self) -> None:
        backend, _ = _backend()
        response = backend.execute("exit:3")
        assert response.exit_code == 3

    async def test_aexecute_applies_budget(self) -> None:
        backend, _ = _backend(commands_per_session=1)
        await backend.aexecute("echo:a")
        with pytest.raises(SandboxError):
            await backend.aexecute("echo:b")


class TestPathGuards:
    def test_upload_rejects_cross_prefix(self) -> None:
        backend, _ = _backend()
        with pytest.raises(SandboxError) as excinfo:
            backend.upload_files([("/drafts/x", b"data")])
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_PATH_NOT_ALLOWED

    def test_upload_allows_workspace(self) -> None:
        backend, delegate = _backend()
        responses = backend.upload_files([("/workspace/a.py", b"data")])
        assert responses[0].error is None
        assert delegate._files["/workspace/a.py"] == b"data"

    def test_download_guards_and_roundtrips(self) -> None:
        backend, _ = _backend()
        backend.upload_files([("/workspace/a.py", b"data")])
        result = backend.download_files(["/workspace/a.py"])
        assert result[0].content == b"data"
        with pytest.raises(SandboxError):
            backend.download_files(["/subagents/x"])

    def test_read_guards_cross_prefix(self) -> None:
        backend, _ = _backend()
        with pytest.raises(SandboxError) as excinfo:
            backend.read("/memories/note")
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_PATH_NOT_ALLOWED
