"""``PolicyEnforcedSandboxBackend`` — the sandbox backend the agent actually sees.

The agent never receives a provider client. It receives this façade, which:

* implements the pinned Deep Agents ``SandboxBackendProtocol`` (so DeepAgents
  recognizes it as a sandbox backend and derives fs tools from it);
* delegates command execution and native file transfer to the provider's
  DeepAgents backend;
* enforces a per-session command budget and truncates combined command output
  before returning to the model;
* rejects filesystem paths that leave ``/workspace`` (defense in depth — the
  ``CompositeBackend`` in the factory already routes only ``/workspace/**``
  here, but this backend refuses cross-prefix paths on its own).

Subclassing ``BaseSandbox`` means every filesystem operation (ls/read/write/
edit/grep/glob) is derived from the single policy-wrapped ``execute`` +
``upload_files``/``download_files`` primitives, so the budget and path guards
apply uniformly without re-implementing each fs method.

A ``LocalShellBackend`` or raw host subprocess is never constructed here.
"""

from __future__ import annotations

import time

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
)
from deepagents.backends.sandbox import BaseSandbox

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfile
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
)
from agent_runtime.capabilities.sandbox.workspace_transfer import WORKSPACE_ROOT

_TRUNCATION_NOTE = "\n[sandbox: output truncated to the command preview ceiling]"


class CommandBudget:
    """Mutable per-session command counter with a hard ceiling."""

    def __init__(self, max_commands: int) -> None:
        self._max = max_commands
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    def consume(self) -> None:
        """Charge one command; raise when the ceiling is crossed."""

        if self._used >= self._max:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_COMMAND_BUDGET_EXCEEDED,
                "Sandbox command budget exhausted for this session.",
            )
        self._used += 1


class PolicyEnforcedSandboxBackend(BaseSandbox):
    """Policy façade over a provider sandbox backend."""

    def __init__(
        self,
        *,
        delegate: SandboxBackendProtocol,
        limits: SandboxLimitProfile,
    ) -> None:
        self._delegate = delegate
        self._limits = limits
        self._budget = CommandBudget(limits.commands_per_session)

    @property
    def id(self) -> str:
        """Opaque backend id (the provider session name)."""

        return self._delegate.id

    @property
    def commands_used(self) -> int:
        """Commands charged against the budget so far (for events/tests)."""

        return self._budget.used

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Charge the budget, clamp the timeout, run, and truncate output."""

        self._budget.consume()
        effective_timeout = self._clamp_timeout(timeout)
        started = time.monotonic()
        response = self._delegate.execute(command, timeout=effective_timeout)
        return self._truncate(response, started)

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109 - forwarded semantic param
    ) -> ExecuteResponse:
        """Async execute with the same budget/timeout/truncation policy."""

        self._budget.consume()
        effective_timeout = self._clamp_timeout(timeout)
        started = time.monotonic()
        response = await self._delegate.aexecute(command, timeout=effective_timeout)
        return self._truncate(response, started)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Delegate native upload after guarding every destination path."""

        for path, _ in files:
            self._guard_path(path)
        return self._delegate.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Delegate native download after guarding every source path."""

        for path in paths:
            self._guard_path(path)
        return self._delegate.download_files(paths)

    # -- fs path guards: refuse anything outside /workspace -----------------

    def ls(self, path: str):  # type: ignore[override]
        self._guard_path(path)
        return super().ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000):  # type: ignore[override]
        self._guard_path(file_path)
        return super().read(file_path, offset, limit)

    def write(self, file_path: str, content: str):  # type: ignore[override]
        self._guard_path(file_path)
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ):  # type: ignore[override]  # noqa: FBT001, FBT002
        self._guard_path(file_path)
        return super().edit(file_path, old_string, new_string, replace_all)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None):  # type: ignore[override]
        if path is not None:
            self._guard_path(path)
        return super().grep(pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None):  # type: ignore[override]
        if path is not None:
            self._guard_path(path)
        return super().glob(pattern, path)

    # -- internals ----------------------------------------------------------

    def _guard_path(self, path: str) -> None:
        """Reject any path that is not under ``/workspace`` (cross-prefix guard)."""

        candidate = (path or "").strip()
        if candidate != WORKSPACE_ROOT and not candidate.startswith(
            f"{WORKSPACE_ROOT}/"
        ):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PATH_NOT_ALLOWED,
                "Sandbox path must be under /workspace.",
            )

    def _clamp_timeout(self, timeout: int | None) -> int:
        """Clamp a requested timeout to the profile's command ceiling."""

        ceiling = self._limits.command_timeout_s
        if timeout is None or timeout <= 0:
            return ceiling
        return min(timeout, ceiling)

    def _truncate(self, response: ExecuteResponse, started: float) -> ExecuteResponse:
        """Cap combined output at the profile's command-preview ceiling."""

        ceiling = self._limits.combined_command_preview_bytes
        output = response.output or ""
        encoded = output.encode("utf-8")
        truncated = response.truncated
        if len(encoded) > ceiling:
            output = (
                encoded[:ceiling].decode("utf-8", errors="ignore") + _TRUNCATION_NOTE
            )
            truncated = True
        return ExecuteResponse(
            output=output,
            exit_code=response.exit_code,
            truncated=truncated,
        )
