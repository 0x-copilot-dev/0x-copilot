"""In-repo fake provider + sandbox backend for sandbox tests.

The fake is a STUB, never a real execution environment: its ``execute`` is a
deterministic simulator over a tiny command grammar and its filesystem is an
in-memory dict. It runs no host subprocess and imports no provider SDK, so the
conformance and lifecycle suites are provider-independent and hermetic.

Command grammar understood by :class:`FakeSandboxBackend.execute`:

* ``echo:<text>``   → stdout ``<text>``, exit 0
* ``exit:<code>``   → empty output, exit ``<code>``
* ``big:<n>``       → stdout of ``n`` ``x`` bytes, exit 0 (drives truncation)
* ``timeout``       → raises ``TimeoutError`` (drives command-timeout mapping)
* anything else     → stdout echoing the command, exit 0
"""

from __future__ import annotations

from datetime import timedelta

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from agent_runtime.capabilities.sandbox.contracts import (
    ManagedSandboxSession,
    SandboxCreateRequest,
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
    _utcnow,
)
from agent_runtime.capabilities.sandbox.ports import SandboxHandle


class FakeSandboxBackend(BaseSandbox):
    """In-memory DeepAgents sandbox backend for tests."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._files: dict[str, bytes] = {}
        self.executed_commands: list[str] = []
        self.last_timeout: int | None = None

    @property
    def id(self) -> str:
        return self._name

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.executed_commands.append(command)
        self.last_timeout = timeout
        if command == "timeout":
            raise TimeoutError("simulated command timeout")
        if command.startswith("echo:"):
            return ExecuteResponse(output=command[len("echo:") :], exit_code=0)
        if command.startswith("exit:"):
            return ExecuteResponse(output="", exit_code=int(command[len("exit:") :]))
        if command.startswith("big:"):
            size = int(command[len("big:") :])
            return ExecuteResponse(output="x" * size, exit_code=0)
        return ExecuteResponse(output=command, exit_code=0)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            self._files[path] = content
            responses.append(FileUploadResponse(path=path, error=None))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            content = self._files.get(path)
            if content is None:
                responses.append(
                    FileDownloadResponse(
                        path=path, content=None, error="file_not_found"
                    )
                )
            else:
                responses.append(
                    FileDownloadResponse(path=path, content=content, error=None)
                )
        return responses


class FakeSandboxProvider:
    """Fake ``SandboxProviderPort`` with idempotent create + owner tracking."""

    def __init__(self, *, session_ttl_seconds: int = 15 * 60) -> None:
        self._ttl = session_ttl_seconds
        self._by_idempotency: dict[str, SandboxHandle] = {}
        self._by_ref: dict[str, ManagedSandboxSession] = {}
        self.terminated_refs: list[str] = []
        self.create_calls = 0

    async def create(self, request: SandboxCreateRequest) -> SandboxHandle:
        self.create_calls += 1
        existing = self._by_idempotency.get(request.idempotency_key)
        if existing is not None:
            return existing
        if request.egress.mode != "deny_all":
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Egress allowlists are not supported by the fake provider.",
            )
        if request.secret_refs:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Secret leases are not supported by the fake provider.",
            )
        ref = f"fake-{request.idempotency_key}"
        now = _utcnow()
        session = ManagedSandboxSession(
            session_id=request.run_id,
            provider=SandboxProviderId.LANGSMITH,
            provider_session_ref=ref,
            owner_tag=request.owner_tag,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
            cleanup_state="active",
        )
        handle = SandboxHandle(session=session, backend=FakeSandboxBackend(ref))
        self._by_idempotency[request.idempotency_key] = handle
        self._by_ref[ref] = session
        return handle

    async def status(self, provider_session_ref: str) -> ManagedSandboxSession:
        session = self._by_ref.get(provider_session_ref)
        if session is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SESSION_EXPIRED,
                "No such sandbox session.",
            )
        return session

    async def terminate(self, provider_session_ref: str) -> None:
        self.terminated_refs.append(provider_session_ref)
        session = self._by_ref.get(provider_session_ref)
        if session is not None:
            self._by_ref[provider_session_ref] = session.with_state("deleted")

    async def list_owned_sessions(
        self, owner_tag: str
    ) -> tuple[ManagedSandboxSession, ...]:
        return tuple(
            session
            for session in self._by_ref.values()
            if session.owner_tag == owner_tag and session.cleanup_state != "deleted"
        )


class FailingTerminateProvider(FakeSandboxProvider):
    """Fake provider whose terminate always fails (drives cleanup_pending)."""

    async def terminate(self, provider_session_ref: str) -> None:
        raise RuntimeError("provider terminate failed")


def make_request(
    *,
    run_id: str = "run-1",
    owner_tag: str = "owner-a",
    idempotency_key: str = "idem-1",
    egress_mode: str = "deny_all",
) -> SandboxCreateRequest:
    """Build a minimal valid :class:`SandboxCreateRequest` for tests."""

    from agent_runtime.capabilities.sandbox.contracts import (
        SandboxEgressPolicy,
        WorkspaceTransferManifest,
    )

    zero_sha = "0" * 64
    manifest = WorkspaceTransferManifest(
        workspace_id="ws-1",
        root_grant_id="grant-1",
        entries=(),
        total_bytes=0,
        manifest_sha256=zero_sha,
    )
    return SandboxCreateRequest(
        run_id=run_id,
        workspace_snapshot=manifest,
        egress=SandboxEgressPolicy(mode=egress_mode),  # type: ignore[arg-type]
        secret_refs=(),
        limit_profile="desktop_v1",
        approval_id="approval-1",
        owner_tag=owner_tag,
        idempotency_key=idempotency_key,
    )
