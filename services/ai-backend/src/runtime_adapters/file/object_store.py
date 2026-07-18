"""Content-addressed blob store for large runtime payloads.

Large tool results are stored once, keyed by the SHA-256 of their bytes, under
``objects/sha256/<hh>/<hash>``. Writes are atomic (temp-write + fsync + rename)
and reads verify the digest so a corrupted blob is never silently returned.

The offload seam this module was built for is now wired:
:class:`~runtime_adapters.file.offload.FileOffloadWriter` parks oversized tool
output here (via ``ContextPayloadManager``) and
:class:`~runtime_adapters.file.large_tool_result_backend.FileLargeToolResultBackend`
resolves Deep Agents' ``CompositeBackend`` ``/large_tool_results/<sha256>`` reads
back out of it. This module still owns only the durable store plus a typed
reference shape (:class:`ObjectRef`).
"""

from __future__ import annotations

import hashlib
import os

from pydantic import BaseModel, ConfigDict, Field

from runtime_adapters.file._capacity import QuotaGuard
from runtime_adapters.file._paths import FileStoreLayout


class ObjectStoreError(RuntimeError):
    """Raised when a stored blob fails its integrity check on read."""


class ObjectRef(BaseModel):
    """Typed reference to a content-addressed blob.

    Minimal shape (``AC1``'s ``ArtifactRefV1`` does not exist yet); a later PR
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

    def __init__(
        self, layout: FileStoreLayout, *, quota: QuotaGuard | None = None
    ) -> None:
        self._layout = layout
        # Unlimited by default: an omitted guard behaves exactly as before.
        self._quota = quota if quota is not None else QuotaGuard(layout)

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
            # Fail closed BEFORE writing a byte: a rejected admission leaves no
            # partial blob and no lingering .tmp sibling. Idempotent re-puts of
            # an already-stored digest never reach here, so they are exempt.
            self._quota.admit(len(data))
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

    def write_in_flight(self, ref: ObjectRef | str) -> bool:
        """Return ``True`` if a partial ``.tmp`` write for this digest exists.

        :meth:`put` writes to ``<path>.tmp`` then atomically renames it into
        place, so a lingering ``.tmp`` sibling is the single-writer desktop
        store's only "open handle" state. The garbage collector consults this
        to refuse deleting a blob whose bytes are still being written.
        """

        digest = ref.sha256 if isinstance(ref, ObjectRef) else ref
        target = self._layout.object_path(digest)
        return target.with_name(target.name + ".tmp").exists()

    def delete(self, ref: ObjectRef | str) -> bool:
        """Physically remove a blob, refusing to touch an in-flight write.

        Returns ``True`` when the blob file was unlinked, ``False`` when it was
        absent or a concurrent ``.tmp`` write made deletion unsafe. Best-effort
        prunes the now-empty ``<hh>`` shard directory. Callers own reachability:
        this only guards the open-handle case, never reference counting.
        """

        if self.write_in_flight(ref):
            return False
        digest = ref.sha256 if isinstance(ref, ObjectRef) else ref
        target = self._layout.object_path(digest)
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        try:
            target.parent.rmdir()
        except OSError:
            # Shard still holds other blobs (or vanished) — leave it be.
            pass
        return True

    def iter_digests(self) -> tuple[str, ...]:
        """Return every blob digest currently present under ``objects/sha256``."""

        objects_dir = self._layout.objects_dir
        if not objects_dir.exists():
            return ()
        digests: list[str] = []
        for shard in objects_dir.iterdir():
            if not shard.is_dir():
                continue
            for blob in shard.iterdir():
                if blob.is_file() and not blob.name.endswith(".tmp"):
                    digests.append(blob.name)
        return tuple(digests)


__all__ = ("FileObjectStore", "ObjectRef", "ObjectStoreError")
