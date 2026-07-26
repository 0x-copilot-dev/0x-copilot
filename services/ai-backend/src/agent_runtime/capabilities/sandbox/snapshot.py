"""Immutable, reference-only inputs for a sandbox operation.

This module deliberately models the hand-off *before* provider execution.  It
does not read a local filesystem, accept a broker handle, or materialize a
workspace.  C1/A2 composition resolves the approved artifact or overlay
references through :class:`SandboxSnapshotFileStorePort`; D3 receives only the
resulting immutable manifest and virtual ``/workspace`` paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
import re
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.sandbox.contracts import SandboxError, SandboxErrorCode
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec

SANDBOX_VIRTUAL_ROOT = "/workspace"
_ARTIFACT_BLOB_REF = re.compile(r"^artifact-blob://sha256/[0-9a-f]{64}$")
_OVERLAY_REF = re.compile(
    r"^workspace-overlay://runs/([A-Za-z0-9._-]{1,255})/versions/([1-9][0-9]*)$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_REF_LENGTH = 2048


class SandboxSnapshotSourceKind(StrEnum):
    """The only authority-free sources that may enter a snapshot."""

    ARTIFACT = "artifact"
    OVERLAY = "overlay"


def normalize_sandbox_virtual_path(value: str) -> str:
    """Validate one canonical provider-visible path below ``/workspace``.

    A leading slash is valid here only because it names the sandbox's virtual
    root.  It never denotes a host path.  The strict root check also prevents a
    caller from smuggling a local absolute path into an otherwise path-like
    field.
    """

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 4096
        or "\x00" in value
        or "\\" in value
        or not value.startswith(f"{SANDBOX_VIRTUAL_ROOT}/")
        or value.startswith("//")
    ):
        raise ValueError("sandbox paths must be canonical virtual /workspace paths")
    segments = value.split("/")[2:]
    if not segments or any(
        not segment or segment in {".", ".."} for segment in segments
    ):
        raise ValueError("sandbox paths must not contain traversal segments")
    if any(re.fullmatch(r"[A-Za-z]:", segment) for segment in segments):
        raise ValueError("sandbox paths must not contain host drive segments")
    return value


def _validate_opaque_ref(value: str, *, label: str) -> str:
    lowered = value.lower() if isinstance(value, str) else ""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_REF_LENGTH
        or value != value.strip()
        or value.startswith(("/", "~", "\\"))
        or lowered.startswith(("file://", "filesystem://"))
        or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{label} must be an opaque immutable reference")
    return value


def _validate_source_ref(kind: SandboxSnapshotSourceKind, value: str) -> str:
    _validate_opaque_ref(value, label="snapshot source_ref")
    if kind is SandboxSnapshotSourceKind.ARTIFACT:
        try:
            ArtifactContentRefCodec.parse(value)
        except Exception as exc:  # ValueError shape is the public boundary.
            raise ValueError(
                "artifact snapshots require an artifact revision ref"
            ) from exc
        return value
    if _OVERLAY_REF.fullmatch(value) is None:
        raise ValueError("overlay snapshots require an immutable overlay version ref")
    return value


def _validate_content_ref(value: str) -> str:
    _validate_opaque_ref(value, label="snapshot content_ref")
    if _ARTIFACT_BLOB_REF.fullmatch(value) is not None:
        return value
    try:
        ArtifactContentRefCodec.parse(value)
    except Exception as exc:  # ValueError shape is the public boundary.
        raise ValueError(
            "snapshot content_ref must be immutable artifact content"
        ) from exc
    return value


class SandboxSnapshotSource(RuntimeContract):
    """One authorized source declaration, with no physical path or bytes."""

    kind: SandboxSnapshotSourceKind
    source_ref: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)

    @model_validator(mode="after")
    def _immutable_source_ref(self) -> "SandboxSnapshotSource":
        _validate_source_ref(self.kind, self.source_ref)
        return self


class SandboxSnapshotInput(RuntimeContract):
    """A requested virtual destination and its authorized source reference."""

    virtual_path: str = Field(min_length=1, max_length=4096)
    source: SandboxSnapshotSource
    executable: bool = False

    @field_validator("virtual_path")
    @classmethod
    def _virtual_path_only(cls, value: str) -> str:
        return normalize_sandbox_virtual_path(value)


class SandboxSnapshotPlan(RuntimeContract):
    """Trusted C1/A2 snapshot selection before byte metadata is resolved."""

    entries: tuple[SandboxSnapshotInput, ...] = ()

    @model_validator(mode="after")
    def _distinct_paths(self) -> "SandboxSnapshotPlan":
        paths = tuple(entry.virtual_path for entry in self.entries)
        if len(paths) != len(set(paths)):
            raise ValueError("sandbox snapshot paths must be unique")
        return self


class SandboxResolvedSnapshotSource(RuntimeContract):
    """Exact immutable content metadata returned by the pending A2/C1 seam."""

    kind: SandboxSnapshotSourceKind
    source_ref: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    content_ref: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _refs_are_immutable(self) -> "SandboxResolvedSnapshotSource":
        _validate_source_ref(self.kind, self.source_ref)
        _validate_content_ref(self.content_ref)
        if (
            self.content_ref.startswith("artifact-blob://sha256/")
            and self.content_ref.rsplit("/", 1)[1] != self.content_digest
        ):
            raise ValueError("snapshot blob reference must match content_digest")
        return self


class SandboxSnapshotEntry(RuntimeContract):
    """One fully resolved, provider-safe file in an immutable snapshot."""

    virtual_path: str = Field(min_length=1, max_length=4096)
    source_kind: SandboxSnapshotSourceKind
    source_ref: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    content_ref: str = Field(min_length=1, max_length=_MAX_REF_LENGTH)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    executable: bool = False

    @field_validator("virtual_path")
    @classmethod
    def _virtual_path_only(cls, value: str) -> str:
        return normalize_sandbox_virtual_path(value)

    @model_validator(mode="after")
    def _refs_are_immutable(self) -> "SandboxSnapshotEntry":
        _validate_source_ref(self.source_kind, self.source_ref)
        _validate_content_ref(self.content_ref)
        return self


class SandboxSnapshotManifest(RuntimeContract):
    """Deterministic, bounded manifest handed to a sandbox execution gateway."""

    format_version: Literal[1] = 1
    snapshot_id: str = Field(pattern=r"^sandbox-snapshot:[0-9a-f]{64}$")
    entries: tuple[SandboxSnapshotEntry, ...] = ()
    total_bytes: int = Field(ge=0)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _is_canonical(self) -> "SandboxSnapshotManifest":
        paths = tuple(entry.virtual_path for entry in self.entries)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("sandbox snapshot entries must be sorted and unique")
        if sum(entry.size_bytes for entry in self.entries) != self.total_bytes:
            raise ValueError("sandbox snapshot byte total does not match entries")
        expected = self._digest(self.entries)
        if (
            self.manifest_digest != expected
            or self.snapshot_id != f"sandbox-snapshot:{expected}"
        ):
            raise ValueError("sandbox snapshot identity does not match manifest")
        return self

    @classmethod
    def from_entries(
        cls, entries: tuple[SandboxSnapshotEntry, ...]
    ) -> "SandboxSnapshotManifest":
        ordered = tuple(sorted(entries, key=lambda entry: entry.virtual_path))
        digest = cls._digest(ordered)
        return cls(
            snapshot_id=f"sandbox-snapshot:{digest}",
            entries=ordered,
            total_bytes=sum(entry.size_bytes for entry in ordered),
            manifest_digest=digest,
        )

    @staticmethod
    def _digest(entries: tuple[SandboxSnapshotEntry, ...]) -> str:
        return canonical_json_sha256(
            {
                "format_version": 1,
                "entries": [entry.model_dump(mode="json") for entry in entries],
                "total_bytes": sum(entry.size_bytes for entry in entries),
            }
        )


class SandboxSnapshotLimits(RuntimeContract):
    """Hard limits that are rechecked before the provider sees a manifest."""

    max_entries: int = Field(default=10_000, ge=1, le=25_000)
    max_total_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    max_entry_bytes: int = Field(default=64 * 1024 * 1024, ge=1)


@runtime_checkable
class SandboxSnapshotFileStorePort(Protocol):
    """Pending A2/C1 composition boundary for immutable snapshot content.

    Implementations resolve a ref from an artifact revision or a versioned
    overlay, then later stream the resulting immutable content ref.  Neither
    method accepts a host path, a broker grant, nor a live workspace mount.
    """

    async def resolve(
        self, *, source: SandboxSnapshotSource, virtual_path: str
    ) -> SandboxResolvedSnapshotSource | None:
        """Resolve one source at its exact virtual path to immutable content.

        ``virtual_path`` is required for C1: a versioned overlay reference names
        one multi-file manifest and the virtual path selects its one file entry.
        Artifact revisions ignore the path but receive the same complete input.
        """
        ...

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        """Stream exactly the content previously returned by ``resolve``."""
        ...


@runtime_checkable
class SandboxSnapshotPlanProvider(Protocol):
    """Trusted selector for the snapshot already authorized for one run."""

    async def snapshot_for(
        self,
        *,
        run_id: str,
        org_id: str | None,
        user_id: str | None,
    ) -> SandboxSnapshotPlan:
        """Return immutable artifact/overlay references and virtual paths only."""
        ...


class SandboxSnapshotBuilder:
    """Resolve reference-only selections into a verified immutable manifest."""

    @classmethod
    async def materialize(
        cls,
        *,
        plan: SandboxSnapshotPlan,
        store: SandboxSnapshotFileStorePort,
        limits: SandboxSnapshotLimits,
    ) -> SandboxSnapshotManifest:
        # A command without a selected input set is never an empty-workspace
        # convenience.  C1 must explicitly authorize at least one immutable
        # input before D3 can construct a provider-facing manifest.  Keep this
        # check at the reference-selection boundary so no resolver/provider
        # sees a zero-input plan.
        if not plan.entries:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            )
        entries: list[SandboxSnapshotEntry] = []
        total_bytes = 0
        for item in plan.entries:
            resolved = await store.resolve(
                source=item.source, virtual_path=item.virtual_path
            )
            if resolved is None:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                    "An authorized immutable sandbox snapshot is unavailable.",
                )
            if (
                resolved.kind is not item.source.kind
                or resolved.source_ref != item.source.source_ref
            ):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot source resolution did not match the request.",
                )
            if resolved.size_bytes > limits.max_entry_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Sandbox snapshot contains a file above the allowed size.",
                )
            total_bytes += resolved.size_bytes
            if total_bytes > limits.max_total_bytes:
                raise SandboxError(
                    SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                    "Sandbox snapshot exceeds the allowed total size.",
                )
            entries.append(
                SandboxSnapshotEntry(
                    virtual_path=item.virtual_path,
                    source_kind=resolved.kind,
                    source_ref=resolved.source_ref,
                    content_ref=resolved.content_ref,
                    content_digest=resolved.content_digest,
                    size_bytes=resolved.size_bytes,
                    executable=item.executable,
                )
            )
        if len(entries) > limits.max_entries:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                "Sandbox snapshot exceeds the allowed entry count.",
            )
        return SandboxSnapshotManifest.from_entries(tuple(entries))


__all__ = (
    "SANDBOX_VIRTUAL_ROOT",
    "SandboxResolvedSnapshotSource",
    "SandboxSnapshotBuilder",
    "SandboxSnapshotEntry",
    "SandboxSnapshotFileStorePort",
    "SandboxSnapshotInput",
    "SandboxSnapshotLimits",
    "SandboxSnapshotManifest",
    "SandboxSnapshotPlan",
    "SandboxSnapshotPlanProvider",
    "SandboxSnapshotSource",
    "SandboxSnapshotSourceKind",
    "normalize_sandbox_virtual_path",
)
