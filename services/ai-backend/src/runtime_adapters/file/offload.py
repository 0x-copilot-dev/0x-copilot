"""Offload writer that parks oversized payloads in the content-addressed store.

This is the write half of the desktop offload seam. A caller (the worker's
tool-result processor) hands a large payload string to :class:`FileOffloadWriter`;
the writer stores it once in the :class:`FileObjectStore` under
``objects/sha256/<hash>`` and returns a stable virtual reference of the form
``/large_tool_results/<sha256>``.

The digest, the locator, the bounded preview, and the media type are not decided
here — they come from
:class:`~runtime_adapters.offload.ContentAddressedOffloadWriter`, the contract
every store's writer shares, so the desktop and a server deployment bound the
same oversized result identically. This class contributes only *where the bytes
land*.

The reference is a plain string so it drops straight into the existing offload
contract (:class:`~agent_runtime.context.memory.contracts.ManagedContextPayload`,
whose ``reference`` field validates against the memory-path grammar — which the
``/large_tool_results/<hex>`` shape satisfies). The *read* half for the deep
agent is
:class:`~runtime_adapters.file.large_tool_result_backend.FileLargeToolResultBackend`,
which resolves the same reference back through the object store;
:meth:`FileOffloadWriter.read_offloaded` is the direct, backend-agnostic sibling
used to verify a payload survived.
"""

from __future__ import annotations

from runtime_adapters.file.object_store import FileObjectStore, ObjectStoreError
from runtime_adapters.offload import (
    ContentAddressedOffloadWriter,
    OffloadedPayloadFact,
    OffloadReadError,
)


class FileOffloadWriter(ContentAddressedOffloadWriter):
    """Callable ``OffloadWriter`` backed by the file store's object store.

    Matches the ``OffloadWriter = Callable[[str], str]`` alias used by
    ``ContextPayloadManager.prepare_tool_output`` — instances are called with
    the full content and return the virtual reference to store in the payload.
    """

    def __init__(self, object_store: FileObjectStore) -> None:
        self._object_store = object_store

    def _persist(self, data: bytes, *, fact: OffloadedPayloadFact) -> str:
        ref = self._object_store.put(
            data,
            media_type=fact.media_type,
            preview=fact.preview,
        )
        return ref.sha256

    def read_offloaded(self, reference: str) -> bytes:
        """Return the exact bytes behind ``reference``."""

        digest = self.digest_for(reference)
        if digest is None:
            raise OffloadReadError(
                f"Not a large-tool-result reference: {reference!r}",
            )
        try:
            return self._object_store.get(digest)
        except ObjectStoreError as exc:
            raise OffloadReadError(
                f"Large tool result not found: {reference!r}"
            ) from exc


__all__ = ("FileOffloadWriter",)
