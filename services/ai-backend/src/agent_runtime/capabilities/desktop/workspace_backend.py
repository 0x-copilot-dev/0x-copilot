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
import logging
import os
import re
from collections.abc import AsyncIterator, Mapping, Sequence
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
    FsStatResult,
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
from agent_runtime.capabilities.workspace.contracts import (
    WorkspaceBaseEntry,
    WorkspaceBaseMatch,
    WorkspaceEntryKind,
    normalize_virtual_path,
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

logger = logging.getLogger(__name__)

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
    """Environment variable names carrying broker connection config.

    The desktop supervisor forwards the WORKSPACE-prefixed names
    (``service-env.ts`` sets ``DESKTOP_WORKSPACE_BROKER_URL`` / ``_TOKEN`` /
    ``_AUDIENCE`` beside the browser broker's ``DESKTOP_BROWSER_BROKER_*``), so
    those are what a supervised ai-backend actually receives. Reading the
    unprefixed ``DESKTOP_BROKER_URL`` / ``DESKTOP_BROKER_TOKEN`` — which nothing
    in the app has ever set — made ``WorkspaceBackendConfig.from_env`` return an
    empty base url, so ``workspace_backend()`` returned ``None``, no
    ``/workspace/`` route was composed, ``guarded_default`` fell through to the
    ``StateBackend``, and ``ls ~/Downloads`` was answered by agent memory with an
    empty listing and a green tick. That was the live defect: every layer below
    was correct and never reached. ``BROKER_AUDIENCE`` already used the
    prefixed name, which is why only the pair below was wrong.

    The unprefixed names stay as a FALLBACK so any caller that already exports
    them keeps working; the prefixed name wins when both are present.
    """

    BROKER_URL: Final = "DESKTOP_WORKSPACE_BROKER_URL"
    BROKER_TOKEN: Final = "DESKTOP_WORKSPACE_BROKER_TOKEN"
    LEGACY_BROKER_URL: Final = "DESKTOP_BROKER_URL"
    LEGACY_BROKER_TOKEN: Final = "DESKTOP_BROKER_TOKEN"
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
            mount = cls._mount_for(grant, name=name)
            if mount is not None:
                mounts.append(mount)
        return tuple(mounts)

    @classmethod
    def _mount_for(cls, grant: BrokerGrant, *, name: str) -> WorkspaceMount | None:
        """One grant's mount, degrading per-grant rather than per-snapshot.

        ``root`` crosses a PROCESS BOUNDARY: it is whatever the broker put on the
        wire, and :class:`WorkspaceMount` rejects anything that is not a
        host-absolute path (a relative root, ``~``-relative, a Windows root that
        nobody normalised to POSIX). That rejection is correct — a root we cannot
        resolve must never become an ``allow`` rule — but raising it HERE used to
        abort the whole table: one malformed grant took every OTHER folder the
        user had attached down with it, mid-run, through
        ``WorkspaceBackendWorkerWiring.workspace_backend`` whose only ``except``
        is ``BrokerError``. Multi-grant is exactly where that hurts most.

        So the bad field is dropped, not the grant. The mount still binds its
        ``grant_id``, so broker-served reads through ``/workspace/<name>/...``
        keep working; it simply carries no host root, which is the same state a
        broker that never sends one produces — the folder keeps ASKING. That is
        the safe direction: degraded, never widened. ``None`` (skip the grant
        entirely) is reserved for a mount that cannot be built at all.
        """

        host_root = grant.root or None
        try:
            return WorkspaceMount(
                name=name,
                grant_id=grant.grant_id,
                label=grant.label or None,
                mode=grant.mode,
                # The grant's real host root, when the broker sends one.
                # This is what `HostFilesystemRules` turns into an `allow`
                # rule, so a folder the user explicitly attached stops
                # prompting on every read. A broker that omits it yields
                # None and the mount still works — the folder just keeps
                # asking, which is degraded rather than broken.
                host_root=host_root,
            )
        except ValueError:
            if host_root is None:
                # Nothing left to drop: the name or the grant id is unusable.
                logger.warning("workspace_mount.unusable_grant mount=%s", name)
                return None
        # Never log the offending root: it is a host path.
        logger.warning("workspace_mount.unusable_root mount=%s", name)
        try:
            return WorkspaceMount(
                name=name,
                grant_id=grant.grant_id,
                label=grant.label or None,
                mode=grant.mode,
                host_root=None,
            )
        except ValueError:
            logger.warning("workspace_mount.unusable_grant mount=%s", name)
            return None

    @classmethod
    def granted_roots(cls, mounts: Sequence[WorkspaceMount]) -> tuple[object, ...]:
        """Mounts that carry a usable host root, as ``GrantedRoot`` rules input.

        Silently drops a mount whose root is absent or does not classify as a
        host path. That is deliberate: a root we cannot resolve must not become
        an ``allow`` rule, because the rule would either match nothing (harmless
        but useless) or — far worse — match the wrong subtree. A dropped root
        degrades to "this folder still asks", never to "this folder is open".

        Built through :meth:`GrantedRoot.from_host_path`, which converts the
        broker's root into the canonical POSIX spelling the tool layer rewrites
        every path to. The plain constructor demands that spelling and REJECTS a
        drive-absolute root, so a Windows grant (``C:\\Users\\p\\Downloads``)
        landed in the ``except ValueError`` below and was dropped — leaving the
        Windows half of the product with a folder the user had explicitly
        attached that asked again on every single read. This is the one
        production caller of that seam, and it is the only lane the rules and
        the floor are now built from.
        """

        from agent_runtime.capabilities.desktop.host_filesystem import (  # noqa: PLC0415
            GrantedRoot,
        )

        roots: list[object] = []
        for mount in mounts:
            if mount.host_root is None:
                continue
            if not mount.classified_host_root().is_host:
                continue
            try:
                roots.append(
                    GrantedRoot.from_host_path(
                        mount.host_root,
                        # Only a grant that actually permits writing may site a
                        # writable scratch dir; read-only stays read-only.
                        writable=mount.mode != "read_only",
                    )
                )
            except ValueError:
                # Same rule as above: an unusable root is dropped, never widened.
                continue
        return tuple(roots)

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

    @property
    def granted_roots(self) -> tuple[object, ...]:
        """Host roots the user has granted, as ``HostFilesystemRules`` input.

        Read through ``getattr`` rather than an ``isinstance`` check, so any
        workspace lane CAN supply it by exposing this one property. But a lane
        that cannot is no longer a lane where grants are inert: ``getattr``
        widened who may answer and did not make anyone able to, and the ENFORCE
        lane's C3 backends still cannot — their host-session projection is
        path-free by design. The worker therefore resolves the roots off the
        broker's active-grant snapshot
        (``WorkspaceBackendWorkerWiring.granted_host_roots``) and hands them to
        the factory directly.

        This property survives as the compatibility lane's shortcut: this
        backend was BUILT from that same snapshot through the same
        ``WorkspaceMountTable`` mapping, so reading it here saves a second,
        independently-timed read of one broker fact.
        """

        return WorkspaceMountTable.granted_roots(self.mounts)

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

    def base_read_port(self) -> "BrokerBaseRead":
        """This backend seen as a `WorkspaceBaseReadPort`.

        The seam that lets `MergedWorkspaceBackend` layer a staged overlay over
        broker-served reads when C2's host session is unavailable — so the
        absence of write authority costs the user writes, not reads.
        """

        return BrokerBaseRead(self)

    async def astat_entry(self, path: str) -> FsStatResult | None:
        """Leaf metadata over `/v1/fs/stat`, or ``None`` when unavailable.

        Exists because `WorkspaceBaseEntry` REQUIRES `byte_size` on a file, and
        `/v1/fs/list` returns only a name and a type. The size is not cosmetic:
        the overlay and the blob store size their reads from it.
        """

        try:
            resolution = await self._aresolve(path, scope=_GrantScope.CONTAINER)
        except (_WorkspaceRootError, _UnknownMountError, _WorkspaceRefusalError):
            return None
        if not resolution.relative:
            return None
        try:
            return await self._client.stat(
                resolution.mount.grant_id, resolution.relative
            )
        except BrokerError:
            return None

    async def abytes(
        self, path: str, *, start: int | None = None, end: int | None = None
    ) -> bytes:
        """Raw bytes of a grant-relative file, unsliced by any text layer.

        `aread` exists for the MODEL and returns line-sliced text (base64 for
        binary). The overlay and the artifact blob store need the file itself:
        a CSV re-wrapped by a line slicer is a different file, and a digest
        taken over it would not match the one the host holds.

        Refusals return empty rather than raising: this feeds a read port whose
        callers merge base content with staged content, and a raise would take
        the staged half down with the base half.
        """

        try:
            resolution = await self._aresolve(path, scope=_GrantScope.CONTAINER)
        except (_WorkspaceRootError, _UnknownMountError, _WorkspaceRefusalError):
            return b""
        if not resolution.relative:
            return b""
        try:
            result = await self._client.read(
                resolution.mount.grant_id,
                resolution.relative,
                offset=start,
                max_bytes=(end - start)
                if (start is not None and end is not None)
                else self._read_max_bytes,
            )
        except BrokerError:
            return b""
        try:
            return base64.b64decode(result.base64, validate=True)
        except (binascii.Error, ValueError):
            return b""

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
        """Build config from the supervisor's broker env (+ mounts).

        Prefers the ``DESKTOP_WORKSPACE_BROKER_*`` names the desktop actually
        forwards, falling back to the unprefixed pair — see :class:`_Env`.
        """
        source = env if env is not None else os.environ
        return cls(
            broker_base_url=(
                source.get(_Env.BROKER_URL)
                or source.get(_Env.LEGACY_BROKER_URL)
                or None
            ),
            broker_token=(
                source.get(_Env.BROKER_TOKEN)
                or source.get(_Env.LEGACY_BROKER_TOKEN)
                or None
            ),
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


class BrokerBaseRead:
    """A :class:`WorkspaceBaseReadPort` served by the capability broker.

    WHY THIS EXISTS. `MergedWorkspaceBackend` — the only object that can layer a
    staged overlay over the user's real files — takes its base reads as a PORT,
    not as a host session. Until now the sole implementation came from C2's
    private host session, so a run without one fell to
    `WorkspaceTombstoneBackend`, which refuses READS as well as writes. Turning
    the enforced lane on therefore made attached folders LESS usable, not more.

    With this, losing the write authority degrades to read-only instead of to
    nothing — the invariant the two-lane split kept violating.

    WHAT IT DELIBERATELY CANNOT DO. The broker's `/v1/fs/list` returns a name
    and a type and nothing else: no digest, no generation, no mtime. Those are
    exactly the fields the overlay compares to prove a staged write's
    precondition still holds. So every entry here reports them as ``None``, and
    a caller that needs a precondition must obtain the host session instead.
    That is not a gap to be filled later by inventing values — a fabricated
    generation would let a write claim a precondition nobody checked.

    Lives beside the backend that already owns mount resolution and the broker
    client rather than in a module of its own, so there is one place that knows
    how a virtual path becomes a grant-relative one.
    """

    def __init__(self, backend: "BrokeredWorkspaceBackend") -> None:
        self._backend = backend

    #: `normalize_virtual_path` demands the full `/workspace/<mount>/...` form,
    #: while the backend routes on `/<mount>/...` because the composite mounts
    #: it AT `/workspace`. This adapter is the seam between those two spellings.
    _ROOT = "/workspace"

    @classmethod
    def _to_route(cls, virtual_path: str) -> str:
        """`/workspace/<mount>/x` -> `/<mount>/x` for the backend's own router."""

        normalized = normalize_virtual_path(virtual_path, allow_mount_root=True)
        return normalized[len(cls._ROOT) :] or "/"

    @classmethod
    def _to_virtual(cls, route_path: str) -> str:
        """The inverse, so every entry we hand back is canonical."""

        suffix = route_path if route_path.startswith("/") else f"/{route_path}"
        return normalize_virtual_path(f"{cls._ROOT}{suffix}", allow_mount_root=True)

    async def _entry(
        self, route_path: str, *, is_dir: bool
    ) -> WorkspaceBaseEntry | None:
        """One entry, or ``None`` when its metadata cannot be established.

        A directory needs nothing further. A FILE needs `byte_size`, which
        `/v1/fs/list` does not return — hence the extra `/v1/fs/stat` per file
        child. That is one loopback round-trip per file in a listing, which is
        the price of not fabricating a size; an entry whose size was invented
        would mis-size every read taken against it.
        """

        virtual_path = self._to_virtual(route_path)
        if is_dir:
            return WorkspaceBaseEntry(
                virtual_path=virtual_path, entry_kind=WorkspaceEntryKind.DIRECTORY
            )
        stat = await self._backend.astat_entry(route_path)
        if stat is None:
            # Listed but unreadable: omit it rather than claim a size. The
            # caller merges this with staged content and must not be told a
            # file is 0 bytes when nobody could measure it.
            return None
        return self._entry_from_stat(route_path, stat)

    def _entry_from_stat(
        self, route_path: str, stat: FsStatResult
    ) -> WorkspaceBaseEntry:
        """Build an entry from metadata already in hand — never re-stat.

        `stat()` holds the result the moment it asks; routing it back through
        `_entry` would issue a second identical `/v1/fs/stat` per call.
        """

        return WorkspaceBaseEntry(
            virtual_path=self._to_virtual(route_path),
            entry_kind=(
                WorkspaceEntryKind.DIRECTORY
                if stat.type == "dir"
                else WorkspaceEntryKind.FILE
            ),
            byte_size=stat.size,
            mtime_ns=int(stat.mtime_ms * 1_000_000),
        )

    async def _entries(self, infos: object) -> tuple[WorkspaceBaseEntry, ...]:
        built = [
            await self._entry(
                str(info.get("path") or ""), is_dir=bool(info.get("is_dir"))
            )
            for info in infos or ()
            if info.get("path")
        ]
        return tuple(entry for entry in built if entry is not None)

    async def stat(self, virtual_path: str) -> WorkspaceBaseEntry | None:
        """Metadata for one base path, or ``None`` when it is not there.

        Straight from `/v1/fs/stat`, which carries the size and mtime a
        `WorkspaceBaseEntry` needs. Unreachable metadata reads as absent — the
        honest answer for a port whose caller merges base with staged content.
        """

        route = self._to_route(virtual_path)
        stat = await self._backend.astat_entry(route)
        return None if stat is None else self._entry_from_stat(route, stat)

    async def list(self, virtual_path: str) -> Sequence[WorkspaceBaseEntry]:
        """Direct children. A refusal lists NOTHING rather than raising.

        The overlay merges this with staged entries, and a raise there would
        take the staged half down with the base half.
        """

        listing = await self._backend.als(self._to_route(virtual_path))
        if listing.error:
            return ()
        return await self._entries(listing.entries)

    async def read(
        self,
        virtual_path: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        """A bounded byte window, as a single-chunk stream.

        Bytes, not the line-sliced text `aread` returns: this port feeds the
        overlay and the artifact blob store, and a CSV that came back re-wrapped
        by a text slicer would be a different file.
        """

        route = self._to_route(virtual_path)
        payload = await self._backend.abytes(route, start=start, end=end)

        async def _stream() -> AsyncIterator[bytes]:
            yield payload

        return _stream()

    async def glob(self, pattern: str) -> Sequence[WorkspaceBaseEntry]:
        result = await self._backend.aglob(pattern)
        if result.error:
            return ()
        return await self._entries(result.entries)

    async def grep(
        self, query: str, paths: Sequence[str] | None = None
    ) -> Sequence[WorkspaceBaseMatch]:
        """Literal search hits. ``paths`` narrows the search root when given."""

        route = self._to_route(paths[0]) if paths else None
        result = await self._backend.agrep(query, path=route)
        if result.error:
            return ()
        matches: list[WorkspaceBaseMatch] = []
        for hit in result.matches or ():
            path = str(hit.get("path") or "")
            line_number = hit.get("line")
            if not path or not isinstance(line_number, int) or line_number < 1:
                continue
            matches.append(
                WorkspaceBaseMatch(
                    virtual_path=self._to_virtual(path),
                    line_number=line_number,
                    line_text=str(hit.get("text") or ""),
                )
            )
        return tuple(matches)
