"""Read-only Deep Agents backend exposing user-granted host folders as ``/workspace/``.

AC5 slice 3a — the ai-backend side. This is the adapter that lets the agent
READ user-granted host folders by translating Deep Agents ``BackendProtocol``
file operations into authenticated calls to the Electron capability broker
(``DesktopBrokerClient``).

Virtual path model
------------------
The agent sees a virtual filesystem rooted at ``/workspace/``. The first path
segment selects a **mount** — a named binding to one broker ``grant_id`` — and
the remainder is a path *relative to that grant's host root*::

    /workspace/<mount>/<relative/path>   →   grant_id=<mount.grant_id>, path="<relative/path>"

Only mount names and root-relative virtual paths ever cross to the broker; a
host-absolute path is never constructed or sent. When this backend is routed by
Deep Agents' ``CompositeBackend`` under the ``/workspace/`` prefix, the prefix
is stripped before delegation, so paths arrive here as ``/<mount>/...``. We also
accept the un-stripped ``/workspace/<mount>/...`` form for direct callers/tests.

Read-only
---------
``ls`` / ``read`` / ``glob`` / ``grep`` (and their async twins) are implemented
against the broker. Every **mutating** method (``write`` / ``edit`` /
``upload_files``) raises :class:`WorkspaceWriteNotSupportedError` — host writes
are a later slice, and this route must never mutate a user's disk.

Integration seam
----------------
``build_workspace_backend(config)`` is the single entry point the runtime
factory wiring (a separate follow-up) will call. It returns ``None`` when broker
config is absent (non-desktop deployments), so nothing changes off the desktop
path.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, cast

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
    BrokerError,
    BrokerNotADirectoryError,
    BrokerNotAFileError,
    BrokerNotFoundError,
    BrokerPermissionDeniedError,
    DesktopBrokerClient,
    FsDirEntry,
    FsReadResult,
)

#: Deep Agents ``CompositeBackend`` route prefix this backend is mounted under.
#: The factory follow-up registers ``{ROUTE_PREFIX: backend}``; kept here as the
#: single source of truth so wiring and path handling cannot drift.
ROUTE_PREFIX: Final = "/workspace/"

#: Default per-read byte window fetched from the broker. Deep Agents ``read``
#: slices by *line*, so we pull a bounded byte window from offset 0 and slice
#: locally. Matches the broker's own default read cap (1 MiB).
DEFAULT_READ_MAX_BYTES: Final = 1 * 1024 * 1024


class _Env:
    """Environment variable names carrying broker connection config."""

    BROKER_URL: Final = "DESKTOP_BROKER_URL"
    BROKER_TOKEN: Final = "DESKTOP_BROKER_TOKEN"
    BROKER_PROTOCOL: Final = "DESKTOP_BROKER_PROTOCOL"


class _Encoding:
    """Deep Agents ``FileData`` content encodings."""

    UTF8: Final = "utf-8"
    BASE64: Final = "base64"


class _SafeMessage:
    """Generic, safe error strings returned to the model (never a host path)."""

    NOT_FOUND: Final = "The requested workspace path was not found."
    NOT_A_DIRECTORY: Final = "The requested workspace path is not a directory."
    NOT_A_FILE: Final = "The requested workspace path is not a regular file."
    IS_A_DIRECTORY: Final = "The requested workspace path is a directory, not a file."
    PERMISSION_DENIED: Final = "Access to the requested workspace path was denied."
    UNAVAILABLE: Final = "The workspace is temporarily unavailable."


class WorkspaceWriteNotSupportedError(RuntimeError):
    """Raised by every mutating method — ``/workspace/`` is read-only in slice 3a."""

    MESSAGE: Final = (
        "Writing to /workspace/ is not supported in slice 3a — host access is "
        "read-only. Use a draft (/drafts/) for authored content instead."
    )

    def __init__(self, message: str | None = None) -> None:
        """Store the fixed read-only message."""
        super().__init__(message or self.MESSAGE)


@dataclass(frozen=True)
class WorkspaceMount:
    """A named binding from a virtual mount segment to one broker ``grant_id``.

    ``name`` is the first virtual-path segment the agent uses (e.g.
    ``project-notes``); it must be a single path segment (no ``/``). ``label``
    is an optional human hint carried for future presentation — it is never sent
    to the broker.
    """

    name: str
    grant_id: str
    label: str | None = None

    def __post_init__(self) -> None:
        """Reject empty or separator-bearing mount names — they must be one segment."""
        if not self.name or "/" in self.name or "\\" in self.name:
            msg = "workspace mount name must be a single non-empty path segment"
            raise ValueError(msg)
        if not self.grant_id:
            msg = "workspace mount grant_id must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True)
class _Resolution:
    """A virtual path resolved to a concrete mount + grant-relative path."""

    mount: WorkspaceMount
    relative: str  # POSIX, no leading slash; "" denotes the mount root


class _WorkspaceRootError(Exception):
    """Internal signal: the path refers to the ``/workspace/`` root itself."""


class _UnknownMountError(Exception):
    """Internal signal: the leading segment names no configured mount."""


class BrokeredWorkspaceBackend(BackendProtocol):
    """Deep Agents ``BackendProtocol`` translating reads into broker ``/v1/fs/*`` calls.

    Method → broker route mapping (READ ONLY):

    * ``ls`` / ``als``      → ``/v1/fs/list`` (root lists the configured mounts)
    * ``read`` / ``aread``  → ``/v1/fs/read`` (byte window → line slice / base64)
    * ``glob`` / ``aglob``  → ``/v1/fs/glob``
    * ``grep`` / ``agrep``  → ``/v1/fs/grep`` (literal substring, per Deep Agents)

    ``write`` / ``edit`` / ``upload_files`` raise
    :class:`WorkspaceWriteNotSupportedError`.
    """

    PATH_PREFIX: str = ROUTE_PREFIX

    #: Path shapes that denote the workspace root (mount listing).
    _ROOT_PATHS: Final = frozenset({"", "/", "/workspace", "/workspace/"})

    def __init__(
        self,
        *,
        client: DesktopBrokerClient,
        mounts: Sequence[WorkspaceMount],
        read_max_bytes: int = DEFAULT_READ_MAX_BYTES,
    ) -> None:
        """Bind the backend to a broker client and its configured mounts."""
        self._client = client
        self._read_max_bytes = read_max_bytes
        by_name: dict[str, WorkspaceMount] = {}
        for mount in mounts:
            if mount.name in by_name:
                msg = f"duplicate workspace mount name: {mount.name!r}"
                raise ValueError(msg)
            by_name[mount.name] = mount
        self._mounts: Mapping[str, WorkspaceMount] = by_name

    # --- BackendProtocol: list ---------------------------------------------

    def ls(self, path: str) -> LsResult:
        """Synchronous directory listing (delegates to :meth:`als`)."""
        return _run_sync(self.als(path))

    async def als(self, path: str) -> LsResult:
        """List the mounts (at root) or a directory's children under a mount."""
        try:
            resolution = self._resolve(path)
        except _WorkspaceRootError:
            entries = [self._mount_dir_entry(m) for m in self._mounts.values()]
            return LsResult(entries=entries)
        except _UnknownMountError:
            return LsResult(error=_SafeMessage.NOT_FOUND)
        try:
            result = await self._client.list(
                resolution.mount.grant_id, resolution.relative
            )
        except BrokerError as exc:
            return LsResult(error=self._safe_message(exc))
        entries = [
            self._entry_to_file_info(resolution, entry) for entry in result.entries
        ]
        return LsResult(entries=entries)

    # --- BackendProtocol: read ---------------------------------------------

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Synchronous file read (delegates to :meth:`aread`)."""
        return _run_sync(self.aread(file_path, offset, limit))

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        """Read a grant-relative file, slicing text by line (base64 for binary)."""
        try:
            resolution = self._resolve(file_path)
        except _WorkspaceRootError:
            return ReadResult(error=_SafeMessage.IS_A_DIRECTORY)
        except _UnknownMountError:
            return ReadResult(error=_SafeMessage.NOT_FOUND)
        if not resolution.relative:
            # The mount root is a directory, not a file.
            return ReadResult(error=_SafeMessage.IS_A_DIRECTORY)
        try:
            result = await self._client.read(
                resolution.mount.grant_id,
                resolution.relative,
                max_bytes=self._read_max_bytes,
            )
        except BrokerError as exc:
            return ReadResult(error=self._safe_message(exc))
        return self._decode_read(result, offset, limit)

    # --- BackendProtocol: glob ---------------------------------------------

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Synchronous glob (delegates to :meth:`aglob`)."""
        return _run_sync(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Match ``pattern`` under the addressed mount, or across all mounts at root."""
        matches: list[FileInfo] = []
        for mount, relative in self._targets(path):
            scoped = self._scoped_glob(relative, pattern)
            try:
                result = await self._client.glob(mount.grant_id, scoped)
            except BrokerError as exc:
                return GlobResult(error=self._safe_message(exc))
            matches.extend(self._match_file_info(mount, p) for p in result.paths)
        return GlobResult(matches=matches)

    # --- BackendProtocol: grep ---------------------------------------------

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Synchronous grep (delegates to :meth:`agrep`)."""
        return _run_sync(self.agrep(pattern, path, glob))

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Literal-substring content search under the addressed mount(s)."""
        matches: list[GrepMatch] = []
        for mount, relative in self._targets(path):
            path_glob = self._scoped_path_glob(relative, glob)
            try:
                result = await self._client.grep(
                    mount.grant_id, pattern, path_glob=path_glob
                )
            except BrokerError as exc:
                return GrepResult(error=self._safe_message(exc))
            matches.extend(self._hit_to_match(mount, hit) for hit in result.hits)
        return GrepResult(matches=matches)

    # --- BackendProtocol: mutating ops (read-only route → always raise) -----

    def write(self, file_path: str, content: str) -> WriteResult:
        """Reject writes — ``/workspace/`` is read-only in slice 3a."""
        raise WorkspaceWriteNotSupportedError

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Reject writes — ``/workspace/`` is read-only in slice 3a."""
        raise WorkspaceWriteNotSupportedError

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Reject edits — ``/workspace/`` is read-only in slice 3a."""
        raise WorkspaceWriteNotSupportedError

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Reject edits — ``/workspace/`` is read-only in slice 3a."""
        raise WorkspaceWriteNotSupportedError

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        """Reject uploads — ``/workspace/`` is read-only in slice 3a."""
        raise WorkspaceWriteNotSupportedError

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        """Reject uploads — ``/workspace/`` is read-only in slice 3a."""
        raise WorkspaceWriteNotSupportedError

    # --- path resolution ----------------------------------------------------

    def _resolve(self, path: str) -> _Resolution:
        """Resolve a virtual path to ``(mount, relative)``.

        Raises :class:`_WorkspaceRootError` for the workspace root and
        :class:`_UnknownMountError` when the leading segment names no mount.
        """
        segments = self._split(path)
        if not segments:
            raise _WorkspaceRootError
        name = segments[0]
        mount = self._mounts.get(name)
        if mount is None:
            raise _UnknownMountError(name)
        return _Resolution(mount=mount, relative="/".join(segments[1:]))

    @classmethod
    def _split(cls, path: str | None) -> list[str]:
        """Strip an optional ``/workspace`` prefix and split into clean segments."""
        raw = path or ""
        if raw == "/workspace":
            raw = ""
        elif raw.startswith("/workspace/"):
            raw = raw[len("/workspace/") :]
        return [segment for segment in raw.split("/") if segment]

    def _is_root(self, path: str | None) -> bool:
        """True when ``path`` denotes the workspace root (mount listing)."""
        return (path or "") in self._ROOT_PATHS

    def _targets(self, path: str | None) -> list[tuple[WorkspaceMount, str]]:
        """Resolve a glob/grep ``path`` to the ``(mount, relative)`` pairs to scan.

        ``None`` / root fans out across every mount; a mount-scoped path narrows
        to one; an unknown mount yields no targets (an empty match set).
        """
        if path is None or self._is_root(path):
            return [(mount, "") for mount in self._mounts.values()]
        try:
            resolution = self._resolve(path)
        except _WorkspaceRootError:
            return [(mount, "") for mount in self._mounts.values()]
        except _UnknownMountError:
            return []
        return [(resolution.mount, resolution.relative)]

    # --- projection helpers -------------------------------------------------

    @staticmethod
    def _mount_dir_entry(mount: WorkspaceMount) -> FileInfo:
        """A workspace-root listing entry for one mount (a virtual directory)."""
        return cast("FileInfo", {"path": f"/{mount.name}/", "is_dir": True})

    @staticmethod
    def _entry_to_file_info(resolution: _Resolution, entry: FsDirEntry) -> FileInfo:
        """Map a broker dir entry to a route-relative ``FileInfo``.

        Paths are relative to THIS backend's root (``/<mount>/...``) so the
        wrapping ``CompositeBackend`` re-prepends ``/workspace`` correctly.
        """
        child = (
            f"{resolution.relative}/{entry.name}" if resolution.relative else entry.name
        )
        is_dir = entry.type == "dir"
        path = f"/{resolution.mount.name}/{child}"
        if is_dir:
            path += "/"
        return cast("FileInfo", {"path": path, "is_dir": is_dir})

    @staticmethod
    def _match_file_info(mount: WorkspaceMount, relative_path: str) -> FileInfo:
        """Map a broker glob path (root-relative) to a route-relative ``FileInfo``."""
        return cast(
            "FileInfo",
            {"path": f"/{mount.name}/{relative_path}", "is_dir": False},
        )

    @staticmethod
    def _hit_to_match(mount: WorkspaceMount, hit: object) -> GrepMatch:
        """Map a broker grep hit to a Deep Agents ``GrepMatch`` (preview → text)."""
        return cast(
            "GrepMatch",
            {
                "path": f"/{mount.name}/{hit.path}",  # type: ignore[attr-defined]
                "line": hit.line,  # type: ignore[attr-defined]
                "text": hit.preview,  # type: ignore[attr-defined]
            },
        )

    @staticmethod
    def _scoped_glob(relative_dir: str, pattern: str) -> str:
        """Scope a glob pattern under a mount subdirectory (broker globs from root)."""
        return f"{relative_dir}/{pattern}" if relative_dir else pattern

    @staticmethod
    def _scoped_path_glob(relative_dir: str, glob: str | None) -> str | None:
        """Combine a mount subdirectory and an optional file glob into a broker ``path_glob``."""
        if relative_dir and glob:
            return f"{relative_dir}/{glob}"
        if relative_dir:
            return f"{relative_dir}/**"
        return glob

    def _decode_read(self, result: FsReadResult, offset: int, limit: int) -> ReadResult:
        """Decode a broker byte window into a Deep Agents ``ReadResult``.

        Bytes that decode as UTF-8 are treated as text and sliced by line
        (``offset`` lines in, up to ``limit`` lines) exactly as Deep Agents'
        ``FilesystemBackend`` does — line-number formatting is the middleware's
        job. Non-UTF-8 bytes are returned base64-encoded (no line slicing).
        """
        try:
            raw = (
                base64.b64decode(result.base64, validate=True) if result.base64 else b""
            )
        except (binascii.Error, ValueError):
            return ReadResult(error=_SafeMessage.UNAVAILABLE)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Binary content — hand back the base64 window unsliced.
            return ReadResult(
                file_data={"content": result.base64, "encoding": _Encoding.BASE64}
            )
        lines = text.splitlines(keepends=True)
        if not lines:
            return ReadResult(file_data={"content": "", "encoding": _Encoding.UTF8})
        start = offset if offset > 0 else 0
        if start >= len(lines):
            return ReadResult(
                error=f"Line offset {offset} exceeds file length ({len(lines)} lines)"
            )
        end = min(start + limit, len(lines)) if limit >= 0 else len(lines)
        return ReadResult(
            file_data={
                "content": "".join(lines[start:end]),
                "encoding": _Encoding.UTF8,
            }
        )

    @staticmethod
    def _safe_message(exc: BrokerError) -> str:
        """Map a broker exception to a safe, model-facing message (no host path)."""
        if isinstance(exc, BrokerNotFoundError):
            return _SafeMessage.NOT_FOUND
        if isinstance(exc, BrokerNotADirectoryError):
            return _SafeMessage.NOT_A_DIRECTORY
        if isinstance(exc, BrokerNotAFileError):
            return _SafeMessage.NOT_A_FILE
        if isinstance(exc, BrokerPermissionDeniedError):
            return _SafeMessage.PERMISSION_DENIED
        # grant_required, unsupported, protocol, unavailable, invalid_* → generic.
        return _SafeMessage.UNAVAILABLE


# --- integration seam --------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceBackendConfig:
    """Config for :func:`build_workspace_backend`.

    ``broker_base_url`` + ``broker_token`` gate construction: when either is
    absent the seam returns ``None`` and no ``/workspace/`` route is created, so
    non-desktop deployments are wholly unaffected. ``mounts`` are supplied by the
    caller (the factory follow-up resolves them from the run's active grant
    snapshot) — this seam performs no network I/O at construction time.
    """

    broker_base_url: str | None = None
    broker_token: str | None = None
    protocol_version: str = "1"
    timeout_seconds: float = 10.0
    read_max_bytes: int = DEFAULT_READ_MAX_BYTES
    mounts: tuple[WorkspaceMount, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(
        cls,
        *,
        mounts: Sequence[WorkspaceMount] = (),
        env: Mapping[str, str] | None = None,
    ) -> WorkspaceBackendConfig:
        """Build config from ``DESKTOP_BROKER_URL`` / ``DESKTOP_BROKER_TOKEN`` (+ mounts)."""
        source = env if env is not None else os.environ
        return cls(
            broker_base_url=source.get(_Env.BROKER_URL) or None,
            broker_token=source.get(_Env.BROKER_TOKEN) or None,
            protocol_version=source.get(_Env.BROKER_PROTOCOL) or "1",
            mounts=tuple(mounts),
        )


def build_workspace_backend(
    config: WorkspaceBackendConfig,
) -> BrokeredWorkspaceBackend | None:
    """Construct the ``/workspace/`` backend, or ``None`` when broker config is absent.

    This is the ONE seam the runtime factory wiring (a separate follow-up) will
    call, e.g. registering ``{ROUTE_PREFIX: build_workspace_backend(cfg)}`` into
    the ``CompositeBackend`` routes only when the result is not ``None``. It is
    intentionally synchronous and does no network I/O — mounts are passed in.
    """
    if not config.broker_base_url or not config.broker_token:
        return None
    client = DesktopBrokerClient(
        BrokerClientConfig(
            base_url=config.broker_base_url,
            token=config.broker_token,
            protocol_version=config.protocol_version,
            timeout_seconds=config.timeout_seconds,
        )
    )
    return BrokeredWorkspaceBackend(
        client=client,
        mounts=config.mounts,
        read_max_bytes=config.read_max_bytes,
    )


def _run_sync(coro: object) -> object:
    """Block on an async coroutine for ``BackendProtocol``'s sync API surface.

    Deep Agents' worker calls the async ``a*`` methods; the sync entry points
    exist only for the framework's legacy dispatch path. We dispatch back to the
    async implementation rather than duplicating logic.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(cast("object", coro))  # type: ignore[arg-type]
    return asyncio.run_coroutine_threadsafe(cast("object", coro), loop).result()  # type: ignore[arg-type]
