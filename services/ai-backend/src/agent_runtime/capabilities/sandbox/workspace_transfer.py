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

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfile
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxError,
    SandboxErrorCode,
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
            if raw.is_symlink or raw.is_special:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Snapshot rejected: only regular files may be uploaded.",
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
        baseline: WorkspaceTransferManifest,
        result_entries: Mapping[str, RawSnapshotEntry],
        complete: bool = True,
    ) -> WorkspacePatchManifest:
        """Return a typed patch from baseline → result.

        ``result_entries`` is keyed by *raw* path; each is normalized/validated
        here. ``complete=False`` marks a partial download whose patch must not
        be applied.
        """

        baseline_by_path = {entry.path: entry for entry in baseline.entries}
        result_by_path: dict[str, RawSnapshotEntry] = {}
        for raw in result_entries.values():
            if raw.is_symlink or raw.is_special:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_INVALID,
                    "Patch rejected: only regular files may be downloaded.",
                )
            normalized = WorkspacePathValidator.normalize(raw.path)
            if WorkspacePathValidator.is_excluded(normalized):
                continue
            result_by_path[normalized] = raw

        entries: list[WorkspacePatchEntry] = []
        for path, raw in result_by_path.items():
            base = baseline_by_path.get(path)
            if base is None:
                entries.append(
                    WorkspacePatchEntry(
                        operation="add",
                        path=path,
                        result_sha256=raw.sha256,
                        result_size_bytes=raw.size_bytes,
                        payload_ref=raw.payload_ref,
                    )
                )
            elif base.sha256 != raw.sha256:
                entries.append(
                    WorkspacePatchEntry(
                        operation="modify",
                        path=path,
                        baseline_sha256=base.sha256,
                        result_sha256=raw.sha256,
                        result_size_bytes=raw.size_bytes,
                        payload_ref=raw.payload_ref,
                    )
                )
        for path, base in baseline_by_path.items():
            if path not in result_by_path:
                entries.append(
                    WorkspacePatchEntry(
                        operation="delete",
                        path=path,
                        baseline_sha256=base.sha256,
                    )
                )

        ordered = tuple(sorted(entries, key=lambda e: (e.operation, e.path)))
        patch_sha = cls._hash_patch(baseline.manifest_sha256, ordered, complete)
        return WorkspacePatchManifest(
            session_id=baseline.workspace_id,
            baseline_manifest_sha256=baseline.manifest_sha256,
            entries=ordered,
            complete=complete,
            manifest_sha256=patch_sha,
        )

    @staticmethod
    def _hash_patch(
        baseline_sha: str,
        entries: Sequence[WorkspacePatchEntry],
        complete: bool,
    ) -> str:
        hasher = hashlib.sha256()
        hasher.update(f"{baseline_sha}\x00{int(complete)}\n".encode("utf-8"))
        for entry in sorted(entries, key=lambda e: (e.operation, e.path)):
            line = (
                f"{entry.operation}\x00{entry.path}\x00"
                f"{entry.baseline_sha256 or ''}\x00{entry.result_sha256 or ''}\n"
            )
            hasher.update(line.encode("utf-8"))
        return hasher.hexdigest()
