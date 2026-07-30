r"""Read-only Deep Agents backend exposing user-granted host folders as ``/workspace/``.

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

Host-absolute paths
-------------------
The agent does not only speak in mounts: a user says "read my downloads folder"
and the model calls ``ls`` with ``/Users/<name>/Downloads`` or
``C:\Users\<name>\Downloads``. Such a path is claimed by this backend
(:meth:`BrokeredWorkspaceBackend.claims_path`) so it can never fall through to a
virtual backend that would answer an EMPTY LISTING with SUCCESS — the live defect
:mod:`agent_runtime.capabilities.desktop.host_path` documents.

The broker's security property survives intact because a host-absolute path is an
input to the GRANT flow only, never to the READ flow:

* covered by a grant → resolved locally to ``mount`` + root-relative path and
  served exactly as a virtual path is (the host string stops here);
* not covered → :class:`~agent_runtime.capabilities.desktop.workspace_grant.WorkspaceGrantGate`
  parks the run and asks the user to grant that folder; on approval a mount is
  created and the read proceeds as above;
* traversal, device namespace, reserved name, or a path whose meaning depends on
  host state this process cannot see → refused with a safe message. A refusal is
  never converted into a grant request, so a grant request can never launder an
  escape.

Every one of those outcomes is an explicit answer. None of them is an empty
listing.

Reads
-----
``ls`` / ``read`` / ``glob`` / ``grep`` (and their async twins) are implemented
against the broker's read routes.

Mutation retirement (PRD-E2 D7)
-------------------------------
Direct broker mutation is permanently retired. The mutation methods required by
``BackendProtocol`` fail closed before a network or filesystem call. Canonical
workspace changes are instead staged in the C3 overlay and reach the host only
through C2's separate prepared/attested authority.

Integration seam
----------------
``build_workspace_backend(config)`` is the single entry point the runtime
worker wiring calls. It returns ``None`` when broker config is absent
(non-desktop deployments), so nothing changes off the desktop path.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final, Literal, cast

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
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
    BrokerError,
    BrokerGrant,
    BrokerNotADirectoryError,
    BrokerNotAFileError,
    BrokerNotFoundError,
    BrokerPermissionDeniedError,
    DesktopBrokerClient,
    FsDirEntry,
    FsReadResult,
)
from agent_runtime.capabilities.desktop.host_path import (
    ClassifiedPath,
    HostPathClassifier,
    HostPathKind,
    HostPathMessages,
    HostRootIndex,
)
from agent_runtime.capabilities.desktop.workspace_grant import (
    WorkspaceGrantGate,
    WorkspaceGrantMessages,
)

#: Grant access modes carried for read-side presentation and compatibility.
GrantMode = Literal["read_only", "read_write_no_delete", "read_write"]

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
    SERVICE_IDENTITY: Final = "DESKTOP_LOCAL_SERVICE_IDENTITY"
    BROKER_AUDIENCE: Final = "DESKTOP_WORKSPACE_BROKER_AUDIENCE"


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
    #: Host access is on, but the user has granted nothing yet. Said out loud
    #: rather than answered with an empty listing.
    NO_GRANTS: Final = (
        "No host folders have been shared with this workspace yet, so there is "
        "nothing to list. Ask the user which folder to use and read it by its "
        "full path — they will be asked to grant access."
    )


class WorkspaceWriteNotSupportedError(RuntimeError):
    """Raised for a permanently retired direct workspace mutation."""

    MESSAGE: Final = (
        "Direct writes to /workspace/ are retired. Use a staged workspace "
        "change or author content under /drafts/."
    )

    def __init__(self, message: str | None = None) -> None:
        """Store the fixed read-only message."""
        super().__init__(message or self.MESSAGE)


class WorkspaceMutationSnapshot(BaseModel):
    """Path-free historic pre-image record retained for audit verification.

    Direct workspace mutation no longer creates these records: host changes now
    reach Electron only through the prepared, attested C2 authority. The value
    object remains a stable boundary for historical audit manifests and ensures
    an opaque run capability context can never be serialized into their event
    payloads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["overwrite", "edit"]
    mount: str
    path: str
    object_sha256: str = Field(min_length=64, max_length=64)
    size: int = Field(ge=0)
    run_capability_context: str

    def event_payload(self) -> dict[str, object]:
        """Return the client-safe projection, excluding the authority handle."""

        return {
            "op": self.op,
            "mount": self.mount,
            "path": self.path,
            "object_sha256": self.object_sha256,
            "size": self.size,
        }

    def event_summary(self) -> str:
        """Return the historic timeline label without exposing host paths."""

        return f"Snapshotted {self.path} before {self.op}"


