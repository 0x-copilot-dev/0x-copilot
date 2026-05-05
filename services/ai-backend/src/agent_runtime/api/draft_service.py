"""Application service for the Workspace-pane draft artifact (PR 1.3).

Reads / writes / sends / discards drafts on top of :class:`DraftStorePort`.

Live ``DRAFT_UPDATED`` events come from the agent path
(:class:`DraftBackend` → :class:`RuntimeEventProducer`) because the SSE stream
is run-scoped and only run-driven writes have a stable ``run_id``. User-
driven PATCH / send / discard return the persisted draft synchronously and
write to the audit chain; other connected clients pick up the change on the
next ``list`` request — same model the conversations endpoint uses.

The send flow does *not* dispatch the connector tool. It transitions the
draft to ``send_pending_approval`` and writes ``draft.send.proposed`` to the
audit chain. The actual approval card + connector-tool call lives in the
worker (PR 1.4 — explicitly out-of-scope for PR 1.3).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from starlette import status

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.persistence.ports import DraftStorePort, OptimisticConflict
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    Draft,
    DraftDiscardRequest,
    DraftListResponse,
    DraftPatchRequest,
    DraftSection,
    DraftSendRequest,
    DraftSendResponse,
)


_AUDIT_DRAFT_SEND_PROPOSED = "draft.send.proposed"
_AUDIT_DRAFT_DISCARDED = "draft.discard.discarded"
_AUDIT_DRAFT_EDIT_USER = "draft.edit.user"

_DRAFT_NOT_FOUND = "Draft was not found for this scope."
_DRAFT_VERSION_CONFLICT = "Draft version conflict; refresh and retry."
_DRAFT_STATUS_IMMUTABLE = "Draft is in a final state and cannot change."

# Sentinel for "argument not supplied" — must precede the class body
# because it's used as a default value in DraftService._next_version.
_UNSET = object()


class DraftService:
    """Coordinate draft list / patch / send / discard against the store + audit."""

    def __init__(
        self,
        *,
        store: DraftStorePort,
        persistence: object | None = None,
    ) -> None:
        self._store = store
        self._persistence = persistence

    # -- read paths ----------------------------------------------------------

    async def list_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> DraftListResponse:
        records = await _maybe_await(
            self._store.latest_for_conversation(
                org_id=org_id, conversation_id=conversation_id
            )
        )
        return DraftListResponse(drafts=tuple(_to_draft(record) for record in records))

    async def get(
        self,
        *,
        org_id: str,
        draft_id: str,
        version: int | None = None,
    ) -> Draft:
        record = await self._load(org_id=org_id, draft_id=draft_id, version=version)
        return _to_draft(record)

    # -- write paths ---------------------------------------------------------

    async def patch(
        self,
        *,
        org_id: str,
        user_id: str,
        draft_id: str,
        request: DraftPatchRequest,
    ) -> Draft:
        latest = await self._expect(
            org_id=org_id,
            draft_id=draft_id,
            expected_version=request.expected_version,
        )
        if latest.status in {DraftStatus.SENT, DraftStatus.DISCARDED}:
            raise self._immutable_status_error(latest.status)
        next_record = self._next_version(
            previous=latest,
            run_id=None,
            user_id=user_id,
            content_text=request.content_text,
            title_override=request.title,
            status=DraftStatus.DRAFT,
        )
        persisted = await _maybe_await(self._store.insert_version(next_record))
        await self._audit(
            org_id=org_id,
            user_id=user_id,
            event_type=_AUDIT_DRAFT_EDIT_USER,
            record=persisted,
        )
        return _to_draft(persisted)

    async def send(
        self,
        *,
        org_id: str,
        user_id: str,
        draft_id: str,
        request: DraftSendRequest,
    ) -> DraftSendResponse:
        latest = await self._expect(
            org_id=org_id,
            draft_id=draft_id,
            expected_version=request.expected_version,
        )
        if latest.status in {DraftStatus.SENT, DraftStatus.DISCARDED}:
            raise self._immutable_status_error(latest.status)
        approval_id = (
            f"draft_send:{latest.draft_id}:{latest.version + 1}:{uuid4().hex[:8]}"
        )
        next_record = self._next_version(
            previous=latest,
            run_id=None,
            user_id=user_id,
            content_text=latest.content_text,
            target_connector=request.target_connector,
            target_metadata=dict(request.target_metadata or {}),
            status=DraftStatus.SEND_PENDING_APPROVAL,
        )
        persisted = await _maybe_await(self._store.insert_version(next_record))
        await self._audit(
            org_id=org_id,
            user_id=user_id,
            event_type=_AUDIT_DRAFT_SEND_PROPOSED,
            record=persisted,
            extra_metadata={"approval_id": approval_id},
        )
        return DraftSendResponse(
            draft=_to_draft(persisted),
            approval_id=approval_id,
            run_id=None,
        )

    async def discard(
        self,
        *,
        org_id: str,
        user_id: str,
        draft_id: str,
        request: DraftDiscardRequest,
    ) -> Draft:
        latest = await self._expect(
            org_id=org_id,
            draft_id=draft_id,
            expected_version=request.expected_version,
        )
        if latest.status == DraftStatus.SENT:
            raise self._immutable_status_error(latest.status)
        next_record = self._next_version(
            previous=latest,
            run_id=None,
            user_id=user_id,
            content_text=latest.content_text,
            status=DraftStatus.DISCARDED,
        )
        persisted = await _maybe_await(self._store.insert_version(next_record))
        await self._audit(
            org_id=org_id,
            user_id=user_id,
            event_type=_AUDIT_DRAFT_DISCARDED,
            record=persisted,
        )
        return _to_draft(persisted)

    # -- internal helpers ----------------------------------------------------

    async def _load(
        self,
        *,
        org_id: str,
        draft_id: str,
        version: int | None,
    ) -> DraftRecord:
        if version is not None:
            record = await _maybe_await(
                self._store.get_version(
                    org_id=org_id, draft_id=draft_id, version=version
                )
            )
        else:
            record = await _maybe_await(
                self._store.latest(org_id=org_id, draft_id=draft_id)
            )
        if record is None:
            raise RuntimeApiError(
                code=RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                safe_message=_DRAFT_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return record

    async def _expect(
        self,
        *,
        org_id: str,
        draft_id: str,
        expected_version: int,
    ) -> DraftRecord:
        try:
            return await _maybe_await(
                self._store.expect_status(
                    org_id=org_id,
                    draft_id=draft_id,
                    expected_version=expected_version,
                )
            )
        except KeyError as exc:
            raise RuntimeApiError(
                code=RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                safe_message=_DRAFT_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
            ) from exc
        except OptimisticConflict as exc:
            raise RuntimeApiError(
                code=RuntimeErrorCode.VALIDATION_ERROR,
                safe_message=_DRAFT_VERSION_CONFLICT,
                http_status=status.HTTP_409_CONFLICT,
                details={
                    "expected_version": exc.expected_version,
                    "actual_version": exc.actual_version,
                },
            ) from exc

    @staticmethod
    def _immutable_status_error(current: DraftStatus) -> RuntimeApiError:
        return RuntimeApiError(
            code=RuntimeErrorCode.VALIDATION_ERROR,
            safe_message=_DRAFT_STATUS_IMMUTABLE,
            http_status=status.HTTP_409_CONFLICT,
            details={"status": current.value},
        )

    @staticmethod
    def _next_version(
        *,
        previous: DraftRecord,
        run_id: str | None,
        user_id: str,
        content_text: str,
        status: DraftStatus,
        title_override: str | None = None,
        target_connector: str | object = _UNSET,  # type: ignore[assignment]
        target_metadata: dict[str, object] | None = None,
    ) -> DraftRecord:
        # ``target_connector`` uses a sentinel so callers can explicitly clear
        # it (pass ``None``) versus inherit the previous value (omit).
        if target_connector is _UNSET:
            resolved_connector: str | None = previous.target_connector
        else:
            resolved_connector = target_connector  # type: ignore[assignment]
        resolved_metadata = (
            target_metadata
            if target_metadata is not None
            else dict(previous.target_metadata or {})
        )
        return DraftRecord(
            draft_id=previous.draft_id,
            version=previous.version + 1,
            org_id=previous.org_id,
            conversation_id=previous.conversation_id,
            run_id=run_id,
            user_id=user_id,
            title=title_override or _title_for(content_text, fallback=previous.title),
            content_text=content_text,
            target_connector=resolved_connector,
            target_metadata=resolved_metadata,
            citation_ids=previous.citation_ids,
            status=status,
            encryption_version=previous.encryption_version,
            created_at=datetime.now(timezone.utc),
        )

    async def _audit(
        self,
        *,
        org_id: str,
        user_id: str,
        event_type: str,
        record: DraftRecord,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        if self._persistence is None:
            return
        write_audit = getattr(self._persistence, "write_audit_log", None)
        if write_audit is None:
            return
        metadata = {
            "org_id": org_id,
            "user_id": user_id,
            "draft_id": record.draft_id,
            "version": record.version,
            "status": record.status.value,
            "target_connector": record.target_connector,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        await _maybe_await(write_audit(event_type=event_type, record=metadata))


# -- module-level helpers -----------------------------------------------------


async def _maybe_await(value: object) -> object:
    if asyncio.iscoroutine(value):
        return await value
    return value


def _title_for(content: str, *, fallback: str = "") -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:240]
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return fallback or ""


def _sections_for(content: str) -> list[DraftSection]:
    sections: list[DraftSection] = []
    current_heading = ""
    current_body: list[str] = []
    for line in content.splitlines():
        if line.startswith("#"):
            if current_heading or current_body:
                sections.append(
                    DraftSection(
                        heading=current_heading,
                        body="\n".join(current_body).strip(),
                    )
                )
            current_heading = line.lstrip("#").strip()
            current_body = []
        else:
            current_body.append(line)
    if current_heading or current_body:
        sections.append(
            DraftSection(
                heading=current_heading,
                body="\n".join(current_body).strip(),
            )
        )
    return sections


def _to_draft(record: DraftRecord) -> Draft:
    return Draft(
        draft_id=record.draft_id,
        version=record.version,
        conversation_id=record.conversation_id,
        run_id=record.run_id,
        user_id=record.user_id,
        title=record.title,
        content_text=record.content_text,
        sections=tuple(_sections_for(record.content_text)),
        target_connector=record.target_connector,
        target_metadata=record.target_metadata or None,
        citation_ids=record.citation_ids,
        status=record.status,
        created_at=record.created_at,
    )
