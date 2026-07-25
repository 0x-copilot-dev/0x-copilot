"""Pure contracts for a run-scoped, durable virtual workspace overlay.

The overlay stores only canonical virtual paths and immutable content references.
Neither host paths nor file bytes belong in these records.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.workspace.errors import WorkspacePathError
from agent_runtime.execution.contracts import RuntimeContract

_WORKSPACE_ROOT = "/workspace"
_MAX_VIRTUAL_PATH_LENGTH = 4096
_MAX_VIRTUAL_PATH_DEPTH = 64
_MAX_SEGMENT_LENGTH = 255
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)


class WorkspaceEntryKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    TOMBSTONE = "tombstone"
    MOVE = "move"


class WorkspaceOperation(StrEnum):
    CREATE = "create"
    REPLACE = "replace"
    DELETE = "delete"
    MOVE = "move"
    MKDIR = "mkdir"


class BaseExistence(StrEnum):
    MUST_EXIST = "must_exist"
    MUST_NOT_EXIST = "must_not_exist"
    ANY = "any"


class OverlayMutationKind(StrEnum):
    UPSERT = "upsert"
    REMOVE = "remove"


def utc_now() -> datetime:
    """Return an aware UTC timestamp without reaching into an adapter."""

    return datetime.now(UTC)


def normalize_virtual_path(raw_path: str, *, allow_mount_root: bool = False) -> str:
    """Return one canonical ``/workspace/<mount>/...`` path.

    The desktop broker is deliberately not imported here.  This is the strict
    virtual-path boundary shared by overlay records and all in-memory tests.
    """

    if not isinstance(raw_path, str) or not raw_path:
        raise WorkspacePathError("Workspace path must be a non-empty virtual path.")
    if len(raw_path) > _MAX_VIRTUAL_PATH_LENGTH or "\x00" in raw_path:
        raise WorkspacePathError("Workspace path is invalid.")
    if "\\" in raw_path or raw_path.startswith("//"):
        raise WorkspacePathError("Workspace path is invalid.")
    normalized_unicode = unicodedata.normalize("NFC", raw_path)
    if not normalized_unicode.startswith(f"{_WORKSPACE_ROOT}/"):
        raise WorkspacePathError("Workspace path must remain under /workspace.")

    raw_segments = normalized_unicode.split("/")[2:]
    if not raw_segments or not raw_segments[0]:
        raise WorkspacePathError("Workspace path must include a mount.")
    if len(raw_segments) > _MAX_VIRTUAL_PATH_DEPTH:
        raise WorkspacePathError("Workspace path is too deep.")
    for segment in raw_segments:
        compatibility = unicodedata.normalize("NFKC", segment)
        if (
            not segment
            or segment in {".", ".."}
            or compatibility in {".", ".."}
            or len(segment) > _MAX_SEGMENT_LENGTH
            or _WINDOWS_RESERVED.fullmatch(segment) is not None
            or re.fullmatch(r"[A-Za-z]:", segment) is not None
        ):
            raise WorkspacePathError("Workspace path is invalid.")

    normalized = posixpath.normpath(normalized_unicode)
    if normalized != normalized_unicode or normalized == _WORKSPACE_ROOT:
        raise WorkspacePathError("Workspace path must be canonical.")
    if not allow_mount_root and normalized.count("/") < 3:
        raise WorkspacePathError("Workspace mutations require a path inside a mount.")
    return normalized


def mount_id_for_path(virtual_path: str) -> str:
    """Return the already-validated mount segment for a virtual path."""

    return normalize_virtual_path(virtual_path, allow_mount_root=True).split("/")[2]


def content_ref_for_blob(blob_key: str) -> str:
    """Create an opaque A2-blob reference; the key is never a filesystem path."""

    if _SHA256.fullmatch(blob_key) is None:
        raise ValueError("blob_key must be a sha256 digest")
    return f"artifact-blob://sha256/{blob_key}"


def blob_key_from_content_ref(content_ref: str) -> str:
    """Recover the A2 blob key from an overlay-private content reference."""

    prefix = "artifact-blob://sha256/"
    key = content_ref.removeprefix(prefix)
    if not content_ref.startswith(prefix) or _SHA256.fullmatch(key) is None:
        raise ValueError("content_ref is not a workspace artifact blob reference")
    return key


class WorkspaceBaseEntry(RuntimeContract):
    """A metadata-only entry returned by the read-only base capability."""

    virtual_path: str
    entry_kind: WorkspaceEntryKind
    opaque_generation: str | None = Field(default=None, min_length=1, max_length=1024)
    content_digest: str | None = None
    stable_file_id: str | None = Field(default=None, min_length=1, max_length=1024)
    byte_size: int | None = Field(default=None, ge=0)
    mtime_ns: int | None = Field(default=None, ge=0)

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_virtual_path(value, allow_mount_root=True)

    @field_validator("content_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError("content_digest must be a sha256 digest")
        return value

    @model_validator(mode="after")
    def _file_metadata_is_consistent(self) -> WorkspaceBaseEntry:
        if self.entry_kind is WorkspaceEntryKind.FILE and self.byte_size is None:
            raise ValueError("file entries require byte_size")
        return self


class WorkspaceBaseMatch(RuntimeContract):
    """One safe, line-oriented search hit from the base read capability."""

    virtual_path: str
    line_number: int = Field(ge=1)
    line_text: str

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_virtual_path(value)


class BasePrecondition(RuntimeContract):
    """Exact base state the eventual workspace executor must revalidate."""

    existence: BaseExistence
    entry_kind: WorkspaceEntryKind | None = None
    opaque_generation: str | None = Field(default=None, min_length=1, max_length=1024)
    content_digest: str | None = None
    stable_file_id: str | None = Field(default=None, min_length=1, max_length=1024)
    byte_size: int | None = Field(default=None, ge=0)
    mtime_ns: int | None = Field(default=None, ge=0)

    @field_validator("content_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError("content_digest must be a sha256 digest")
        return value

    @model_validator(mode="after")
    def _is_exact_when_required(self) -> BasePrecondition:
        if self.existence is BaseExistence.MUST_NOT_EXIST and any(
            value is not None
            for value in (
                self.entry_kind,
                self.opaque_generation,
                self.content_digest,
                self.stable_file_id,
                self.byte_size,
                self.mtime_ns,
            )
        ):
            raise ValueError("must_not_exist preconditions cannot carry base metadata")
        if self.existence is BaseExistence.MUST_EXIST and self.entry_kind is None:
            raise ValueError("must_exist preconditions require entry_kind")
        if (
            self.existence is BaseExistence.MUST_EXIST
            and self.entry_kind is WorkspaceEntryKind.FILE
            and self.content_digest is None
        ):
            raise ValueError("file overwrite preconditions require content_digest")
        return self


class OverlayEntry(RuntimeContract):
    """The one current overlay entry for a canonical virtual path."""

    virtual_path: str
    entry_kind: WorkspaceEntryKind
    operation: WorkspaceOperation
    content_ref: str | None = None
    content_digest: str | None = None
    byte_size: int | None = Field(default=None, ge=0)
    source_virtual_path: str | None = None
    baseline: BasePrecondition
    stage_id: str | None = Field(default=None, min_length=1, max_length=256)
    stage_revision: int | None = Field(default=None, ge=1)
    overlay_revision: int = Field(default=0, ge=0)
    author: str = Field(min_length=1, max_length=512)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_virtual_path(value)

    @field_validator("source_virtual_path")
    @classmethod
    def _canonical_source_path(cls, value: str | None) -> str | None:
        return normalize_virtual_path(value) if value is not None else None

    @field_validator("content_digest")
    @classmethod
    def _digest_is_sha256(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError("content_digest must be a sha256 digest")
        return value

    @model_validator(mode="after")
    def _entry_shape_matches_operation(self) -> OverlayEntry:
        content_values = (self.content_ref, self.content_digest, self.byte_size)
        if self.entry_kind is WorkspaceEntryKind.FILE:
            if any(value is None for value in content_values):
                raise ValueError(
                    "file overlay entries require an immutable content reference"
                )
        elif (
            self.entry_kind is WorkspaceEntryKind.MOVE
            and any(value is not None for value in content_values)
            and any(value is None for value in content_values)
        ):
            raise ValueError("move entries must carry a complete content reference")
        elif self.entry_kind is not WorkspaceEntryKind.MOVE and any(
            value is not None for value in content_values
        ):
            raise ValueError("non-file overlay entries cannot carry file bytes")
        if self.entry_kind is WorkspaceEntryKind.MOVE:
            if (
                self.operation is not WorkspaceOperation.MOVE
                or self.source_virtual_path is None
            ):
                raise ValueError(
                    "move entries require a move operation and source path"
                )
        elif self.source_virtual_path is not None:
            raise ValueError("only move entries may carry source_virtual_path")
        if self.entry_kind is WorkspaceEntryKind.TOMBSTONE and self.operation not in {
            WorkspaceOperation.DELETE,
            WorkspaceOperation.MOVE,
        }:
            raise ValueError("tombstones must be delete or move operations")
        if (
            self.entry_kind is WorkspaceEntryKind.DIRECTORY
            and self.operation is not WorkspaceOperation.MKDIR
        ):
            raise ValueError("directory overlay entries must be mkdir operations")
        if (self.stage_id is None) != (self.stage_revision is None):
            raise ValueError("stage_id and stage_revision must be supplied together")
        return self


class OverlayManifest(RuntimeContract):
    """The immutable current view of one run's overlay metadata."""

    run_id: str = Field(min_length=1, max_length=255)
    version: int = Field(default=0, ge=0)
    entries: tuple[OverlayEntry, ...] = ()
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _entries_are_unique_and_sorted(self) -> OverlayManifest:
        paths = tuple(entry.virtual_path for entry in self.entries)
        if len(paths) != len(set(paths)):
            raise ValueError("overlay manifest cannot contain duplicate paths")
        if paths != tuple(sorted(paths)):
            raise ValueError("overlay manifest entries must be sorted by virtual_path")
        if any(entry.overlay_revision > self.version for entry in self.entries):
            raise ValueError("overlay entry revision cannot exceed manifest version")
        return self

    def entry_at(self, virtual_path: str) -> OverlayEntry | None:
        canonical = normalize_virtual_path(virtual_path, allow_mount_root=True)
        return next(
            (entry for entry in self.entries if entry.virtual_path == canonical), None
        )