@dataclass(frozen=True)
class WorkspaceMount:
    """A named binding from a virtual mount segment to one broker ``grant_id``.

    ``name`` is the first virtual-path segment the agent uses (e.g.
    ``project-notes``); it must be a single path segment (no ``/``). ``label``
    is an optional human hint carried for future presentation — it is never sent
    to the broker. ``mode`` is retained only for read-side compatibility.

    ``host_root`` is the host-absolute folder this mount stands for, and it is
    **local knowledge only**: it is populated exclusively by the grant flow, from
    the root the user themselves picked, and — like ``label`` — is never sent to
    the broker. Mounts resolved from a broker grant snapshot leave it ``None``,
    because that snapshot is deliberately path-free and must never become a
    host-path oracle. Its only job is to let a host-absolute path be translated
    to ``mount`` + root-relative path inside this process.
    """

    name: str
    grant_id: str
    label: str | None = None
    mode: GrantMode = "read_only"
    host_root: str | None = None

    def __post_init__(self) -> None:
        """Reject empty or separator-bearing mount names — they must be one segment."""
        if not self.name or "/" in self.name or "\\" in self.name:
            msg = "workspace mount name must be a single non-empty path segment"
            raise ValueError(msg)
        if not self.grant_id:
            msg = "workspace mount grant_id must be non-empty"
            raise ValueError(msg)
        if self.host_root is not None and not self.classified_host_root().is_host:
            # Fail loud: a binding we cannot resolve would silently mis-root
            # every read served through this mount.
            msg = "workspace mount host_root must be a host-absolute path"
            raise ValueError(msg)

    def classified_host_root(self) -> ClassifiedPath:
        """Classify :attr:`host_root` (an unusable root classifies as refused)."""
        return HostPathClassifier.classify(self.host_root)


class WorkspaceMountTable:
    """Resolves the per-run mount table from a broker active-grant snapshot.

    Each :class:`~agent_runtime.capabilities.desktop.broker_client.BrokerGrant`
    becomes one :class:`WorkspaceMount`, binding a **readable** virtual mount
    name (the agent addresses ``/workspace/<name>/...``) to the grant's
    ``grant_id`` (every broker op keys off the id, never a host path). The name
    is a slug of the grant's sanitized ``label``; when a label is empty or slugs
    to nothing, the grant's opaque per-boot ``mount`` id is used instead.
    Collisions are disambiguated with a numeric suffix so names stay unique
    within a run. Revoked grants (and any with an empty ``grant_id``) are
    skipped — the route only ever exposes live grants.
    """

    _ACTIVE: Final = "active"
    _SLUG_INVALID: Final = re.compile(r"[^a-z0-9._-]+")
    _DASH_RUN: Final = re.compile(r"-{2,}")
    _FALLBACK_NAME: Final = "workspace"

    @classmethod
    def from_broker_grants(
        cls, grants: Sequence[BrokerGrant]
    ) -> tuple[WorkspaceMount, ...]:
        """Map an ordered active-grant snapshot into a unique-named mount table."""
        mounts: list[WorkspaceMount] = []
        used: set[str] = set()
        for grant in grants:
            if grant.status != cls._ACTIVE or not grant.grant_id:
                continue
            name = cls.mount_name(grant, used=frozenset(used))
            used.add(name)
            mounts.append(
                WorkspaceMount(
                    name=name,
                    grant_id=grant.grant_id,
                    label=grant.label or None,
                    mode=grant.mode,
                )
            )
        return tuple(mounts)

    @classmethod
    def mount_name(cls, grant: BrokerGrant, *, used: frozenset[str]) -> str:
        """Return the readable mount name for ``grant``, unique against ``used``.

        Shared with the grant flow so a mount created mid-run is named by exactly
        the rule a fresh run would have used.
        """
        base = cls._slug(grant.label) or cls._slug(grant.mount) or cls._FALLBACK_NAME
        return cls._dedupe(base, set(used))

    @classmethod
    def _slug(cls, value: str) -> str:
        """Reduce a label to a single safe path segment (``[a-z0-9._-]``)."""
        lowered = (value or "").strip().lower()
        slug = cls._SLUG_INVALID.sub("-", lowered)
        slug = cls._DASH_RUN.sub("-", slug).strip("-._")
        return slug

    @staticmethod
    def _dedupe(base: str, used: set[str]) -> str:
        """Return ``base``, or ``base-2`` / ``base-3`` … when already taken."""
        if base not in used:
            return base
        suffix = 2
        while f"{base}-{suffix}" in used:
            suffix += 1
        return f"{base}-{suffix}"


