"""Project `runtime_events` into virtual `/subagents/<task_id>/` files.

Exposes subagent execution traces (conversation, tool calls, summary, raw
events) to the supervisor's filesystem so the next turn can answer questions
like "what search queries did subagent 2 run?" verbatim, and so partial work
from a cancelled/timed-out subagent is not lost.

Read-only by design: writes to `/subagents/...` always fail. The projection is
computed on demand from the event store; nothing is persisted by this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

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

from agent_runtime.api.constants import Keys
from agent_runtime.api.async_ports import AsyncEventStorePort, AsyncPersistencePort
from runtime_api.schemas import (
    MessageRecord,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


def _extract_text(value: object) -> str | None:
    """Normalize a payload field to a non-empty string or None.

    Mirrors `runtime_worker.stream_messages.StreamTextHelper.extract` but lives
    here to avoid the `runtime_worker → agent_runtime → runtime_worker` import
    cycle that arises when the run handler imports `SubagentArtifactsBackend`.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


class StreamTextHelper:  # noqa: D101 - shim; named for parity with the public helper
    extract = staticmethod(_extract_text)


class _Files:
    CONVERSATION = "conversation.md"
    TOOL_CALLS = "tool_calls.json"
    SUMMARY = "summary.md"
    EVENTS = "events.jsonl"

    ALL: tuple[str, ...] = (CONVERSATION, TOOL_CALLS, SUMMARY, EVENTS)


_PATH_PREFIX = "/subagents/"
# Paths can arrive with the `/subagents/` prefix included (when accessed
# directly) or stripped (when this backend is wrapped by deepagents'
# `CompositeBackend`, which strips the matched route prefix before
# delegating). Match both shapes.
_TASK_PATH = re.compile(r"^(?:/subagents)?/(?P<task_id>[^/]+)(?:/(?P<file>.+?))?/?$")
_ROOT_PATHS = frozenset({"/", "/subagents", "/subagents/"})
_TOOL_OUTPUT_PREVIEW_LIMIT = 1_500
_READ_ONLY_ERROR = "The /subagents/ filesystem is read-only."


