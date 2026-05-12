"""DraftBackend — routes ``/drafts/`` writes through deepagents into Postgres.

Drafts are first-class artifacts in the Atlas Workspace pane. Rather than
inventing a new tool, we let the agent use deepagents' built-in ``write_file``
and ``edit_file`` tools and route the ``/drafts/`` path prefix to this backend
through the existing :class:`CompositeBackend` setup.

Each successful ``awrite`` / ``aedit`` call:

1. Validates the path (``/drafts/<32 hex>.md`` after CompositeBackend strips
   the ``/drafts/`` prefix → leaves ``/<32 hex>.md``).
2. Reads the latest persisted version (if any) for the same ``draft_id``.
3. For ``aedit``, performs the substitution server-side (we never trust the
   model's idea of current content) and validates the match shape.
4. Inserts a new row through :class:`DraftStorePort` with ``version+1``.
5. Emits a ``DRAFT_UPDATED`` runtime event so the SSE pipeline pushes the new
   version to the FE Workspace pane DraftTab without an extra fetch.

``aread`` returns the latest version's ``content_text``. ``als`` lists drafts
in the bound conversation. The backend is **scoped to one run**: ``org_id``,
``conversation_id``, ``run_id``, and ``user_id`` are bound at construction
time, so the agent cannot escape its own tenant.
"""

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
    """Standardized backend error strings (deepagents recognises these literals)."""

    INVALID_PATH = "invalid_path"
    FILE_NOT_FOUND = "file_not_found"
    PERMISSION_DENIED = "permission_denied"
    AMBIGUOUS_MATCH = "Ambiguous match — old_string occurs multiple times; pass replace_all=True or use a more specific anchor."
    NO_MATCH = "old_string was not found in the draft body."
    EMPTY_DRAFT = "edit_file cannot be applied to an empty draft."


class _ToolRuntimeProxy:
    """Best-effort bridge to deepagents' ToolRuntime.

    BackendProtocol methods don't take a runtime arg; deepagents' filesystem
    middleware injects context into the backend instance via the ToolRuntime
    on each call. We don't depend on that here — every per-call value
    (``run_id``, ``user_id`` overrides, etc.) is bound at backend
    construction. The proxy exists so that future plumbing (per-tool-call
    overrides, attribution to a specific subagent) can be added without
    re-shaping callers.
    """


def _summary_for(record: DraftRecord) -> str:
    title = record.title.strip() or "Untitled draft"
    return f"Draft v{record.version}: {title}"


def _section_split(content: str) -> list[dict[str, str]]:
    """Split markdown into ``[{heading, body}]`` for the UI projection.

    Lines starting with ``#`` (with up to four ``#`` and one space) become
    section headings. Free-form text before the first heading is collapsed
    into a heading ``""`` section (the FE renders it without a header chip).
    Pure presentation; nothing here is load-bearing for persistence.
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
    """Derive a draft title from the first H1 (or first non-empty line)."""

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
    """Deepagents backend that persists ``/drafts/<uuid>.md`` writes.

    One instance per run. Construction binds the run's tenant identity; no
    method takes ``org_id`` because we never trust the model's path strings to
    carry it.
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
        self._store = store
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._user_id = user_id
        self._emit = emit_event

        # Per-draft lock keeps ``latest → +1 → insert`` atomic against
        # concurrent edits inside the same agent loop (a supervisor + a
        # subagent both editing the same draft). Cross-process races are
        # caught by the UNIQUE (org_id, draft_id, version) constraint and
        # surfaced through OptimisticConflict.
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = RLock()

    # -- BackendProtocol surface ---------------------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        return _run_sync(self.awrite(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
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
        return _run_sync(self.aedit(file_path, old_string, new_string, replace_all))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
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
        return _run_sync(self.aread(file_path, offset, limit))

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        draft_id = self._extract_draft_id(file_path)
        if draft_id is None:
            return ReadResult(error=_Errors.INVALID_PATH)
        latest = await self._store.latest(org_id=self._org_id, draft_id=draft_id)
        if latest is None:
            return ReadResult(error=_Errors.FILE_NOT_FOUND)
        return ReadResult(
            file_data={
                "content": latest.content_text,
                "encoding": "utf-8",
                "modified_at": latest.created_at.isoformat(),
            }
        )

    def ls(self, path: str) -> LsResult:
        return _run_sync(self.als(path))

    async def als(self, path: str) -> LsResult:
        # ``path`` is post-strip — at the prefix root we receive ``"/"``.
        if path not in ("/", "", "/."):
            # Drafts are flat. Anything below ``/drafts/`` is a single file
            # path; ``/drafts/foo/`` is not a thing.
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
        return _run_sync(self.agrep(pattern, path, glob))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
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
        return _run_sync(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        # Pattern matching is intentionally limited to ``*.md`` — the only
        # filenames our prefix can hold. We fall back to ``als``.
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
        async with self._lock_for(draft_id):
            latest = await self._store.latest(org_id=self._org_id, draft_id=draft_id)
            next_version = (latest.version + 1) if latest is not None else 1
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
        with self._locks_guard:
            lock = self._locks.get(draft_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[draft_id] = lock
            return lock


# -- helpers ------------------------------------------------------------------


def _run_sync(awaitable: Awaitable[Any]) -> Any:
    """Bridge async backend methods into deepagents' sync entry points."""

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(awaitable)
        finally:
            loop.close()
    if loop.is_running():
        # We're inside an async context (e.g. tests calling sync entry from
        # an async test). Schedule and synchronously wait — never reached in
        # production where the worker drives ``a*`` methods directly.
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
    """Build the ``emit_event`` callback for :class:`DraftBackend`.

    Kept as a free function rather than a backend method so the backend stays
    pure and unit-testable without a real producer.

    The producer is the same :class:`RuntimeEventProducer` used everywhere
    else in the worker; we call its ``append_api_event`` so the event flows
    through redaction, presentation projection, and the run-sequence cursor
    update — same path as every other API-authored event.
    """

    async def _emit(record: DraftRecord) -> None:
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
