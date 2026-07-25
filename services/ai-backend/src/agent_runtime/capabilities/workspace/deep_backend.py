"""Deep Agents backend for C1 overlay reads and C3 staged workspace writes."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Coroutine
from typing import Any, cast

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileData,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.capabilities.operations.context import OperationRequestFactory
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.workspace.contracts import (
    WorkspaceEntryKind,
    normalize_virtual_path,
)
from agent_runtime.capabilities.workspace.effects import (
    WorkspaceGrantBinding,
    WorkspaceOperationAdapter,
    staged_disposition_message,
)
from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend

_UNAVAILABLE = (
    "Local workspace access is unavailable. Create an artifact or download "
    "instead; no local file was changed."
)


class WorkspaceGatewayBackend(BackendProtocol):
    """Route all mutations through A3 and expose C1 read-your-writes."""

    uses_effect_staging = True
    supports_writes = False
    advertise_workspace = True

    def __init__(
        self,
        *,
        merged: MergedWorkspaceBackend,
        gateway: OperationGateway,
        adapter: WorkspaceOperationAdapter,
        grants: tuple[WorkspaceGrantBinding, ...],
    ) -> None:
        self._merged = merged
        self._gateway = gateway
        self._adapter = adapter
        self._grants = grants
        self._mutation_lock = asyncio.Lock()

    def ls(self, path: str) -> LsResult:
        return cast(LsResult, _run_sync(self.als(path)))

    async def als(self, path: str) -> LsResult:
        if path in {"", "/", "/workspace", "/workspace/"}:
            return LsResult(
                entries=[
                    FileInfo(
                        path=f"/workspace/{grant.mount_name}",
                        is_dir=True,
                        size=0,
                    )
                    for grant in sorted(
                        self._grants,
                        key=lambda candidate: candidate.mount_name,
                    )
                    if grant.status == "active"
                ]
            )
        try:
            entries = await self._merged.alist(_virtual_path(path, allow_root=True))
        except Exception:
            return LsResult(error="The requested workspace directory is unavailable.")
        return LsResult(
            entries=[
                FileInfo(
                    path=entry.virtual_path,
                    is_dir=entry.entry_kind is WorkspaceEntryKind.DIRECTORY,
                    size=entry.byte_size or 0,
                )
                for entry in entries
            ]
        )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return cast(ReadResult, _run_sync(self.aread(file_path, offset, limit)))

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        try:
            body = await self._merged.aread_bytes(_virtual_path(file_path))
        except Exception:
            return ReadResult(error="The requested workspace file is unavailable.")
        try:
            text = body.decode("utf-8")
            lines = text.splitlines(keepends=True)
            content = "".join(lines[max(offset, 0) : max(offset, 0) + max(limit, 0)])
            return ReadResult(file_data=FileData(content=content, encoding="utf-8"))
        except UnicodeDecodeError:
            return ReadResult(
                file_data=FileData(
                    content=base64.b64encode(body).decode("ascii"),
                    encoding="base64",
                )
            )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return cast(GlobResult, _run_sync(self.aglob(pattern, path)))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        scoped = _glob_pattern(pattern, path)
        try:
            entries = await self._merged.aglob(scoped)
        except Exception:
            return GlobResult(error="Workspace path search is unavailable.")
        return GlobResult(
            matches=[
                FileInfo(
                    path=entry.virtual_path,
                    is_dir=entry.entry_kind is WorkspaceEntryKind.DIRECTORY,
                    size=entry.byte_size or 0,
                )
                for entry in entries
            ]
        )

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return cast(GrepResult, _run_sync(self.agrep(pattern, path, glob)))

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        del glob
        paths = (_virtual_path(path),) if path else None
        try:
            matches = await self._merged.agrep(pattern, paths)
        except Exception:
            return GrepResult(error="Workspace content search is unavailable.")
        return GrepResult(
            matches=[
                GrepMatch(
                    path=match.virtual_path,
                    line=match.line_number,
                    text=match.line_text,
                )
                for match in matches
            ]
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        return cast(WriteResult, _run_sync(self.awrite(file_path, content)))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        path = _virtual_path(file_path)
        async with self._mutation_lock:
            exists = await self._merged.astat(path)
            op = "create" if exists is None else "replace"
            return await self._write_disposition(
                op=op,
                arguments={"virtual_path": path, "content": content},
            )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return cast(
            EditResult,
            _run_sync(self.aedit(file_path, old_string, new_string, replace_all)),
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        async with self._mutation_lock:
            disposition = await self._invoke(
                op="edit",
                arguments={
                    "virtual_path": _virtual_path(file_path),
                    "old_string": old_string,
                    "new_string": new_string,
                    "replace_all": replace_all,
                },
            )
        return EditResult(error=staged_disposition_message(disposition))

    async def adelete(self, file_path: str) -> WriteResult:
        async with self._mutation_lock:
            return await self._write_disposition(
                op="delete",
                arguments={"virtual_path": _virtual_path(file_path)},
            )

    async def amkdir(self, path: str) -> WriteResult:
        async with self._mutation_lock:
            return await self._write_disposition(
                op="mkdir",
                arguments={"virtual_path": _virtual_path(path)},
            )

    async def amove(self, source: str, destination: str) -> WriteResult:
        async with self._mutation_lock:
            return await self._write_disposition(
                op="move",
                arguments={
                    "source_virtual_path": _virtual_path(source),
                    "destination_virtual_path": _virtual_path(destination),
                },
            )

    async def _write_disposition(
        self, *, op: str, arguments: dict[str, object]
    ) -> WriteResult:
        disposition = await self._invoke(op=op, arguments=arguments)
        return WriteResult(error=staged_disposition_message(disposition))

    async def _invoke(self, *, op: str, arguments: dict[str, object]):
        request = OperationRequestFactory.create(
            capability="workspace",
            op=op,
            arguments=arguments,
        )
        return await self._gateway.invoke(request, self._adapter)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        del files
        return [WriteResult(error=_UNAVAILABLE)]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        return self.upload_files(files)


class WorkspaceTombstoneBackend(BackendProtocol):
    """Always-mounted enforce fallback which prevents StateBackend fallthrough."""

    uses_effect_staging = True
    supports_writes = False
    advertise_workspace = False

    def ls(self, path: str) -> LsResult:
        del path
        return LsResult(error=_UNAVAILABLE)

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        del file_path, offset, limit
        return ReadResult(error=_UNAVAILABLE)

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        return self.read(file_path, offset, limit)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        del pattern, path
        return GlobResult(error=_UNAVAILABLE)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.glob(pattern, path)

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        del pattern, path, glob
        return GrepResult(error=_UNAVAILABLE)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return self.grep(pattern, path, glob)

    def write(self, file_path: str, content: str) -> WriteResult:
        del file_path, content
        return WriteResult(error=_UNAVAILABLE)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        del file_path, old_string, new_string, replace_all
        return EditResult(error=_UNAVAILABLE)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    async def adelete(self, file_path: str) -> WriteResult:
        return self.write(file_path, "")

    async def amkdir(self, path: str) -> WriteResult:
        return self.write(path, "")

    async def amove(self, source: str, destination: str) -> WriteResult:
        del destination
        return self.write(source, "")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        del files
        return [WriteResult(error=_UNAVAILABLE)]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        return self.upload_files(files)


def _virtual_path(path: str | None, *, allow_root: bool = False) -> str:
    raw = path or "/workspace"
    if raw in {"", "/", "/workspace", "/workspace/"}:
        if allow_root:
            return "/workspace"
        return normalize_virtual_path("/workspace/unavailable", allow_mount_root=True)
    candidate = (
        raw if raw.startswith("/workspace/") else f"/workspace/{raw.lstrip('/')}"
    )
    return normalize_virtual_path(candidate, allow_mount_root=allow_root)


def _glob_pattern(pattern: str, path: str | None) -> str:
    if path is None:
        return (
            pattern
            if pattern.startswith("/workspace/")
            else f"/workspace/{pattern.lstrip('/')}"
        )
    root = _virtual_path(path, allow_root=True).rstrip("/")
    return f"{root}/{pattern.lstrip('/')}"


def _run_sync(coro: Coroutine[Any, Any, object]) -> object:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("synchronous workspace methods cannot run in an event loop")


__all__ = ("WorkspaceGatewayBackend", "WorkspaceTombstoneBackend")
