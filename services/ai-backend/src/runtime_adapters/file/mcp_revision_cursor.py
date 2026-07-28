"""Durable, symlink-safe desktop cursor storage for MCP revision feeds."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from agent_runtime.capabilities.mcp.revision_feed import (
    MCP_REVISION_CURSOR_MAX_BYTES,
    McpRevisionCursorStoreError,
    McpRevisionSubject,
    validate_mcp_revision_cursor,
)

_CURSOR_DIRECTORY = "mcp-revision-cursors"


class DesktopFilesystemMcpRevisionCursorStore:
    """Filesystem cursor adapter rooted beneath ``RUNTIME_FILE_STORE_ROOT``.

    Cursor records are reached only through a directory descriptor.  This avoids
    a validate-then-use pathname window for reads, writes, and deletion.  The
    persistent filenames are SHA-256 digests of subject identity and never
    contain tenant or user IDs.
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        max_bytes: int = MCP_REVISION_CURSOR_MAX_BYTES,
    ) -> None:
        configured_root = (
            root if root is not None else os.environ.get("RUNTIME_FILE_STORE_ROOT")
        )
        if not configured_root:
            raise ValueError(
                "RUNTIME_FILE_STORE_ROOT is required for filesystem cursors"
            )
        if max_bytes <= 0 or max_bytes > MCP_REVISION_CURSOR_MAX_BYTES:
            raise ValueError("max_bytes must be between 1 and 1024")
        self._root = Path(configured_root).absolute()
        self._max_bytes = max_bytes
        self._guard = asyncio.Lock()

    @staticmethod
    def _directory_flags() -> int:
        return (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )

    def _open_cursor_directory(self) -> int:
        """Open the dedicated directory without ever following its final link."""

        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_fd = os.open(self._root, self._directory_flags())
        except OSError as exc:
            raise McpRevisionCursorStoreError(
                "cursor root is unsafe or unavailable"
            ) from exc
        try:
            root_stat = os.fstat(root_fd)
            if not stat.S_ISDIR(root_stat.st_mode):
                raise McpRevisionCursorStoreError("cursor root is not a directory")
            os.fchmod(root_fd, 0o700)
            try:
                os.mkdir(_CURSOR_DIRECTORY, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            cursor_fd = os.open(
                _CURSOR_DIRECTORY, self._directory_flags(), dir_fd=root_fd
            )
        except OSError as exc:
            raise McpRevisionCursorStoreError("cursor directory is unsafe") from exc
        finally:
            os.close(root_fd)
        try:
            cursor_stat = os.fstat(cursor_fd)
            if not stat.S_ISDIR(cursor_stat.st_mode):
                raise McpRevisionCursorStoreError("cursor directory is not a directory")
            os.fchmod(cursor_fd, 0o700)
            return cursor_fd
        except Exception:
            os.close(cursor_fd)
            raise

    @staticmethod
    def _filename(subject: McpRevisionSubject) -> str:
        digest = hashlib.sha256(
            f"{subject.org_id}\0{subject.user_id}".encode("utf-8")
        ).hexdigest()
        return f"mcp-revision-{digest}.cursor"

    @staticmethod
    def _assert_regular_entry(name: str, directory_fd: int) -> bool:
        """Return whether the entry exists; reject every non-regular type."""

        try:
            entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode):
            raise McpRevisionCursorStoreError("cursor entry is not a regular file")
        if entry_stat.st_mode & 0o077:
            raise McpRevisionCursorStoreError(
                "cursor file permissions are not restrictive"
            )
        return True

    async def load(self, subject: McpRevisionSubject) -> str | None:
        name = self._filename(subject)
        async with self._guard:
            directory_fd = self._open_cursor_directory()
            try:
                if not self._assert_regular_entry(name, directory_fd):
                    return None
                try:
                    file_fd = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise McpRevisionCursorStoreError(
                        "could not safely open cursor"
                    ) from exc
                try:
                    file_stat = os.fstat(file_fd)
                    if (
                        not stat.S_ISREG(file_stat.st_mode)
                        or file_stat.st_size > self._max_bytes
                    ):
                        raise McpRevisionCursorStoreError(
                            "cursor file is invalid or too large"
                        )
                    raw = os.read(file_fd, self._max_bytes + 1)
                finally:
                    os.close(file_fd)
            finally:
                os.close(directory_fd)
        if len(raw) > self._max_bytes:
            raise McpRevisionCursorStoreError("cursor file is too large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpRevisionCursorStoreError("cursor file is corrupt") from exc
        if not isinstance(payload, dict) or set(payload) != {"cursor"}:
            raise McpRevisionCursorStoreError("cursor file has an invalid schema")
        cursor = payload["cursor"]
        if not isinstance(cursor, str):
            raise McpRevisionCursorStoreError("cursor is not a string")
        try:
            validate_mcp_revision_cursor(cursor)
        except ValueError as exc:
            raise McpRevisionCursorStoreError("cursor value is invalid") from exc
        return cursor

    async def save(self, subject: McpRevisionSubject, cursor: str) -> None:
        validate_mcp_revision_cursor(cursor)
        encoded = json.dumps({"cursor": cursor}, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self._max_bytes:
            raise McpRevisionCursorStoreError("cursor is too large")
        name = self._filename(subject)
        temporary = f".{name}.{secrets.token_hex(16)}.tmp"
        async with self._guard:
            directory_fd = self._open_cursor_directory()
            file_fd = -1
            try:
                self._assert_regular_entry(name, directory_fd)
                file_fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                written = 0
                while written < len(encoded):
                    count = os.write(file_fd, encoded[written:])
                    if count <= 0:
                        raise OSError("short cursor write")
                    written += count
                os.fsync(file_fd)
                os.close(file_fd)
                file_fd = -1
                os.replace(
                    temporary,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                os.fsync(directory_fd)
            except OSError as exc:
                raise McpRevisionCursorStoreError(
                    "could not durably save cursor"
                ) from exc
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
                finally:
                    os.close(directory_fd)

    async def clear(self, subject: McpRevisionSubject) -> None:
        name = self._filename(subject)
        async with self._guard:
            directory_fd = self._open_cursor_directory()
            try:
                if not self._assert_regular_entry(name, directory_fd):
                    return
                os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError as exc:
                raise McpRevisionCursorStoreError(
                    "could not safely clear cursor"
                ) from exc
            finally:
                os.close(directory_fd)
