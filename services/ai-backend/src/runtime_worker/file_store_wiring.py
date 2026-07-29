"""Single source of truth for wiring the desktop file store into a worker run.

Both the initial-run path (:mod:`runtime_worker.handlers.run`) and the
approval-resume path (:mod:`runtime_worker.handlers.approval`) must offload
oversized tool results to the object store and compose the file-native read
backends (``/subagents/`` traces, ``/large_tool_results/`` blobs) onto the deep
agent. Bug R1 was exactly the two paths drifting: the resume path skipped this
seam, so after an approval a large tool result was persisted inline and a
pre-pause ``/large_tool_results/`` or ``/subagents/`` reference was unreadable.
Keeping the gate + builders here means the paths cannot drift again.

Everything is **gated on the duck-typed file store**: the event store is the
file adapter only when it exposes both an ``object_store`` and a ``layout``. On
the web / postgres / in-memory images this returns ``None`` everywhere, so the
offloader stays ``None`` (inline behavior, byte-identical) and no read routes are
added. The file adapter (and its object-store / sqlite deps) is imported lazily
so it never loads on those images.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
    from runtime_worker.tool_result_offload import ToolResultOffloader


class FileStoreWorkerWiring:
    """Gate + builders for the desktop file-store offloader and read backends.

    Constructed from the worker's ``event_store``; all methods are ``None``-safe
    no-ops on non-file backends.
    """

    def __init__(self, event_store: object) -> None:
        self._event_store = event_store
        self._tool_result_admission: ToolResultAdmissionAdapter | None = None
        self._tool_result_offloader: ToolResultOffloader | None = None

    def file_store(self) -> object | None:
        """Return the active file store, or ``None`` on non-file backends.

        Duck-typed on the object store + layout the file adapter exposes so the
        worker's hot path never imports the desktop-only file backend on the
        web / postgres / in-memory images.
        """

        store = self._event_store
        if hasattr(store, "object_store") and hasattr(store, "layout"):
            return store
        return None

    def tool_result_offloader(self) -> ToolResultOffloader | None:
        """Construct the file-store tool-result offloader, or ``None`` elsewhere."""

        store = self.file_store()
        if store is None:
            return None
        self._ensure_tool_result_admission(store)
        return self._tool_result_offloader

    def tool_result_admission(self) -> ToolResultAdmissionAdapter | None:
        """Return the pre-model admission adapter, or ``None`` elsewhere."""

        store = self.file_store()
        if store is None:
            return None
        self._ensure_tool_result_admission(store)
        return self._tool_result_admission

    def schema_artifact_writer(self) -> object | None:
        """Return the raw object-store write half, or ``None`` elsewhere.

        F3.4 publishes an over-bound capability schema through the runtime's
        existing oversized-payload seam rather than through a store of its own,
        so this hands back the same ``OffloadWriter`` — a plain
        ``Callable[[str], str]`` — that parks an oversized tool result in the
        content-addressed object store and returns the ``/large_tool_results/``
        locator the ordinary read path already resolves.

        Deliberately *not* the tool-result offloader: that one applies a token
        policy and a projection record meant for model context, and a schema
        artifact is neither.  What is shared is the store and the locator
        format, which is the whole point — one place bytes go, one path they are
        read back through.  ``None`` off the file store means an over-bound
        schema is reported unavailable, never truncated.
        """

        store = self.file_store()
        if store is None:
            return None
        from runtime_adapters.file import FileOffloadWriter  # noqa: PLC0415

        return FileOffloadWriter(store.object_store)

    def discard_tool_result_projections(self, *, run_id: str) -> None:
        """Release bounded projection records left by an interrupted run."""

        adapter = self._tool_result_admission
        discard = getattr(adapter, "discard_projections", None)
        if callable(discard):
            discard(projection_key=run_id)

    def _ensure_tool_result_admission(self, store: object) -> None:
        """Build one writer/adapter/projector pipeline for this worker."""

        if self._tool_result_admission is not None:
            return
        # Lazy imports: the file adapter (and its sqlite/object-store deps) must
        # not load on the web/postgres images.
        from agent_runtime.context.tool_result_admission import (  # noqa: PLC0415
            ToolResultAdmissionAdapter,
        )
        from runtime_adapters.file import FileOffloadWriter  # noqa: PLC0415
        from runtime_worker.tool_result_offload import (  # noqa: PLC0415
            ToolResultOffloader,
        )

        adapter = ToolResultAdmissionAdapter(FileOffloadWriter(store.object_store))
        self._tool_result_admission = adapter
        self._tool_result_offloader = ToolResultOffloader(admission_adapter=adapter)

    def subagent_artifacts_backend(
        self, *, org_id: str, conversation_id: str
    ) -> object | None:
        """Return the file-native ``/subagents/`` trace backend, or ``None`` elsewhere.

        On non-file backends this is ``None``; callers that always need a
        subagent backend (the run path) fall back to the event-store projection.
        """

        store = self.file_store()
        if store is None:
            return None
        from runtime_adapters.file import FileSubagentTraceBackend  # noqa: PLC0415

        return FileSubagentTraceBackend(
            layout=store.layout,
            org_id=org_id,
            conversation_id=conversation_id,
        )

    def large_tool_results_backend(self) -> object | None:
        """Return the object-store ``/large_tool_results/`` backend, or ``None`` elsewhere."""

        store = self.file_store()
        if store is None:
            return None
        from runtime_adapters.file import FileLargeToolResultBackend  # noqa: PLC0415

        return FileLargeToolResultBackend(store.object_store)


__all__ = ("FileStoreWorkerWiring",)
