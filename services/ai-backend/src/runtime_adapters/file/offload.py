"""Offload writer that parks oversized payloads in the content-addressed store.

This is the write half of the desktop offload seam. A caller (the worker's
tool-result processor) hands a large payload string to :class:`FileOffloadWriter`;
the writer stores it once in the :class:`FileObjectStore` under
``objects/sha256/<hash>`` and returns a stable virtual reference of the form
``/large_tool_results/<sha256>``.

The reference is a plain string so it drops straight into the existing offload
contract (:class:`~agent_runtime.context.memory.contracts.ManagedContextPayload`,
whose ``reference`` field validates against the memory-path grammar — which the
``/large_tool_results/<hex>`` shape satisfies). The *read* half is
:class:`~runtime_adapters.file.large_tool_result_backend.FileLargeToolResultBackend`,
which resolves the same reference back through the object store.
"""

from __future__ import annotations

from agent_runtime.api.constants import Values
from runtime_adapters.file.object_store import FileObjectStore


class FileOffloadWriter:
    """Callable ``OffloadWriter`` backed by the file store's object store.

    Matches the ``OffloadWriter = Callable[[str], str]`` alias used by
    ``ContextPayloadManager.prepare_tool_output`` — instances are called with
    the full content and return the virtual reference to store in the payload.
    """

    # Short inline peek persisted alongside the blob (never authoritative).
    _OBJECT_PREVIEW_CHARS = 200
    _TEXT_MEDIA_TYPE = "text/plain; charset=utf-8"

    def __init__(self, object_store: FileObjectStore) -> None:
        self._object_store = object_store

    def __call__(self, content: str) -> str:
        """Store ``content`` and return its ``/large_tool_results/<sha256>`` ref."""

        data = content.encode("utf-8")
        ref = self._object_store.put(
            data,
            media_type=self._TEXT_MEDIA_TYPE,
            preview=content[: self._OBJECT_PREVIEW_CHARS] or None,
        )
        return f"{Values.VirtualPath.LARGE_TOOL_RESULTS_PREFIX}{ref.sha256}"


__all__ = ("FileOffloadWriter",)