class SubagentTraceProjector:
    """Pure projection of `RuntimeEventEnvelope`s into per-subagent file content."""

    @classmethod
    def visible_events(
        cls,
        events: Iterable[RuntimeEventEnvelope],
    ) -> tuple[RuntimeEventEnvelope, ...]:
        """Drop events that should never reach the model (internal, redacted)."""

        return tuple(
            event
            for event in events
            if event.visibility is RuntimeEventVisibility.USER
            and event.redaction_state is not RuntimeEventRedactionState.OFFLOADED
        )

    @classmethod
    def list_task_ids_with_names(
        cls,
        events: Sequence[RuntimeEventEnvelope],
    ) -> tuple[tuple[str, str], ...]:
        """Return `(task_id, subagent_name)` for each `SUBAGENT_STARTED` event.

        Order: oldest first. Duplicates dropped (a `SUBAGENT_STARTED` only fires
        once per task_id).
        """

        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for event in events:
            if event.event_type is not RuntimeApiEventType.SUBAGENT_STARTED:
                continue
            task_id = StreamTextHelper.extract(event.payload.get(Keys.Field.TASK_ID))
            if task_id is None or task_id in seen:
                continue
            seen.add(task_id)
            name = (
                StreamTextHelper.extract(event.payload.get(Keys.Field.SUBAGENT_NAME))
                or "subagent"
            )
            result.append((task_id, name))
        return tuple(result)

    @classmethod
    def events_for_task(
        cls,
        task_id: str,
        events: Sequence[RuntimeEventEnvelope],
    ) -> tuple[RuntimeEventEnvelope, ...]:
        """Events that belong to one subagent's execution scope."""

        return tuple(
            event
            for event in events
            if (
                event.parent_task_id == task_id
                or (
                    event.event_type
                    in {
                        RuntimeApiEventType.SUBAGENT_STARTED,
                        RuntimeApiEventType.SUBAGENT_COMPLETED,
                    }
                    and StreamTextHelper.extract(event.payload.get(Keys.Field.TASK_ID))
                    == task_id
                )
            )
        )

    @classmethod
    def project_summary(
        cls,
        task_id: str,
        events: Sequence[RuntimeEventEnvelope],
    ) -> str:
        scoped = cls.events_for_task(task_id, events)
        started = next(
            (
                event
                for event in scoped
                if event.event_type is RuntimeApiEventType.SUBAGENT_STARTED
            ),
            None,
        )
        completed = next(
            (
                event
                for event in scoped
                if event.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED
            ),
            None,
        )
        objective = (
            StreamTextHelper.extract(started.payload.get(Keys.Field.SUMMARY))
            if started is not None
            else None
        ) or "(no objective recorded)"
        if completed is not None:
            status = (
                StreamTextHelper.extract(completed.payload.get(Keys.Field.STATUS))
                or "completed"
            )
            result = (
                StreamTextHelper.extract(completed.payload.get(Keys.Field.SUMMARY))
                or "(no result summary recorded)"
            )
        else:
            status = "running"
            result = (
                "(subagent did not reach a terminal state — partial work below "
                "is whatever fired before the run ended)"
            )
        run_id = (
            scoped[0].run_id
            if scoped
            else (started.run_id if started is not None else "unknown")
        )
        subagent_name = (
            started
            and StreamTextHelper.extract(started.payload.get(Keys.Field.SUBAGENT_NAME))
        ) or "subagent"
        return (
            f"# Subagent {task_id}\n\n"
            f"## Subagent\n{subagent_name}\n\n"
            f"## Status\n{status}\n\n"
            f"## Objective\n{objective}\n\n"
            f"## Result\n{result}\n\n"
            f"## Run\n{run_id}\n"
        )

    @classmethod
    def project_tool_calls(
        cls,
        task_id: str,
        events: Sequence[RuntimeEventEnvelope],
    ) -> str:
        """Structured per-call record. `args` preserved verbatim for the
        'what queries did subagent X run?' use case."""

        scoped = cls.events_for_task(task_id, events)
        calls: dict[str, dict[str, object]] = {}
        for event in scoped:
            payload = event.payload or {}
            call_id = StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
            if call_id is None:
                continue
            entry = calls.setdefault(
                call_id,
                {
                    "call_id": call_id,
                    "tool_name": None,
                    "args": None,
                    "output": None,
                    "started_at": None,
                    "completed_at": None,
                    "status": None,
                },
            )
            tool_name = StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
            if tool_name and entry["tool_name"] in (None, "unknown_tool"):
                entry["tool_name"] = tool_name
            args = payload.get(Keys.Field.ARGS)
            if isinstance(args, dict) and args and entry["args"] in (None, {}):
                entry["args"] = args
            if event.event_type is RuntimeApiEventType.TOOL_CALL_STARTED:
                entry["started_at"] = event.created_at.isoformat()
                entry["status"] = entry.get("status") or "started"
            elif event.event_type is RuntimeApiEventType.TOOL_RESULT:
                entry["completed_at"] = event.created_at.isoformat()
                entry["status"] = (
                    StreamTextHelper.extract(payload.get(Keys.Field.STATUS))
                    or "completed"
                )
                output = payload.get(Keys.Field.OUTPUT)
                entry["output"] = cls._truncated_output(output)
            elif event.event_type is RuntimeApiEventType.TOOL_CALL_COMPLETED:
                entry["completed_at"] = (
                    entry["completed_at"] or event.created_at.isoformat()
                )
                entry["status"] = entry.get("status") or "completed"
        ordered = sorted(
            calls.values(),
            key=lambda entry: entry.get("started_at") or "",
        )
        return json.dumps(ordered, indent=2, ensure_ascii=False, default=str)

    @classmethod
    def project_conversation(
        cls,
        task_id: str,
        events: Sequence[RuntimeEventEnvelope],
    ) -> str:
        """Chronological prose interleaving model text and tool calls."""

        scoped = cls.events_for_task(task_id, events)
        lines: list[str] = []
        last_kind: str | None = None
        for event in scoped:
            payload = event.payload or {}
            if event.event_type is RuntimeApiEventType.MODEL_DELTA:
                text = StreamTextHelper.extract(payload.get(Keys.Payload.DELTA)) or ""
                if not text:
                    continue
                if last_kind != "delta":
                    lines.append("")
                lines.append(text)
                last_kind = "delta"
                continue
            if event.event_type is RuntimeApiEventType.FINAL_RESPONSE:
                text = StreamTextHelper.extract(payload.get(Keys.Payload.MESSAGE)) or ""
                if text:
                    if last_kind == "delta":
                        lines.append("")
                    lines.append("## Final response\n")
                    lines.append(text)
                    last_kind = "final"
                continue
            if event.event_type is RuntimeApiEventType.TOOL_CALL_STARTED:
                tool_name = (
                    StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
                    or "unknown_tool"
                )
                args = payload.get(Keys.Field.ARGS) or {}
                args_repr = json.dumps(args, ensure_ascii=False, default=str)
                if last_kind == "delta":
                    lines.append("")
                lines.append(f"> tool_call: {tool_name} args={args_repr}")
                last_kind = "tool_call"
                continue
            if event.event_type is RuntimeApiEventType.TOOL_RESULT:
                output = cls._truncated_output(payload.get(Keys.Field.OUTPUT))
                output_repr = json.dumps(output, ensure_ascii=False, default=str)
                lines.append(f"< tool_result: {output_repr}")
                last_kind = "tool_result"
        body = "\n".join(line for line in lines if line is not None).strip()
        if not body:
            body = (
                "(no model text or tool calls were emitted before the subagent ended)"
            )
        return body + "\n"

    @classmethod
    def project_events_jsonl(
        cls,
        task_id: str,
        events: Sequence[RuntimeEventEnvelope],
    ) -> str:
        """Raw event envelopes. Already visibility/redaction-filtered."""

        scoped = cls.events_for_task(task_id, events)
        lines = [
            event.model_dump_json(exclude_none=True, by_alias=False) for event in scoped
        ]
        return "\n".join(lines) + ("\n" if lines else "")

    @classmethod
    def _truncated_output(cls, output: object) -> object:
        if output is None:
            return None
        if isinstance(output, (dict, list)):
            text = json.dumps(output, ensure_ascii=False, default=str)
            if len(text) <= _TOOL_OUTPUT_PREVIEW_LIMIT:
                return output
            return f"{text[:_TOOL_OUTPUT_PREVIEW_LIMIT].rstrip()}…[truncated]"
        if isinstance(output, str):
            if len(output) <= _TOOL_OUTPUT_PREVIEW_LIMIT:
                return output
            return f"{output[:_TOOL_OUTPUT_PREVIEW_LIMIT].rstrip()}…[truncated]"
        return str(output)


