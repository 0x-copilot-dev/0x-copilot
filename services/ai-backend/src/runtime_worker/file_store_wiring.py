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

    def host_write_journal(
        self, *, org_id: str, conversation_id: str, run_id: str
    ) -> object | None:
        """Return this run's agent-write undo journal, or ``None`` elsewhere.

        Gated on the same duck-typed file store as everything else here, and for
        a stronger reason than convention: the journal's whole job is to hold
        the pre-image BYTES, and the object store that can hold them exists only
        on this backend. A journal without one would record that a file changed
        and be unable to put it back — a list of regrets rather than an undo.

        Bound to the run here so the floor only ever hands it a path: the floor
        is composed once per harness and has no run identity of its own, and
        threading the identity through the backend composition instead would put
        it on every non-desktop image for nothing.
        """

        store = self.file_store()
        if store is None:
            return None
        from agent_runtime.capabilities.desktop.write_journal import (  # noqa: PLC0415
            HostWriteJournal,
        )
        from runtime_adapters.file import (  # noqa: PLC0415
            FileHostWriteJournalStore,
        )

        return HostWriteJournal(
            FileHostWriteJournalStore(store.layout, store.object_store),
            org_id=org_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )


__all__ = ("FileStoreWorkerWiring",)
