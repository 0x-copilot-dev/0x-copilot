"""File-native Deep Agents backend for ``/subagents/<task_id>/`` reads.

The desktop counterpart to
:class:`~agent_runtime.context.memory.subagent_trace.SubagentArtifactsBackend`.
That backend projects the subagent trace on demand from the event-store *port*
(the disposable catalog index). This one reads the **canonical** append-only
JSONL directly off disk — the per-subagent ``subagents/<key>.jsonl`` files plus
the main ``events.jsonl`` — so a read never depends on the rebuildable index and
survives an ``index/`` wipe.

Projection is delegated to the shared
:class:`~agent_runtime.context.memory.subagent_trace.SubagentTraceProjector`, so
the virtual files (``conversation.md``, ``tool_calls.json``, ``summary.md``,
``events.jsonl``) render byte-for-byte the same as the port-backed backend — the
only thing that changes is where the envelopes come from. Reads of an
append-only file return the committed prefix, so no lock is needed (the desktop
worker is the single in-process writer).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.context.memory.subagent_trace import SubagentTraceProjector
from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.file._paths import FileStoreLayout
from runtime_api.schemas import RuntimeEventEnvelope


class _Files:
    """Virtual filenames served under each ``/subagents/<task_id>/`` directory.

    Mirrors ``agent_runtime.context.memory.subagent_trace._Files`` — kept local
    so this adapter does not import a private name across the domain boundary.
    """

    CONVERSATION = "conversation.md"
    TOOL_CALLS = "tool_calls.json"
    SUMMARY = "summary.md"
    EVENTS = "events.jsonl"

    ALL: tuple[str, ...] = (CONVERSATION, TOOL_CALLS, SUMMARY, EVENTS)


# Accept both the full ``/subagents/<task_id>/<file>`` form and the
# prefix-stripped ``/<task_id>/<file>`` form that ``CompositeBackend`` delivers.
_TASK_PATH = re.compile(r"^(?:/subagents)?/(?P<task_id>[^/]+)(?:/(?P<file>.+?))?/?$")
_ROOT_PATHS = frozenset({"/", "/subagents", "/subagents/"})
_READ_ONLY_ERROR = "The /subagents/ filesystem is read-only."


class FileSubagentTraceBackend(BackendProtocol):
    """Read-only ``/subagents/<task_id>/`` view sourced from canonical JSONL."""

    PATH_PREFIX: str = "/subagents/"

    def __init__(
        self,
        *,
        layout: FileStoreLayout,
        org_id: str,
        conversation_id: str,
    ) -> None:
        self._layout = layout
        self._org_id = org_id
        self._conversation_id = conversation_id

    # --- BackendProtocol surface -------------------------------------------

    def ls(self, path: str) -> LsResult:
        """List subagent directories / per-task files (sync)."""

        return self._ls(path)

    async def als(self, path: str) -> LsResult:
        """List subagent directories / per-task files (async)."""

        return self._ls(path)

    def _ls(self, path: str) -> LsResult:
        normalized = self._normalize_dir_path(path)
        events = self._collect_events()
        task_pairs = SubagentTraceProjector.list_task_ids_with_names(events)
        if normalized in _ROOT_PATHS:
            entries: list[FileInfo] = [
                {"path": f"/{task_id}/", "is_dir": True}
                for task_id, _name in reversed(task_pairs)
            ]
            return LsResult(entries=entries)
        match = _TASK_PATH.match(normalized)
        if match is None:
            return LsResult(error=f"Path not found: {path}")
        task_id = match.group("task_id")
        sub_path = match.group("file")
        if not any(task_id == tid for tid, _ in task_pairs):
            return LsResult(error=f"Subagent not found: {task_id}")
        if sub_path:
            return LsResult(error=f"Path is a file, not a directory: {path}")
        entries = [
            {"path": f"/{task_id}/{name}", "is_dir": False} for name in _Files.ALL
        ]
        return LsResult(entries=entries)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Return the projected file content for a task path (sync)."""

        return self._read(file_path)

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Return the projected file content for a task path (async)."""

        return self._read(file_path)

    def _read(self, file_path: str) -> ReadResult:
        match = _TASK_PATH.match(file_path)
        if match is None or match.group("file") is None:
            return ReadResult(error=f"File not found: {file_path}")
        task_id = match.group("task_id")
        file_name = match.group("file")
        if file_name not in _Files.ALL:
            return ReadResult(error=f"File not found: {file_path}")
        events = self._collect_events()
        task_pairs = SubagentTraceProjector.list_task_ids_with_names(events)
        if not any(task_id == tid for tid, _ in task_pairs):
            return ReadResult(error=f"Subagent not found: {task_id}")
        content = self._project_file(task_id, file_name, events)
        return ReadResult(
            file_data={
                "content": content,
                "encoding": "utf-8",
                "modified_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """Reject writes — subagent traces are read-only."""

        return WriteResult(error=_READ_ONLY_ERROR)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Reject writes — subagent traces are read-only."""

        return WriteResult(error=_READ_ONLY_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Reject edits — subagent traces are read-only."""

        return EditResult(error=_READ_ONLY_ERROR)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Reject edits — subagent traces are read-only."""

        return EditResult(error=_READ_ONLY_ERROR)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Unsupported on projected files."""

        return GrepResult(matches=[])

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Unsupported on projected files."""

        return GrepResult(matches=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Unsupported on projected files."""

        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Unsupported on projected files."""

        return GlobResult(matches=[])

    # --- helpers -----------------------------------------------------------

    @classmethod
    def _project_file(
        cls,
        task_id: str,
        file_name: str,
        events: tuple[RuntimeEventEnvelope, ...],
    ) -> str:
        """Dispatch to the shared projector for the requested virtual filename."""

        if file_name == _Files.CONVERSATION:
            return SubagentTraceProjector.project_conversation(task_id, events)
        if file_name == _Files.TOOL_CALLS:
            return SubagentTraceProjector.project_tool_calls(task_id, events)
        if file_name == _Files.SUMMARY:
            return SubagentTraceProjector.project_summary(task_id, events)
        if file_name == _Files.EVENTS:
            return SubagentTraceProjector.project_events_jsonl(task_id, events)
        return ""

    @staticmethod
    def _normalize_dir_path(path: str) -> str:
        """Canonicalize a directory path for root and task-level comparisons."""

        if not path:
            return "/subagents/"
        if path.endswith("/") and len(path) > 1:
            return path[:-1] + "/"
        return path

    def _collect_events(self) -> tuple[RuntimeEventEnvelope, ...]:
        """Read every canonical JSONL for this conversation, ordered by sequence.

        The union of the main ``events.jsonl`` and every per-subagent
        ``subagents/<key>.jsonl`` is exactly the run's event set — each event is
        routed to one file or the other by the store's ``_persist_event`` — so
        sourcing from all of them and letting the projector filter by task is
        correct regardless of which file a given lifecycle event landed in.
        """

        conversation_dir = self._layout.conversation_dir(
            self._org_id, self._conversation_id
        )
        envelopes: list[RuntimeEventEnvelope] = []
        self._extend_from_file(envelopes, conversation_dir / self._layout.EVENTS_FILE)
        subagents_dir = conversation_dir / self._layout.SUBAGENTS_DIR
        if subagents_dir.is_dir():
            for sub_file in sorted(subagents_dir.iterdir()):
                if sub_file.suffix == ".jsonl":
                    self._extend_from_file(envelopes, sub_file)
        envelopes.sort(key=lambda envelope: envelope.sequence_no)
        return SubagentTraceProjector.visible_events(envelopes)

    @staticmethod
    def _extend_from_file(sink: list[RuntimeEventEnvelope], path: Path) -> None:
        """Append parsed envelopes from one JSONL file, skipping malformed rows."""

        for doc in JsonlIo.iter_lines(path):
            try:
                sink.append(RuntimeEventEnvelope.model_validate(doc))
            except Exception:
                # A row that does not round-trip is skipped rather than failing
                # the whole read — the canonical stream is append-only and any
                # torn tail was already dropped by ``iter_lines``.
                continue


__all__ = ("FileSubagentTraceBackend",)