class SubagentArtifactsBackend(BackendProtocol):
    """Read-only Deep Agents backend exposing `/subagents/<task_id>/` files."""

    PATH_PREFIX: str = _PATH_PREFIX

    def __init__(
        self,
        *,
        event_store: AsyncEventStorePort,
        persistence: AsyncPersistencePort,
        org_id: str,
        conversation_id: str,
        current_run_id: str,
    ) -> None:
        self._event_store = event_store
        self._persistence = persistence
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._current_run_id = current_run_id

    # --- BackendProtocol surface ---------------------------------------------

    def ls(self, path: str) -> LsResult:
        return _run_sync(self.als(path))

    async def als(self, path: str) -> LsResult:
        # Path inputs:
        # - Direct callers (and unit tests) pass `/subagents/<task_id>/...`.
        # - Through deepagents' `CompositeBackend`, the matched route prefix
        #   `/subagents/` is stripped before delegation, so we receive `/`,
        #   `/<task_id>/`, etc.
        # We return entries with paths relative to our route (so CompositeBackend
        # can prepend `/subagents/` correctly via `_remap_file_info_path`),
        # and rely on `_TASK_PATH` to accept either shape on input.
        normalized = self._normalize_dir_path(path)
        events = await self._collect_events()
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
        return _run_sync(self.aread(file_path, offset, limit))

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        match = _TASK_PATH.match(file_path)
        if match is None or match.group("file") is None:
            return ReadResult(error=f"File not found: {file_path}")
        task_id = match.group("task_id")
        file_name = match.group("file")
        if file_name not in _Files.ALL:
            return ReadResult(error=f"File not found: {file_path}")
        events = await self._collect_events()
        task_pairs = SubagentTraceProjector.list_task_ids_with_names(events)
        if not any(task_id == tid for tid, _ in task_pairs):
            return ReadResult(error=f"Subagent not found: {task_id}")
        content = self._project_file(task_id, file_name, events)
        modified_at = datetime.now(timezone.utc).isoformat()
        return ReadResult(
            file_data={
                "content": content,
                "encoding": "utf-8",
                "modified_at": modified_at,
            }
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_ERROR)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_ERROR)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return GrepResult(matches=[])

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return GrepResult(matches=[])

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    # --- helpers -------------------------------------------------------------

    @classmethod
    def _project_file(
        cls,
        task_id: str,
        file_name: str,
        events: Sequence[RuntimeEventEnvelope],
    ) -> str:
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
        if not path:
            return "/subagents/"
        if path.endswith("/") and len(path) > 1:
            return path[:-1] + "/"
        return path

    async def _collect_events(self) -> tuple[RuntimeEventEnvelope, ...]:
        """Walk prior runs in the conversation chain plus the current run."""

        run_ids = await self._conversation_run_ids()
        collected: list[RuntimeEventEnvelope] = []
        for run_id in run_ids:
            events = await self._event_store.list_events_after(
                org_id=self._org_id,
                run_id=run_id,
                after_sequence=0,
            )
            for event in events:
                if event.conversation_id != self._conversation_id:
                    continue
                collected.append(event)
        return SubagentTraceProjector.visible_events(collected)

    async def _conversation_run_ids(self) -> tuple[str, ...]:
        """Distinct prior + current run_ids reachable through the parent chain."""

        records = await self._persistence.list_messages(
            org_id=self._org_id,
            conversation_id=self._conversation_id,
            limit=200,
        )
        run_ids: list[str] = []
        seen: set[str] = set()
        for record in records:
            run_id = _record_run_id(record)
            if run_id is None or run_id in seen:
                continue
            seen.add(run_id)
            run_ids.append(run_id)
        if self._current_run_id not in seen:
            run_ids.append(self._current_run_id)
        return tuple(run_ids)


def _record_run_id(record: MessageRecord) -> str | None:
    return record.run_id


def _run_sync(coro):
    """Block on an async coroutine for `BackendProtocol`'s sync API surface.

    The sync entry points exist for callers that haven't migrated to the async
    methods yet. We dispatch back to the async implementation rather than
    duplicating logic.
    """

    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return asyncio.run_coroutine_threadsafe(coro, loop).result()
