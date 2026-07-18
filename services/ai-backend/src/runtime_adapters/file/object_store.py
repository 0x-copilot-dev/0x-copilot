"""Content-addressed blob store for large runtime payloads.

Large tool results and offloaded context payloads (offload wiring is a
follow-up PR — see the module TODOs) are stored once, keyed by the SHA-256 of
their bytes, under ``objects/sha256/<hh>/<hash>``. Writes are atomic
(temp-write + fsync + rename) and reads verify the digest so a corrupted blob
is never silently returned.

This module provides only the durable store plus a typed reference shape
(:class:`ObjectRef`). No caller in this PR offloads into it yet; the seam is
here for the follow-up that wires ``ContextPayloadManager`` / Deep Agents'
``CompositeBackend`` ``/large_tool_results/`` reads to it.
"""

from __future__ import annotations

import hashlib
import os

from pydantic import BaseModel, ConfigDict, Field

from runtime_adapters.file._paths import FileStoreLayout


class ObjectStoreError(RuntimeError):
    """Raised when a stored blob fails its integrity check on read."""


class ObjectRef(BaseModel):
    """Typed reference to a content-addressed blob.

    Minimal shape (``AC1``'s ``ArtifactRefV1`` does not exist yet); a follow-up
    can widen this or swap it for the shared contract without touching the
    on-disk layout, since the ``sha256`` fully addresses the bytes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(min_length=64, max_length=64)
    size: int = Field(ge=0)
    media_type: str = "application/octet-stream"
    # Short inline preview (e.g. first bytes decoded) for UIs that want a peek
    # without fetching the whole blob. Never authoritative.
    preview: str | None = None


class FileObjectStore:
    """Atomic, verify-on-read content-addressed store under one root."""

    def __init__(self, layout: FileStoreLayout) -> None:
        self._layout = layout

    def put(
        self,
        data: bytes,
        *,
        media_type: str = "application/octet-stream",
        preview: str | None = None,
    ) -> ObjectRef:
        """Store ``data`` and return its :class:`ObjectRef`.

        Idempotent: writing identical bytes twice yields the same path and
        digest and never corrupts an existing blob (temp-write + rename).
        """

        digest = hashlib.sha256(data).hexdigest()
        target = self._layout.object_path(digest)
        if not target.exists():
            FileStoreLayout.ensure_dir(target.parent)
            tmp = target.with_name(target.name + ".tmp")
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            FileStoreLayout.restrict_file(target)
        return ObjectRef(
            sha256=digest,
            size=len(data),
            media_type=media_type,
            preview=preview,
        )

    def get(self, ref: ObjectRef | str) -> bytes:
        """Return the bytes for a ref, verifying the digest.

        Accepts either an :class:`ObjectRef` or a bare 64-char hex digest.
        Raises :class:`ObjectStoreError` on a missing or corrupted blob.
        """

        digest = ref.sha256 if isinstance(ref, ObjectRef) else ref
        path = self._layout.object_path(digest)
        if not path.exists():
            raise ObjectStoreError(f"object {digest} not found")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise ObjectStoreError(
                f"object {digest} failed integrity check (got {actual})"
            )
        return data

    def exists(self, ref: ObjectRef | str) -> bool:
        """Return ``True`` if the blob is present (no integrity check)."""

        digest = ref.sha256 if isinstance(ref, ObjectRef) else ref
        return self._layout.object_path(digest).exists()


__all__ = ("FileObjectStore", "ObjectRef", "ObjectStoreError")