@dataclass(frozen=True)
class _Resolution:
    """A virtual path resolved to a concrete mount + grant-relative path."""

    mount: WorkspaceMount
    relative: str  # POSIX, no leading slash; "" denotes the mount root


class _WorkspaceRootError(Exception):
    """Internal signal: the path refers to the ``/workspace/`` root itself."""


class _UnknownMountError(Exception):
    """Internal signal: the leading segment names no configured mount."""


class _WorkspaceRefusalError(Exception):
    """Internal signal: the path is refused, with the message the model sees.

    Carries a safe, actionable string — never a host path, never broker
    internals. Raised instead of returning an empty result so a question about
    the filesystem can never be answered with silence.
    """

    def __init__(self, safe_message: str) -> None:
        """Store the model-facing refusal message."""
        super().__init__(safe_message)
        self.safe_message = safe_message


class _GrantScope(StrEnum):
    """Which folder a grant request should name for a given operation.

    A listing addresses the folder itself; a file read addresses its container,
    since a grant covers a folder rather than a single file.
    """

    PATH = "path"
    CONTAINER = "container"


class BrokeredWorkspaceBackend(BackendProtocol):
    """Deep Agents ``BackendProtocol`` translating file ops into broker ``/v1/fs/*`` calls.

    Method → broker route mapping:

    * ``ls`` / ``als``      → ``/v1/fs/list`` (root lists the configured mounts)
    * ``read`` / ``aread``  → ``/v1/fs/read`` (byte window → line slice / base64)
    * ``glob`` / ``aglob``  → ``/v1/fs/glob``
    * ``grep`` / ``agrep``  → ``/v1/fs/grep`` (literal substring, per Deep Agents)
    The required mutation methods raise ``WorkspaceWriteNotSupportedError``.
    The only host-write protocol is C2's staged/prepared/attested authority.

    A host-absolute path is accepted on every read op: covered by a grant it is
    translated locally to ``mount`` + root-relative path (so the broker sees the
    same request it always did), and otherwise it parks the run on a grant
    request through ``grant_gate``. With no gate wired the same path is refused
    out loud. It is never answered with an empty listing.
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
        grant_gate: WorkspaceGrantGate | None = None,
    ) -> None:
        """Bind the read-only backend to a broker client, its mounts and a gate."""
        self._client = client
        self._read_max_bytes = read_max_bytes
        by_name: dict[str, WorkspaceMount] = {}
        for mount in mounts:
            if mount.name in by_name:
                msg = f"duplicate workspace mount name: {mount.name!r}"
                raise ValueError(msg)
            by_name[mount.name] = mount
        # Mutable because the grant flow binds a new mount mid-run; every other
        # read of it is by name.
        self._mounts: dict[str, WorkspaceMount] = by_name
        self._grant_gate = grant_gate
        self._host_roots: HostRootIndex | None = None

    @property
    def supports_writes(self) -> bool:
        """Direct host mutation is retired independently of every rollout flag."""
        return False

    @property
    def mounts(self) -> tuple[WorkspaceMount, ...]:
        """The mount table as it stands now (the grant flow can extend it)."""
        return tuple(self._mounts.values())

    @classmethod
    def claims_path(cls, path: str | None) -> bool:
        """True when a router MUST deliver ``path`` here rather than fall through.

        A host-shaped path belongs to this backend even when it is unsafe or
        ungranted, because only this backend can answer it truthfully — with a
        grant request or an explicit refusal. Letting it reach a virtual backend
        is what produced the empty-success defect.
        """
        raw = path or ""
        if raw == ROUTE_PREFIX.rstrip("/") or raw.startswith(ROUTE_PREFIX):
            return True
        return HostPathClassifier.is_host_shaped(raw)

    # --- BackendProtocol: list ---------------------------------------------

    def ls(self, path: str) -> LsResult:
        """Synchronous directory listing (delegates to :meth:`als`)."""
        return _run_sync(self.als(path))

    async def als(self, path: str) -> LsResult:
        """List the mounts (at root) or a directory's children under a mount."""
        try:
            resolution = await self._aresolve(path, scope=_GrantScope.PATH)
        except _WorkspaceRootError:
            if not self._mounts:
                # Host access is on but nothing is granted: say so.
                return LsResult(error=_SafeMessage.NO_GRANTS)
            entries = [self._mount_dir_entry(m) for m in self._mounts.values()]
            return LsResult(entries=entries)
        except _UnknownMountError:
            return LsResult(error=_SafeMessage.NOT_FOUND)
        except _WorkspaceRefusalError as exc:
            return LsResult(error=exc.safe_message)
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
            # A read addresses a file, so the grantable folder is its container.
            resolution = await self._aresolve(file_path, scope=_GrantScope.CONTAINER)
        except _WorkspaceRootError:
            return ReadResult(error=_SafeMessage.IS_A_DIRECTORY)
        except _UnknownMountError:
            return ReadResult(error=_SafeMessage.NOT_FOUND)
        except _WorkspaceRefusalError as exc:
            return ReadResult(error=exc.safe_message)
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
        try:
            targets = await self._atargets(path)
        except _WorkspaceRefusalError as exc:
            return GlobResult(error=exc.safe_message)
        for mount, relative in targets:
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
        try:
            targets = await self._atargets(path)
        except _WorkspaceRefusalError as exc:
            return GrepResult(error=exc.safe_message)
        for mount, relative in targets:
            path_glob = self._scoped_path_glob(relative, glob)
            try:
                result = await self._client.grep(
                    mount.grant_id, pattern, path_glob=path_glob
                )
            except BrokerError as exc:
                return GrepResult(error=self._safe_message(exc))
            matches.extend(self._hit_to_match(mount, hit) for hit in result.hits)
        return GrepResult(matches=matches)

    # --- BackendProtocol: retired mutation ops ------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        """Refuse a legacy direct host write before any broker call."""
        del file_path, content
        raise WorkspaceWriteNotSupportedError

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Refuse a legacy direct host write before any broker call."""
        del file_path, content
        raise WorkspaceWriteNotSupportedError

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Refuse a legacy direct host edit before any broker call."""
        del file_path, old_string, new_string, replace_all
        raise WorkspaceWriteNotSupportedError

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Refuse a legacy direct host edit before any broker call."""
        del file_path, old_string, new_string, replace_all
        raise WorkspaceWriteNotSupportedError

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        """Refuse legacy batch upload; it is not a prepared workspace effect."""
        del files
        raise WorkspaceWriteNotSupportedError

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        """Refuse legacy batch upload; it is not a prepared workspace effect."""
        del files
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

    async def _atargets(self, path: str | None) -> list[tuple[WorkspaceMount, str]]:
        """Resolve a glob/grep ``path`` to the ``(mount, relative)`` pairs to scan.

        ``None`` / root fans out across every mount; a mount-scoped path narrows
        to one; a host-absolute path resolves (or requests a grant) exactly as a
        read does. An unknown mount and an empty mount table are *refusals*, not
        empty match sets — an empty search result is indistinguishable from "no
        such folder", which is the ambiguity this route exists to remove.
        """
        if path is None or self._is_root(path):
            return self._all_targets()
        try:
            resolution = await self._aresolve(path, scope=_GrantScope.PATH)
        except _WorkspaceRootError:
            return self._all_targets()
        except _UnknownMountError:
            raise _WorkspaceRefusalError(_SafeMessage.NOT_FOUND) from None
        return [(resolution.mount, resolution.relative)]

    def _all_targets(self) -> list[tuple[WorkspaceMount, str]]:
        """Every mount at its root, refusing when nothing has been granted."""
        if not self._mounts:
            raise _WorkspaceRefusalError(_SafeMessage.NO_GRANTS)
        return [(mount, "") for mount in self._mounts.values()]

    # --- host-path resolution ----------------------------------------------

    async def _aresolve(self, path: str | None, *, scope: _GrantScope) -> _Resolution:
        """Resolve any accepted path shape to ``(mount, relative)``.

        Virtual paths take the synchronous path unchanged. Host-absolute paths
        are covered by a grant or become a grant request. Everything else is
        refused with a safe message.
        """
        classified = HostPathClassifier.classify(path)
        if classified.kind is HostPathKind.UNSAFE:
            # Traversal / device / reserved shapes fail closed here — BEFORE any
            # grant request, so a grant can never launder an escape.
            raise _WorkspaceRefusalError(
                HostPathMessages.for_refusal(classified.refusal)
            )
        if self._is_virtual_namespace(path):
            return self._resolve(path)
        if classified.kind is not HostPathKind.HOST_ABSOLUTE:
            raise _WorkspaceRefusalError(
                HostPathMessages.for_refusal(classified.refusal)
            )
        return await self._resolve_host(classified, scope=scope)

    def _is_virtual_namespace(self, path: str | None) -> bool:
        """True when ``path`` addresses this backend's own mount namespace.

        Three ways in: the explicit ``/workspace/...`` form (unstripped, so an
        unknown mount there is a not-found rather than a host folder), a leading
        segment that names a configured mount (the prefix-stripped form
        ``CompositeBackend`` delivers), and any path that is not host-shaped at
        all. A mount name always wins over a same-named host folder.
        """
        raw = path or ""
        if raw == ROUTE_PREFIX.rstrip("/") or raw.startswith(ROUTE_PREFIX):
            return True
        segments = self._split(raw)
        if segments and segments[0] in self._mounts:
            return True
        return not HostPathClassifier.is_host_shaped(raw)

    async def _resolve_host(
        self, target: ClassifiedPath, *, scope: _GrantScope
    ) -> _Resolution:
        """Serve a host-absolute path through a covering grant, or request one."""
        resolution = self._cover(target)
        if resolution is not None:
            return resolution
        folder = target if scope is _GrantScope.PATH else target.parent()
        if not folder.is_host:
            # e.g. reading ``/a.csv``: the container is a whole volume, which is
            # never a grantable folder.
            raise _WorkspaceRefusalError(HostPathMessages.for_refusal(folder.refusal))
        if self._grant_gate is None:
            raise _WorkspaceRefusalError(WorkspaceGrantMessages.NOT_GRANTED)
        outcome = await self._grant_gate.request(
            folder, bound_grant_ids=self._bound_grant_ids()
        )
        if (
            not outcome.approved
            or outcome.grant is None
            or outcome.granted_root is None
        ):
            raise _WorkspaceRefusalError(outcome.message)
        self._bind_grant(outcome.grant, outcome.granted_root)
        resolution = self._cover(target)
        if resolution is None:
            raise _WorkspaceRefusalError(WorkspaceGrantMessages.UNBOUND)
        return resolution

    def _cover(self, target: ClassifiedPath) -> _Resolution | None:
        """Translate ``target`` to ``(mount, relative)`` through a bound root."""
        match = self._host_root_index().cover(target)
        if match is None:
            return None
        return _Resolution(mount=self._mounts[match.key], relative=match.relative)

    def _host_root_index(self) -> HostRootIndex:
        """The mount-name-keyed index of granted host roots (built on demand)."""
        if self._host_roots is None:
            self._host_roots = HostRootIndex(
                [
                    (mount.name, mount.classified_host_root())
                    for mount in self._mounts.values()
                    if mount.host_root is not None
                ]
            )
        return self._host_roots

    def _bound_grant_ids(self) -> frozenset[str]:
        """Grant ids already bound to a mount — how a NEW grant is recognised."""
        return frozenset(mount.grant_id for mount in self._mounts.values())

    def _bind_grant(self, grant: BrokerGrant, root: ClassifiedPath) -> None:
        """Bind an approved grant to its host root, extending the mount table.

        A grant already bound to this exact root is reused; one bound with no
        root yet adopts it (a snapshot-derived mount cannot know its own root).
        Two different roots for one grant id mean one of them is wrong, so that
        case is left unbound and the read is refused rather than mis-rooted.
        """
        existing = next(
            (m for m in self._mounts.values() if m.grant_id == grant.grant_id), None
        )
        if existing is not None:
            if existing.host_root is None:
                self._mounts[existing.name] = replace(existing, host_root=root.display)
                self._host_roots = None
            return
        name = WorkspaceMountTable.mount_name(grant, used=frozenset(self._mounts))
        self._mounts[name] = WorkspaceMount(
            name=name,
            grant_id=grant.grant_id,
            label=grant.label or None,
            mode=grant.mode,
            host_root=root.display,
        )
        self._host_roots = None

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

    ``grant_requests`` is ON by default: wherever host access is configured at
    all, an ungranted folder must be able to ASK. Access stays grant-scoped
    either way — default-on means the affordance exists, not that anything is
    readable. Turning it off does not restore the silent fallthrough: an
    ungranted host path is then refused out loud instead.

    ``run_id`` only scopes the grant request's deterministic approval id.
    """

    broker_base_url: str | None = None
    broker_token: str | None = None
    service_identity: str | None = None
    broker_audience: str | None = None
    protocol_version: str = "1"
    timeout_seconds: float = 10.0
    read_max_bytes: int = DEFAULT_READ_MAX_BYTES
    mounts: tuple[WorkspaceMount, ...] = field(default_factory=tuple)
    grant_requests: bool = True
    run_id: str | None = None

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
            service_identity=source.get(_Env.SERVICE_IDENTITY) or None,
            broker_audience=source.get(_Env.BROKER_AUDIENCE) or None,
            protocol_version=source.get(_Env.BROKER_PROTOCOL) or "1",
            mounts=tuple(mounts),
        )

    def with_run(self, run_id: str | None) -> WorkspaceBackendConfig:
        """Return a copy scoped to ``run_id`` (frozen-safe replace)."""
        return replace(self, run_id=run_id)

    def with_mounts(self, mounts: Sequence[WorkspaceMount]) -> WorkspaceBackendConfig:
        """Return a copy of this config carrying ``mounts`` (frozen-safe replace).

        The wiring resolves mounts from the broker grant snapshot after building
        the connection config from env, then binds them here before calling
        :func:`build_workspace_backend`.
        """
        return replace(self, mounts=tuple(mounts))