class OverlayMutation(RuntimeContract):
    """One atomically applied metadata change in a manifest revision."""

    kind: OverlayMutationKind
    virtual_path: str
    entry: OverlayEntry | None = None

    @field_validator("virtual_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_virtual_path(value)

    @model_validator(mode="after")
    def _mutation_has_expected_entry(self) -> OverlayMutation:
        if self.kind is OverlayMutationKind.UPSERT:
            if self.entry is None or self.entry.virtual_path != self.virtual_path:
                raise ValueError("upsert mutation entry must match virtual_path")
        elif self.entry is not None:
            raise ValueError("remove mutations cannot carry an entry")
        return self


class WorkspaceMutationResult(RuntimeContract):
    """A structured staged-in-overlay outcome; it never claims a host write."""

    entry: OverlayEntry | None = None
    manifest: OverlayManifest
    message: str = "Change staged in workspace overlay; the host was not modified."


def sha256_digest(body: bytes) -> str:
    """Return the canonical content digest used by base preconditions and blobs."""

    return hashlib.sha256(body).hexdigest()


__all__ = (
    "BaseExistence",
    "BasePrecondition",
    "OverlayEntry",
    "OverlayManifest",
    "OverlayMutation",
    "OverlayMutationKind",
    "WorkspaceBaseEntry",
    "WorkspaceBaseMatch",
    "WorkspaceEntryKind",
    "WorkspaceMutationResult",
    "WorkspaceOperation",
    "blob_key_from_content_ref",
    "content_ref_for_blob",
    "mount_id_for_path",
    "normalize_virtual_path",
    "sha256_digest",
)
