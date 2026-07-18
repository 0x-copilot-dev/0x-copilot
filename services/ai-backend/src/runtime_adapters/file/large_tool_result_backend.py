"""Read-only Deep Agents backend resolving ``/large_tool_results/<ref>`` blobs.

The read half of the desktop offload seam. Oversized tool output is parked in
the content-addressed :class:`FileObjectStore` by
:class:`~runtime_adapters.file.offload.FileOffloadWriter`, which hands back a
``/large_tool_results/<sha256>`` reference. When the supervisor (or a subagent)
calls ``read_file`` on that reference, deepagents' ``CompositeBackend`` routes it
here and we return the stored bytes verbatim.

Read-only by design — the object store is content-addressed, so a caller cannot
choose a path; writes are refused. The blob is durable (JSONL/object store are
canonical), so reads never depend on the disposable catalog index or in-graph
state.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.api.constants import Values
from runtime_adapters.file.object_store import FileObjectStore, ObjectStoreError

_PREFIX = Values.VirtualPath.LARGE_TOOL_RESULTS_PREFIX
# A blob reference is the 64-char hex sha256 digest, arriving either with the
# full ``/large_tool_results/`` prefix (direct callers / tests) or already
# stripped by ``CompositeBackend`` to ``/<sha>`` or ``<sha>``.
_SHA256 = re.compile(r"^/?(?:large_tool_results/)?(?P<sha>[0-9a-f]{64})/?$")
_READ_ONLY_ERROR = "The /large_tool_results/ store is read-only."


class FileLargeToolResultBackend(BackendProtocol):
    """Resolve offloaded large tool results from the content-addressed store."""

    PATH_PREFIX: str = _PREFIX

    def __init__(self, object_store: FileObjectStore) -> None:
        self._object_store = object_store

    # --- BackendProtocol surface -------------------------------------------

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Return the offloaded blob addressed by ``file_path`` (sync)."""

        return self._read(file_path)

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Return the offloaded blob addressed by ``file_path`` (async)."""

        return self._read(file_path)

    def _read(self, file_path: str) -> ReadResult:
        sha = self._sha_for(file_path)
        if sha is None:
            return ReadResult(error=f"Not a large-tool-result reference: {file_path}")
        try:
            data = self._object_store.get(sha)
        except ObjectStoreError:
            return ReadResult(error=f"Large tool result not found: {file_path}")
        return ReadResult(
            file_data={
                "content": data.decode("utf-8", errors="replace"),
                "encoding": "utf-8",
                "modified_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def ls(self, path: str) -> LsResult:
        """The content-addressed store is not enumerable by path."""

        return LsResult(entries=[])

    async def als(self, path: str) -> LsResult:
        """The content-addressed store is not enumerable by path."""

        return LsResult(entries=[])

    def write(self, file_path: str, content: str) -> WriteResult:
        """Reject writes — offloaded blobs are stored by the worker, not here."""

        return WriteResult(error=_READ_ONLY_ERROR)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Reject writes — offloaded blobs are stored by the worker, not here."""

        return WriteResult(error=_READ_ONLY_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Reject edits — offloaded blobs are immutable."""

        return EditResult(error=_READ_ONLY_ERROR)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Reject edits — offloaded blobs are immutable."""

        return EditResult(error=_READ_ONLY_ERROR)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Unsupported — blobs are addressed by digest, not searched."""

        return GrepResult(matches=[])

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Unsupported — blobs are addressed by digest, not searched."""

        return GrepResult(matches=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Unsupported — blobs are addressed by digest, not globbed."""

        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Unsupported — blobs are addressed by digest, not globbed."""

        return GlobResult(matches=[])

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _sha_for(file_path: str) -> str | None:
        """Extract the 64-char hex digest from a reference path, or ``None``."""

        if not file_path:
            return None
        match = _SHA256.match(file_path.strip())
        return match.group("sha") if match is not None else None


__all__ = ("FileLargeToolResultBackend",)