def build_workspace_backend(
    config: WorkspaceBackendConfig,
    *,
    client: DesktopBrokerClient | None = None,
    grant_gate: WorkspaceGrantGate | None = None,
) -> BrokeredWorkspaceBackend | None:
    """Construct the ``/workspace/`` backend, or ``None`` when broker config is absent.

    This is the ONE seam the runtime worker wiring calls, e.g. registering
    ``{ROUTE_PREFIX: build_workspace_backend(cfg)}`` into the ``CompositeBackend``
    routes only when the result is not ``None``. It is intentionally synchronous
    and does no network I/O — mounts are passed in via ``config``.

    ``client`` lets the caller reuse the broker client it already built for the
    grant-snapshot fetch (one client per run, and a test can inject a fake
    transport). When omitted, a client is constructed from ``config`` over the
    process-shared HTTP pool.

    ``grant_gate`` overrides the gate that asks the user to grant an ungranted
    folder (tests inject one with a fake interrupt handler). When omitted, a gate
    is built over the same broker client unless ``config.grant_requests`` is off.

    An EMPTY mount table still yields a backend. That is deliberate: host access
    is on by default and stays grant-scoped, so the route must exist for a host
    path to be able to ask for a grant. Returning ``None`` here is what left
    ``ls /Users/<name>/Downloads`` to a virtual backend that answered it with an
    empty listing.

    This seam is read-only. C2's prepared authority is composed separately by
    the workspace-effect executor and is never passed through this backend.
    """
    if not config.broker_base_url or not config.broker_token:
        return None
    resolved_client = client or DesktopBrokerClient(
        BrokerClientConfig(
            base_url=config.broker_base_url,
            token=config.broker_token,
            service_identity=config.service_identity,
            broker_audience=config.broker_audience,
            protocol_version=config.protocol_version,
            timeout_seconds=config.timeout_seconds,
        )
    )
    resolved_gate = grant_gate
    if resolved_gate is None and config.grant_requests:
        resolved_gate = WorkspaceGrantGate(grants=resolved_client, run_id=config.run_id)
    return BrokeredWorkspaceBackend(
        client=resolved_client,
        mounts=config.mounts,
        read_max_bytes=config.read_max_bytes,
        grant_gate=resolved_gate,
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
