"""Deep Agents BackendProtocol that routes the ``/drafts/`` prefix to versioned Postgres rows."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from threading import RLock
from typing import Any, cast

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.api.constants import Keys, Values
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.ports import DraftStorePort
from agent_runtime.persistence.records import (
    DraftPath,
    DraftRecord,
    DraftStatus,
)
from runtime_api.schemas import RunRecord, RuntimeApiEventType


# After CompositeBackend strips the ``/drafts/`` prefix the inner path looks
# like ``/<32 hex>.md`` (the leading slash is preserved so the standard
# BackendProtocol invariants hold). We accept both shapes so direct callers
# (unit tests, deep agent invocations that bypass the composite) still work.
_INNER_PATH_RE = re.compile(r"^/([0-9a-f]{32})\.md$")
_FULL_PATH_RE = re.compile(r"^/drafts/([0-9a-f]{32})\.md$")


class _Errors:
    """Error string literals returned to Deep Agents (the framework pattern-matches them)."""

    INVALID_PATH = "invalid_path"
    FILE_NOT_FOUND = "file_not_found"
    PERMISSION_DENIED = "permission_denied"
    AMBIGUOUS_MATCH = "Ambiguous match — old_string occurs multiple times; pass replace_all=True or use a more specific anchor."
    NO_MATCH = "old_string was not found in the draft body."
    EMPTY_DRAFT = "edit_file cannot be applied to an empty draft."


class _ToolRuntimeProxy:
    """Extension point for future per-call Deep Agents ToolRuntime attribution.

    Identity values are bound at ``DraftBackend`` construction, so no per-call
    injection is needed today.
    """


def _summary_for(record: DraftRecord) -> str:
    """Return a short human-readable label for a persisted draft version."""
    title = record.title.strip() or "Untitled draft"
    return f"Draft v{record.version}: {title}"


def _section_split(content: str) -> list[dict[str, str]]:
    """Split markdown into ``[{heading, body}]`` sections for the SSE event payload.

    Text before the first heading lands in a section with ``heading=""``; the
    frontend renders it without a header chip. This is pure presentation logic
    and is not used for persistence decisions.
    """

    sections: list[dict[str, str]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in content.splitlines():
        if line.startswith("#"):
            if current_heading or current_body:
                sections.append(
                    {
                        "heading": current_heading,
                        "body": "\n".join(current_body).strip(),
                    }
                )
            current_heading = line.lstrip("#").strip()
            current_body = []
        else:
            current_body.append(line)
    if current_heading or current_body:
        sections.append(
            {"heading": current_heading, "body": "\n".join(current_body).strip()}
        )
    return sections


def _title_for(content: str, fallback: str = "") -> str:
    """Extract a display title: the first H1 heading, or the first non-empty line, capped at 240 chars."""

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:240]
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return fallback or ""


class DraftBackend(BackendProtocol):
    """Deep Agents BackendProtocol that persists ``/drafts/<uuid>.md`` writes to Postgres.

    Scoped to one run at construction; path strings from the model are untrusted
    for identity, so org/conversation/run/user are bound immutably at construction.
    """

    PATH_PREFIX: str = DraftPath.PREFIX

    def __init__(
        self,
        *,
        store: DraftStorePort,
        org_id: str,
        conversation_id: str,
        run_id: str,
        user_id: str,
        emit_event: Callable[[DraftRecord], Awaitable[None]] | None = None,
    ) -> None:
        """Initialise the draft backend bound to a store, run context, and event emitter."""
        self._store = store
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._user_id = user_id
        self._emit = emit_event

        # Per-draft asyncio.Lock serializes "latest → +1 → insert" within this
        # process. Cross-process write races are caught by the UNIQUE (org_id,
        # draft_id, version) DB constraint and surface as OptimisticConflict.
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = RLock()

    # -- BackendProtocol surface ---------------------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        """Synchronous wrapper for :meth:`awrite`."""
        return _run_sync(self.awrite(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Create or overwrite a draft by inserting a new version row."""
        draft_id = self._extract_draft_id(file_path)
        if draft_id is None:
            return WriteResult(error=_Errors.INVALID_PATH)
        await self._append_version(
            draft_id=draft_id,
            content_text=content,
            status=DraftStatus.DRAFT,
        )
        return WriteResult(path=DraftPath.for_draft_id(draft_id))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Synchronous wrapper for :meth:`aedit`."""
        return _run_sync(self.aedit(file_path, old_string, new_string, replace_all))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Apply a string substitution to the latest draft version and persist the result."""
        draft_id = self._extract_draft_id(file_path)
        if draft_id is None:
            return EditResult(error=_Errors.INVALID_PATH)
        latest = await self._store.latest(org_id=self._org_id, draft_id=draft_id)
        if latest is None:
            return EditResult(error=_Errors.FILE_NOT_FOUND)
        if not latest.content_text:
            return EditResult(error=_Errors.EMPTY_DRAFT)
        occurrences = latest.content_text.count(old_string)
        if occurrences == 0:
            return EditResult(error=_Errors.NO_MATCH)
        # Reject ambiguous edits unless the caller explicitly opted into replace_all;
        # this matches the Deep Agents spec for strict-anchor edits.
        if occurrences > 1 and not replace_all:
            return EditResult(error=_Errors.AMBIGUOUS_MATCH)
        if replace_all:
            new_content = latest.content_text.replace(old_string, new_string)
        else:
            new_content = latest.content_text.replace(old_string, new_string, 1)
        await self._append_version(
            draft_id=draft_id,
            content_text=new_content,
            status=DraftStatus.DRAFT,
        )
        return EditResult(
            path=DraftPath.for_draft_id(draft_id),
            occurrences=occurrences if replace_all else 1,
        )

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Synchronous wrapper for :meth:`aread`."""
        return _run_sync(self.aread(file_path, offset, limit))

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Return the latest content of a draft, or a file-not-found error."""
        draft_id = self._extract_draft_id(file_path)
        if draft_id is None:
            return ReadResult(error=_Errors.INVALID_PATH)
        latest = await self._store.latest(org_id=self._org_id, draft_id=draft_id)
        if latest is None:
            return ReadResult(error=_Errors.FILE_NOT_FOUND)
        # ``offset`` / ``limit`` are part of the BackendProtocol signature but
        # drafts are small enough that we return the full content unconditionally.
        return ReadResult(
            file_data={
                "content": latest.content_text,
                "encoding": "utf-8",
                "modified_at": latest.created_at.isoformat(),
            }
        )

    def ls(self, path: str) -> LsResult:
        """Synchronous wrapper for :meth:`als`."""
        return _run_sync(self.als(path))

    async def als(self, path: str) -> LsResult:
        """List all latest draft versions for the current conversation."""
        # ``path`` is post-strip by CompositeBackend. The draft namespace is
        # flat — there are no subdirectories, so anything other than the root
        # sentinel values returns an empty listing rather than an error.
        if path not in ("/", "", "/."):
            return LsResult(entries=[])
        records = await self._store.latest_for_conversation(
            org_id=self._org_id, conversation_id=self._conversation_id
        )
        entries: list[FileInfo] = [
            cast(
                FileInfo,
                {
                    "path": f"/{record.draft_id}.md",
                    "is_dir": False,
                    "size": len(record.content_text.encode("utf-8")),
                    "modified_at": record.created_at.isoformat(),
                },
            )
            for record in records
        ]
        return LsResult(entries=entries)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Synchronous wrapper for :meth:`agrep`."""
        return _run_sync(self.agrep(pattern, path, glob))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """Search all latest drafts for lines containing ``pattern`` (substring, not regex)."""
        records = await self._store.latest_for_conversation(
            org_id=self._org_id, conversation_id=self._conversation_id
        )
        matches: list[GrepMatch] = []
        for record in records:
            for index, line in enumerate(record.content_text.splitlines(), start=1):
                if pattern in line:
                    matches.append(
                        cast(
                            GrepMatch,
                            {
                                "path": f"/{record.draft_id}.md",
                                "line": index,
                                "text": line,
                            },
                        )
                    )
        return GrepResult(matches=matches)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        """Synchronous wrapper for :meth:`aglob`."""
        return _run_sync(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        """Return all draft filenames; pattern is ignored because drafts are always ``*.md``."""
        # All draft filenames are ``<uuid>.md`` — full glob matching would never
        # reject any entry. Delegate to ``als`` for the canonical list.
        ls = await self.als("/")
        return GlobResult(matches=ls.entries or [])

    # -- internal helpers -----------------------------------------------------

    @staticmethod
    def _extract_draft_id(file_path: str) -> str | None:
        """Accept post-strip ``/<uuid>.md`` and full ``/drafts/<uuid>.md``."""

        for candidate in (_INNER_PATH_RE, _FULL_PATH_RE):
            match = candidate.match(file_path)
            if match is not None:
                return match.group(1)
        return None

    async def _append_version(
        self,
        *,
        draft_id: str,
        content_text: str,
        status: DraftStatus,
    ) -> DraftRecord:
        """Insert the next version row and emit DRAFT_UPDATED.

        Holds a per-draft asyncio lock so the "read latest → +1 → insert" trio
        is atomic within this process. The DB's UNIQUE (org_id, draft_id, version)
        constraint catches cross-process races.
        """
        async with self._lock_for(draft_id):
            latest = await self._store.latest(org_id=self._org_id, draft_id=draft_id)
            next_version = (latest.version + 1) if latest is not None else 1
            # Carry forward citation/connector/metadata from the previous version so
            # a plain write doesn't silently strip send-metadata set by an earlier turn.
            citation_ids = latest.citation_ids if latest is not None else ()
            target_connector = latest.target_connector if latest is not None else None
            target_metadata = dict(latest.target_metadata) if latest is not None else {}
            record = DraftRecord(
                draft_id=draft_id,
                version=next_version,
                org_id=self._org_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                user_id=self._user_id,
                title=_title_for(content_text),
                content_text=content_text,
                target_connector=target_connector,
                target_metadata=target_metadata,
                citation_ids=citation_ids,
                status=status,
                created_at=datetime.now(timezone.utc),
            )
            persisted = await self._store.insert_version(record)
        if self._emit is not None:
            await self._emit(persisted)
        return persisted

    def _lock_for(self, draft_id: str) -> asyncio.Lock:
        """Return or lazily create the per-draft asyncio.Lock for this backend instance."""
        with self._locks_guard:
            lock = self._locks.get(draft_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[draft_id] = lock
            return lock


# -- helpers ------------------------------------------------------------------


def _run_sync(awaitable: Awaitable[Any]) -> Any:
    """Bridge Deep Agents' sync entry points to async implementations.

    Production workers always call the async ``a*`` methods directly. This
    helper exists solely for the framework's sync dispatch path. When a
    running event loop is detected (e.g. an async test that exercises a sync
    entry point), the coroutine is scheduled on that loop via
    ``run_coroutine_threadsafe`` to avoid nesting ``run_until_complete`` calls.
    """

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(awaitable)
        finally:
            loop.close()
    if loop.is_running():
        return asyncio.run_coroutine_threadsafe(
            asyncio.ensure_future(awaitable), loop
        ).result()
    return loop.run_until_complete(awaitable)


# -- event emitter factory ----------------------------------------------------


def make_event_emitter(
    *,
    event_producer: object,
    run: RunRecord,
) -> Callable[[DraftRecord], Awaitable[None]]:
    """Build the ``emit_event`` callback to inject into ``DraftBackend``.

    Kept as a free function so ``DraftBackend`` stays testable without a live
    producer. The returned closure calls ``append_api_event`` so every draft
    update travels through the same sequencing and redaction path as other
    runtime events.
    """

    async def _emit(record: DraftRecord) -> None:
        """Emit a draft-change event with the record payload to the run's event stream."""
        payload = {
            Keys.Field.RUN_ID: run.run_id,
            Keys.Field.CONVERSATION_ID: run.conversation_id,
            "draft_id": record.draft_id,
            "version": record.version,
            "status": record.status.value,
            Keys.Field.TITLE: record.title,
            "sections": _section_split(record.content_text),
            "target_connector": record.target_connector,
            "target_metadata": record.target_metadata or None,
            "citation_ids": list(record.citation_ids),
            Keys.Field.SUMMARY: _summary_for(record),
            Keys.Field.STATUS: Values.Status.COMPLETED,
        }
        await event_producer.append_api_event(  # type: ignore[attr-defined]
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.DRAFT_UPDATED,
            payload=payload,
            summary=_summary_for(record),
            status=Values.Status.COMPLETED,
        )

    return _emit
