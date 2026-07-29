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
_WINDOWS_REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT


class McpRevisionCursorStoreUnsupported(RuntimeError):
    """The host provides neither the POSIX nor Windows desktop strategy."""


class DesktopFilesystemMcpRevisionCursorStore:
    """Filesystem cursor adapter rooted beneath ``RUNTIME_FILE_STORE_ROOT``.

    POSIX cursor records are reached only through a directory descriptor. This
    avoids a validate-then-use pathname window for reads, writes, and deletion.
    Windows follows the desktop file store's single-OS-user trust boundary:
    final path components are checked for symlink/reparse points and writes use
    exclusive temporary files plus ``os.replace``. It deliberately does not
    claim POSIX descriptor-relative race guarantees. Persistent filenames are
    SHA-256 digests of subject identity and never contain tenant or user IDs.
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
        self._strategy = self._platform_strategy()
        if self._strategy is None:
            raise McpRevisionCursorStoreUnsupported(
                "MCP revision cursor storage requires POSIX directory-fd support "
                "or Windows desktop filesystem support"
            )
        self._root = Path(configured_root).absolute()
        self._max_bytes = max_bytes
        # Cursor bytes and record bytes are separate bounds. A valid UTF-8
        # cursor may require JSON escaping (for example control characters),
        # so the envelope needs a bounded but wider allowance.
        self._max_record_bytes = (max_bytes * 6) + 64
        self._guard = asyncio.Lock()

    @classmethod
    def _platform_strategy(cls) -> str | None:
        if cls._supports_descriptor_operations():
            return "posix"
        return "windows" if os.name == "nt" else None

    @staticmethod
    def _supports_descriptor_operations() -> bool:
        return (
            os.name == "posix"
            and hasattr(os, "O_DIRECTORY")
            and hasattr(os, "O_NOFOLLOW")
            and os.open in os.supports_dir_fd
            and os.rename in os.supports_dir_fd
        )

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
    def _is_reparse_or_symlink(entry_stat: os.stat_result) -> bool:
        return stat.S_ISLNK(entry_stat.st_mode) or bool(
            getattr(entry_stat, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
        )

    def _open_windows_cursor_directory(self) -> Path:
        """Create/check private desktop storage without POSIX race claims."""

        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_stat = self._root.lstat()
            if self._is_reparse_or_symlink(root_stat) or not stat.S_ISDIR(
                root_stat.st_mode
            ):
                raise McpRevisionCursorStoreError("cursor root is unsafe")
            self._root.chmod(0o700)
            directory = self._root / _CURSOR_DIRECTORY
            directory.mkdir(mode=0o700, exist_ok=True)
            directory_stat = directory.lstat()
            if self._is_reparse_or_symlink(directory_stat) or not stat.S_ISDIR(
                directory_stat.st_mode
            ):
                raise McpRevisionCursorStoreError("cursor directory is unsafe")
            directory.chmod(0o700)
            return directory
        except OSError as exc:
            raise McpRevisionCursorStoreError(
                "cursor root is unsafe or unavailable"
            ) from exc

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

    def _assert_windows_regular_entry(self, path: Path) -> bool:
        try:
            entry_stat = path.lstat()
        except FileNotFoundError:
            return False
        if self._is_reparse_or_symlink(entry_stat) or not stat.S_ISREG(
            entry_stat.st_mode
        ):
            raise McpRevisionCursorStoreError("cursor entry is not a regular file")
        return True

    async def load(self, subject: McpRevisionSubject) -> str | None:
        if self._strategy == "windows":
            return await self._load_windows(subject)
        return await self._load_posix(subject)

    async def _load_posix(self, subject: McpRevisionSubject) -> str | None:
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
                        or file_stat.st_mode & 0o077
                        or file_stat.st_size > self._max_record_bytes
                    ):
                        raise McpRevisionCursorStoreError(
                            "cursor file is invalid or too large"
                        )
                    chunks: list[bytes] = []
                    remaining = self._max_record_bytes + 1
                    while remaining:
                        chunk = os.read(file_fd, remaining)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    raw = b"".join(chunks)
                finally:
                    os.close(file_fd)
            finally:
                os.close(directory_fd)
        if len(raw) > self._max_record_bytes:
            raise McpRevisionCursorStoreError("cursor file is too large")
        return self._decode_cursor(raw)

    async def _load_windows(self, subject: McpRevisionSubject) -> str | None:
        name = self._filename(subject)
        async with self._guard:
            directory = self._open_windows_cursor_directory()
            path = directory / name
            if not self._assert_windows_regular_entry(path):
                return None
            try:
                # Windows cannot express the POSIX ``dir_fd`` + ``O_NOFOLLOW``
                # open here. A same-OS-user mutation can race this lstat→open
                # check; desktop's private-root single-user boundary is the
                # explicit trust assumption for this weaker strategy.
                with path.open("rb") as file:
                    file_stat = os.fstat(file.fileno())
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise McpRevisionCursorStoreError("cursor entry is not regular")
                    raw = file.read(self._max_record_bytes + 1)
            except OSError as exc:
                raise McpRevisionCursorStoreError(
                    "could not safely open cursor"
                ) from exc
        if len(raw) > self._max_record_bytes:
            raise McpRevisionCursorStoreError("cursor file is too large")
        return self._decode_cursor(raw)

    def _decode_cursor(self, raw: bytes) -> str:
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
        if len(cursor.encode("utf-8")) > self._max_bytes:
            raise McpRevisionCursorStoreError("cursor value is too large")
        return cursor

    async def save(self, subject: McpRevisionSubject, cursor: str) -> None:
        validate_mcp_revision_cursor(cursor)
        if len(cursor.encode("utf-8")) > self._max_bytes:
            raise McpRevisionCursorStoreError("cursor is too large")
        encoded = json.dumps(
            {"cursor": cursor}, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if len(encoded) > self._max_record_bytes:
            raise McpRevisionCursorStoreError("cursor record is too large")
        if self._strategy == "windows":
            await self._save_windows(subject, encoded)
            return
        await self._save_posix(subject, encoded)

    async def _save_posix(self, subject: McpRevisionSubject, encoded: bytes) -> None:
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

    async def _save_windows(self, subject: McpRevisionSubject, encoded: bytes) -> None:
        name = self._filename(subject)
        temporary = f".{name}.{secrets.token_hex(16)}.tmp"
        async with self._guard:
            directory = self._open_windows_cursor_directory()
            target = directory / name
            temp_path = directory / temporary
            file_fd = -1
            try:
                self._assert_windows_regular_entry(target)
                file_fd = os.open(
                    str(temp_path),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                    0o600,
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
                # ``replace`` changes the directory entry itself; it does not
                # follow a target reparse point. Windows has no portable
                # directory fsync in Python, so this is intentionally weaker
                # than the POSIX descriptor-relative durability boundary. A
                # same-user replacement can still race the earlier lstat check.
                os.replace(temp_path, target)
            except OSError as exc:
                raise McpRevisionCursorStoreError(
                    "could not durably save cursor"
                ) from exc
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    async def clear(self, subject: McpRevisionSubject) -> None:
        if self._strategy == "windows":
            await self._clear_windows(subject)
            return
        await self._clear_posix(subject)

    async def _clear_posix(self, subject: McpRevisionSubject) -> None:
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

    async def _clear_windows(self, subject: McpRevisionSubject) -> None:
        name = self._filename(subject)
        async with self._guard:
            directory = self._open_windows_cursor_directory()
            path = directory / name
            try:
                if not self._assert_windows_regular_entry(path):
                    return
                # Same-user path replacement can race the lstat above; this is
                # within the desktop file-store trust boundary, not a POSIX
                # descriptor-relative safety claim.
                path.unlink()
            except OSError as exc:
                raise McpRevisionCursorStoreError(
                    "could not safely clear cursor"
                ) from exc
