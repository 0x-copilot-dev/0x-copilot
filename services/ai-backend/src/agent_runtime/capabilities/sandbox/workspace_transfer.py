"""Workspace snapshot validation, deterministic hashing, and patch diffing.

Input to a sandbox is an explicit, reviewable snapshot — never a live host
mount. This module owns:

* normalizing/validating snapshot paths (reject traversal, absolute paths,
  links/devices, non-overridable secret excludes, and quota overflow);
* computing an order-independent manifest hash;
* diffing a post-run ``/workspace`` listing against the baseline into a typed
  ``WorkspacePatchManifest``.

DEFERRED (noted here as the seam): applying a patch to the host filesystem is a
SEPARATE AC5 broker operation (grant revalidation + expected-hash + atomic
write). AC7 never writes host files; :meth:`WorkspacePatchBuilder.build`
produces the reviewable artifact and stops there.

The broker (AC5) is the authority for reading host bytes and producing the
per-file SHA-256 + content ``ArtifactRef``; this module validates and assembles
what the broker supplies. It does not itself touch the host filesystem.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import posixpath

from pydantic import ValidationError

from agent_runtime.capabilities.sandbox.config import (
    SandboxLimitProfile,
    SandboxLimitProfiles,
)
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxError,
    SandboxErrorCode,
    SandboxSnapshot,
    WorkspacePatchEntry,
    WorkspacePatchManifest,
    WorkspaceTransferEntry,
    WorkspaceTransferManifest,
)

WORKSPACE_ROOT = "/workspace"

#: Non-overridable path/glob exclusions (PRD "Workspace snapshot"). Matching is
#: performed on normalized POSIX segments so ``a/.env`` and ``.ssh/id_rsa`` are
#: both caught. A ``/`` suffix marks a directory prefix; otherwise a basename
#: glob suffix (``*.pem``) or exact basename (``.env``).
_EXCLUDED_DIR_SEGMENTS: frozenset[str] = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
    }
)
_EXCLUDED_BASENAMES: frozenset[str] = frozenset({".env"})
_EXCLUDED_SUFFIXES: tuple[str, ...] = (
    ".env",  # catches .env.local, .env.production via startswith below too
    ".pem",
    ".key",
    ".p12",
    ".pfx",
)


@dataclass(frozen=True)
class RawSnapshotEntry:
    """A candidate file the broker collected, before validation.

    ``path`` is a host-relative POSIX path (the broker never emits absolute host
    paths). ``is_symlink``/``is_special`` flag non-regular files the broker
    detected so this module can reject them without a host round-trip.
    """

    path: str
    sha256: str
    size_bytes: int
    executable: bool = False
    payload_ref: ArtifactRef | None = None
    is_symlink: bool = False
    is_special: bool = False
    # Archive/import adapters must expose every ambiguity.  D3 rejects rather
    # than dereferencing: symlink/device/socket/FIFO and hard-link semantics
    # can all reach bytes outside the reviewed snapshot; sparse files can make
    # a small archive materialise into an unbounded upload.
    is_hardlink: bool = False
    is_sparse: bool = False
    link_count: int = 1
    complete: bool = True


class WorkspacePathValidator:
    """Normalizes and screens a single snapshot path."""

    @classmethod
    def normalize(cls, raw_path: str) -> str:
        """Return a normalized ``/workspace``-rooted POSIX path or raise.

        Rejects absolute host paths, backslashes (Windows separators must be
        normalized by the broker first), NUL bytes, and any ``..`` traversal
        that escapes the root.
        """

        candidate = (raw_path or "").strip()
        if not candidate:
            cls._reject("empty path")
        if "\x00" in candidate:
            cls._reject("path contains NUL")
        if "\\" in candidate:
            cls._reject("path contains a backslash separator")
        # Treat everything as relative to /workspace; a leading slash that is
        # not already the workspace root is a host-absolute path.
        if candidate.startswith("/") and not candidate.startswith(WORKSPACE_ROOT):
            cls._reject("absolute host path is not permitted")
        rel = (
            candidate[len(WORKSPACE_ROOT) :]
            if candidate.startswith(WORKSPACE_ROOT)
            else candidate
        )
        rel = rel.lstrip("/")
        normalized = posixpath.normpath(rel)
        if normalized in (".", "") or normalized.startswith(".."):
            cls._reject("path escapes the workspace root")
        if "/../" in f"/{normalized}/":
            cls._reject("path escapes the workspace root")
        return f"{WORKSPACE_ROOT}/{normalized}"

    @classmethod
    def is_excluded(cls, normalized_path: str) -> bool:
        """Whether a normalized path is a non-overridable secret/cache exclusion."""

        rel = normalized_path[len(WORKSPACE_ROOT) + 1 :]
        segments = rel.split("/")
        if any(seg in _EXCLUDED_DIR_SEGMENTS for seg in segments[:-1]):
            return True
        basename = segments[-1]
        if basename in _EXCLUDED_BASENAMES:
            return True
        if basename.startswith(".env"):
            return True
        return any(basename.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES)

    @staticmethod
    def _reject(reason: str) -> None:
        raise SandboxError(
            SandboxErrorCode.SNAPSHOT_INVALID,
            f"Snapshot rejected: {reason}.",
        )


class WorkspaceManifestBuilder:
    """Validates raw broker entries and assembles a signed transfer manifest."""

    @classmethod
    def build(
        cls,
        *,
        workspace_id: str,
        root_grant_id: str,
        raw_entries: Iterable[RawSnapshotEntry],
        limits: SandboxLimitProfile,
    ) -> WorkspaceTransferManifest:
        """Return a validated, deterministically-hashed transfer manifest.

        Raises :class:`SandboxError` with ``SNAPSHOT_INVALID`` for path/type
        violations and ``SNAPSHOT_QUOTA_EXCEEDED`` when count/byte ceilings are
        crossed. Excluded (secret/cache) paths are dropped silently — they are
        not an error, they simply never leave the device.
        """

        validated: list[WorkspaceTransferEntry] = []
        total_bytes = 0
        seen: set[str] = set()
        for raw in raw_entries:
            if (
                raw.is_symlink
                or raw.is_special
                or raw.is_hardlink
                or raw.is_sparse
                or raw.link_count != 1
                or not raw.complete
            ):
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Snapshot rejected: archive entries must be complete regular files.",
                )
            normalized = WorkspacePathValidator.normalize(raw.path)
            if WorkspacePathValidator.is_excluded(normalized):
                continue
            if normalized in seen:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Snapshot rejected: duplicate path after normalization.",
                )
            seen.add(normalized)
            if raw.size_bytes > limits.max_upload_file_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Snapshot rejected: a file exceeds the per-file ceiling.",
                )
            if raw.payload_ref is None:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Snapshot rejected: missing content reference for a file.",
                )
            if (
                raw.payload_ref.sha256 != raw.sha256
                or raw.payload_ref.size_bytes != raw.size_bytes
            ):
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Snapshot rejected: content reference does not match metadata.",
                )
            total_bytes += raw.size_bytes
            validated.append(
                WorkspaceTransferEntry(
                    path=normalized,
                    sha256=raw.sha256,
                    size_bytes=raw.size_bytes,
                    executable=raw.executable,
                    payload_ref=raw.payload_ref,
                )
            )

        if len(validated) > limits.max_upload_files:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                "Snapshot rejected: file count exceeds the ceiling.",
            )
        if total_bytes > limits.max_upload_total_bytes:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                "Snapshot rejected: total bytes exceed the ceiling.",
            )

        ordered = tuple(sorted(validated, key=lambda entry: entry.path))
        manifest_sha = cls._hash_entries(ordered)
        return WorkspaceTransferManifest(
            workspace_id=workspace_id,
            root_grant_id=root_grant_id,
            entries=ordered,
            total_bytes=total_bytes,
            manifest_sha256=manifest_sha,
        )

    @classmethod
    def to_sandbox_snapshot(
        cls,
        manifest: WorkspaceTransferManifest,
        *,
        snapshot_id: str,
    ) -> SandboxSnapshot:
        """Strip C3-private workspace/grant facts before provider transfer."""

        cls.verify_manifest(manifest)
        return SandboxSnapshot(
            snapshot_id=snapshot_id,
            entries=manifest.entries,
            total_bytes=manifest.total_bytes,
            manifest_sha256=manifest.manifest_sha256,
        )

    @classmethod
    def verify_manifest(
        cls,
        manifest: WorkspaceTransferManifest | SandboxSnapshot,
        *,
        limits: SandboxLimitProfile | None = None,
    ) -> None:
        """Recompute every immutable manifest fact at the transfer boundary."""

        entries = manifest.entries
        if len({entry.path for entry in entries}) != len(entries):
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_INVALID,
                "Snapshot rejected: duplicate virtual path.",
            )
        if sum(entry.size_bytes for entry in entries) != manifest.total_bytes:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot byte total verification failed.",
            )
        if cls._hash_entries(entries) != manifest.manifest_sha256:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot manifest verification failed.",
            )
        if limits is not None:
            if len(entries) > limits.max_upload_files:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Sandbox snapshot file count exceeds the ceiling.",
                )
            if manifest.total_bytes > limits.max_upload_total_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Sandbox snapshot byte total exceeds the ceiling.",
                )
        for entry in entries:
            normalized = WorkspacePathValidator.normalize(entry.path)
            if normalized != entry.path or WorkspacePathValidator.is_excluded(
                normalized
            ):
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Sandbox snapshot contains a non-canonical or excluded path.",
                )
            if limits is not None and entry.size_bytes > limits.max_upload_file_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Sandbox snapshot file exceeds the per-file ceiling.",
                )
            if (
                entry.payload_ref.sha256 != entry.sha256
                or entry.payload_ref.size_bytes != entry.size_bytes
            ):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot content reference verification failed.",
                )

    @staticmethod
    def _hash_entries(entries: Sequence[WorkspaceTransferEntry]) -> str:
        """Order-independent manifest hash over (path, sha256, size, exec).

        Entries are sorted by path before hashing so host directory enumeration
        order cannot change the manifest hash.
        """

        hasher = hashlib.sha256()
        for entry in sorted(entries, key=lambda e: e.path):
            line = f"{entry.path}\x00{entry.sha256}\x00{entry.size_bytes}\x00{int(entry.executable)}\n"
            hasher.update(line.encode("utf-8"))
        return hasher.hexdigest()


class WorkspacePatchBuilder:
    """Diffs a post-run ``/workspace`` listing against the baseline manifest.

    Produces the reviewable :class:`WorkspacePatchManifest`. Host apply is a
    SEPARATE AC5 broker step and is out of scope here.
    """

    @classmethod
    def build(
        cls,
        *,
        baseline: WorkspaceTransferManifest | SandboxSnapshot,
        result_entries: Mapping[str, RawSnapshotEntry],
        directories: Iterable[str] = (),
        moves: Mapping[str, str] | None = None,
        complete: bool = True,
        limits: SandboxLimitProfile | None = None,
    ) -> WorkspacePatchManifest:
        """Return a typed patch from baseline → result.

        ``result_entries`` is keyed by *raw* path; each is normalized/validated
        here. ``complete=False`` marks a partial download whose patch must not
        be applied.
        """

        WorkspaceManifestBuilder.verify_manifest(baseline)
        enforced_limits = limits or SandboxLimitProfiles.get("desktop_v1")
        baseline_by_path = {entry.path: entry for entry in baseline.entries}
        result_by_path: dict[str, RawSnapshotEntry] = {}
        for raw in result_entries.values():
            if (
                raw.is_symlink
                or raw.is_special
                or raw.is_hardlink
                or raw.is_sparse
                or raw.link_count != 1
                or not raw.complete
                or raw.payload_ref is None
            ):
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: output entries must be complete regular files.",
                )
            normalized = WorkspacePathValidator.normalize(raw.path)
            if WorkspacePathValidator.is_excluded(normalized):
                continue
            if normalized in result_by_path:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: duplicate path after normalization.",
                )
            if raw.size_bytes > enforced_limits.max_upload_file_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Patch rejected: a changed file exceeds the per-file ceiling.",
                )
            if (
                raw.payload_ref.sha256 != raw.sha256
                or raw.payload_ref.size_bytes != raw.size_bytes
            ):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Patch rejected: output content reference verification failed.",
                )
            result_by_path[normalized] = raw

        normalized_directories: set[str] = set()
        for directory in directories:
            normalized = WorkspacePathValidator.normalize(directory)
            if WorkspacePathValidator.is_excluded(normalized):
                continue
            if normalized in result_by_path:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: a path cannot be both a file and directory.",
                )
            normalized_directories.add(normalized)

        normalized_moves: dict[str, str] = {}
        for source, destination in (moves or {}).items():
            normalized_source = WorkspacePathValidator.normalize(source)
            normalized_destination = WorkspacePathValidator.normalize(destination)
            if normalized_source == normalized_destination:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: a move must change its path.",
                )
            if normalized_source in normalized_moves:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: duplicate move source.",
                )
            if normalized_destination in normalized_moves.values():
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: duplicate move destination.",
                )
            baseline_entry = baseline_by_path.get(normalized_source)
            destination_entry = result_by_path.get(normalized_destination)
            if baseline_entry is None or destination_entry is None:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: a move must prove its source and destination.",
                )
            if normalized_source in result_by_path:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: a moved source may not also remain as a file.",
                )
            if (
                baseline_entry.sha256 != destination_entry.sha256
                or baseline_entry.size_bytes != destination_entry.size_bytes
            ):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Patch rejected: a move destination must preserve exact bytes.",
                )
            normalized_moves[normalized_source] = normalized_destination

        entries: list[WorkspacePatchEntry] = []
        moved_destinations = set(normalized_moves.values())
        for source, destination in normalized_moves.items():
            baseline_entry = baseline_by_path[source]
            entries.append(
                WorkspacePatchEntry(
                    operation="move",
                    path=destination,
                    source_path=source,
                    baseline_digest=baseline_entry.sha256,
                )
            )
        for path, raw in result_by_path.items():
            if path in moved_destinations:
                continue
            base = baseline_by_path.get(path)
            if base is None:
                entries.append(
                    WorkspacePatchEntry(
                        operation="create",
                        path=path,
                        result_digest=raw.sha256,
                        result_size_bytes=raw.size_bytes,
                        result_ref=raw.payload_ref,
                    )
                )
            elif base.sha256 != raw.sha256:
                entries.append(
                    WorkspacePatchEntry(
                        operation="replace",
                        path=path,
                        baseline_digest=base.sha256,
                        result_digest=raw.sha256,
                        result_size_bytes=raw.size_bytes,
                        result_ref=raw.payload_ref,
                    )
                )
        changed_bytes = sum(
            raw.size_bytes
            for path, raw in result_by_path.items()
            if path in moved_destinations
            or path not in baseline_by_path
            or baseline_by_path[path].sha256 != raw.sha256
        )
        if changed_bytes > enforced_limits.download_changed_bytes:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                "Patch rejected: changed output bytes exceed the ceiling.",
            )
        for path, base in baseline_by_path.items():
            if path not in result_by_path and path not in normalized_moves:
                entries.append(
                    WorkspacePatchEntry(
                        operation="delete",
                        path=path,
                        baseline_digest=base.sha256,
                    )
                )
        for path in normalized_directories:
            entries.append(WorkspacePatchEntry(operation="mkdir", path=path))

        if len(entries) > enforced_limits.download_file_count:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                "Patch rejected: changed entry count exceeds the ceiling.",
            )
        ordered = tuple(
            sorted(
                entries,
                key=lambda entry: (
                    entry.path,
                    entry.operation,
                    entry.source_path or "",
                ),
            )
        )
        patch_sha = cls._hash_patch(baseline.manifest_sha256, ordered, complete)
        return WorkspacePatchManifest(
            session_id=(
                baseline.snapshot_id
                if isinstance(baseline, SandboxSnapshot)
                else baseline.workspace_id
            ),
            baseline_manifest_sha256=baseline.manifest_sha256,
            entries=ordered,
            complete=complete,
            manifest_sha256=patch_sha,
        )

    @classmethod
    def verify_patch(
        cls,
        manifest: WorkspacePatchManifest,
        *,
        require_complete: bool = False,
    ) -> None:
        """Validate a patch before any C1/C3 handoff.

        This is intentionally independent of an ambient host filesystem.  The
        future workspace executor rechecks its overlay baseline separately.
        """

        try:
            manifest = WorkspacePatchManifest.model_validate(manifest.model_dump())
        except ValidationError as exc:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_INVALID,
                "Sandbox patch contains invalid operation evidence.",
            ) from exc
        if require_complete and not manifest.complete:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PATCH_INCOMPLETE,
                "Sandbox patch collection was incomplete and cannot be staged.",
            )
        paths: set[str] = set()
        move_sources: set[str] = set()
        for entry in manifest.entries:
            normalized = WorkspacePathValidator.normalize(entry.path)
            if (
                normalized != entry.path
                or WorkspacePathValidator.is_excluded(normalized)
                or normalized in paths
            ):
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: invalid or duplicate target path.",
                )
            paths.add(normalized)
            if entry.source_path is not None:
                source = WorkspacePathValidator.normalize(entry.source_path)
                if source != entry.source_path or source in move_sources:
                    raise SandboxError(
                        SandboxErrorCode.SNAPSHOT_INVALID,
                        "Patch rejected: invalid or duplicate move source.",
                    )
                move_sources.add(source)
        if (
            cls._hash_patch(
                manifest.baseline_manifest_sha256, manifest.entries, manifest.complete
            )
            != manifest.manifest_sha256
        ):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox patch manifest verification failed.",
            )

    @staticmethod
    def _hash_patch(
        baseline_sha: str,
        entries: Sequence[WorkspacePatchEntry],
        complete: bool,
    ) -> str:
        hasher = hashlib.sha256()
        hasher.update(f"{baseline_sha}\x00{int(complete)}\n".encode("utf-8"))
        for entry in sorted(
            entries,
            key=lambda entry: (entry.path, entry.operation, entry.source_path or ""),
        ):
            line = (
                f"{entry.operation}\x00{entry.path}\x00"
                f"{entry.source_path or ''}\x00{entry.baseline_digest or ''}\x00"
                f"{entry.baseline_identity or ''}\x00{entry.result_digest or ''}\x00"
                f"{entry.result_size_bytes if entry.result_size_bytes is not None else ''}\n"
            )
            hasher.update(line.encode("utf-8"))
        return hasher.hexdigest()
