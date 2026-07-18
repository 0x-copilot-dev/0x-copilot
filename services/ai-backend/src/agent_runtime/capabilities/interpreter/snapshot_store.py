"""Bounded, content-addressed snapshot persistence for code mode.

Interpreter state is RAM-only. At an external-function suspension point the
adapter serializes a snapshot; this module writes those bytes to a
content-addressed blob store (the AC4 object store in production) and returns a
small :class:`~agent_runtime.capabilities.interpreter.contracts.SnapshotRef`.

Two invariants live here:

* **Bounded** — bytes over the profile's ``max_snapshot_bytes`` are rejected with
  a typed ``SNAPSHOT_BYTES`` limit error; the interpreter never writes an
  unbounded blob.
* **Bound to the program** — the ref carries adapter / ABI / source hash /
  limit-profile hash / invocation index. :meth:`get` re-checks them so a resume
  cannot load a snapshot from a different adapter, program, or profile. An
  unsupported snapshot fails closed (``SNAPSHOT_INCOMPATIBLE``); it is never
  blind-loaded (PRD "unsupported snapshot -> restart, never blind-load").

The blob store is injected as a narrow structural type so this module does not
depend on the adapter layer directly; production wires the file object store.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_runtime.capabilities.interpreter.contracts import (
    InterpreterError,
    InterpreterErrorCode,
    InterpreterLimitKind,
    SnapshotRef,
)


@runtime_checkable
class ContentAddressedBlobStore(Protocol):
    """Structural view of a content-addressed blob store (e.g. AC4 object store).

    Matches ``runtime_adapters.file.object_store.FileObjectStore`` byte-for-byte
    at the call sites this module uses.
    """

    def put(
        self,
        data: bytes,
        *,
        media_type: str = ...,
        preview: str | None = ...,
    ) -> "_ObjectRefLike": ...

    def get(self, ref: object) -> bytes: ...


@runtime_checkable
class _ObjectRefLike(Protocol):
    sha256: str
    size: int


class ObjectStoreSnapshotStore:
    """Persists interpreter snapshots via a content-addressed blob store."""

    #: Snapshot blobs are opaque Monty bytes; mark them so a UI never tries to
    #: render them as text.
    MEDIA_TYPE = "application/x-monty-snapshot"

    def __init__(self, blob_store: ContentAddressedBlobStore) -> None:
        self._blob_store = blob_store

    def put(
        self,
        data: bytes,
        *,
        adapter: str,
        abi_version: str,
        source_sha256: str,
        limit_profile_hash: str,
        invocation_index: int,
        max_snapshot_bytes: int,
    ) -> SnapshotRef:
        """Store ``data`` under its digest, enforcing the size ceiling.

        Raises :class:`InterpreterError` (``resource_limit_exceeded`` /
        ``SNAPSHOT_BYTES``) if the serialized snapshot is over budget.
        """

        if len(data) > max_snapshot_bytes:
            raise InterpreterError(
                InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED,
                "interpreter snapshot exceeded the configured size limit",
                limit_kind=InterpreterLimitKind.SNAPSHOT_BYTES,
            )
        ref = self._blob_store.put(data, media_type=self.MEDIA_TYPE)
        return SnapshotRef(
            sha256=ref.sha256,
            size=ref.size,
            adapter=adapter,
            abi_version=abi_version,
            source_sha256=source_sha256,
            limit_profile_hash=limit_profile_hash,
            invocation_index=invocation_index,
        )

    def get(self, ref: SnapshotRef) -> bytes:
        """Fetch and integrity-check the bytes for ``ref``.

        The blob store verifies the digest on read; a missing or corrupted blob
        becomes a typed ``snapshot_invalid``. Envelope compatibility (adapter /
        ABI / source / profile) is the resumer's responsibility via
        :meth:`ensure_compatible`, called before this.
        """

        try:
            return self._blob_store.get(ref.sha256)
        except Exception as exc:  # noqa: BLE001 - convert to typed, safe error
            raise InterpreterError(
                InterpreterErrorCode.SNAPSHOT_INVALID,
                "interpreter snapshot is missing or failed its integrity check",
            ) from exc

    @staticmethod
    def ensure_compatible(
        ref: SnapshotRef,
        *,
        adapter: str,
        abi_version: str,
        source_sha256: str,
        limit_profile_hash: str,
    ) -> None:
        """Reject a resume whose snapshot was made by a different program/adapter.

        Any mismatch is a non-retryable ``snapshot_incompatible``: the caller
        must restart the segment rather than blind-load foreign state.
        """

        mismatched = (
            ref.adapter != adapter
            or ref.abi_version != abi_version
            or ref.source_sha256 != source_sha256
            or ref.limit_profile_hash != limit_profile_hash
        )
        if mismatched:
            raise InterpreterError(
                InterpreterErrorCode.SNAPSHOT_INCOMPATIBLE,
                "interpreter snapshot is not compatible with this session",
            )


__all__ = (
    "ContentAddressedBlobStore",
    "ObjectStoreSnapshotStore",
)
