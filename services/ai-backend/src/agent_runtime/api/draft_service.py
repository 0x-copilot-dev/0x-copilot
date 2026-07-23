"""Application service for draft artifact lifecycle: list, patch, send, and discard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from starlette import status

from agent_runtime.api.constants import Keys, Values as ApiValues
from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthCheck,
    CapabilityAuthGate,
    CapabilityAuthOutcome,
)
from agent_runtime.execution.contracts import RuntimeErrorCode, StreamEventSource
from agent_runtime.persistence.ports import DraftStorePort, OptimisticConflict
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ApprovalRequestRecord,
    Draft,
    DraftDiscardRequest,
    DraftListResponse,
    DraftPatchRequest,
    DraftSection,
    DraftSendRequest,
    DraftSendResponse,
    RuntimeApiEventType,
)


_AUDIT_DRAFT_SEND_PROPOSED = "draft.send.proposed"
_AUDIT_DRAFT_DISCARDED = "draft.discard.discarded"
_AUDIT_DRAFT_EDIT_USER = "draft.edit.user"

_DRAFT_NOT_FOUND = "Draft was not found for this scope."
_DRAFT_VERSION_CONFLICT = "Draft version conflict; refresh and retry."
_DRAFT_STATUS_IMMUTABLE = "Draft is in a final state and cannot change."
_NO_HOST_RUN = (
    "Cannot send a draft from a chat with no run history; start a chat first."
)
_INVALID_TARGET_CONNECTOR = "Unknown connector for this workspace."
_CONNECTOR_AUTH_REQUIRED = "Connector requires authentication for this user."
_CONNECTOR_WORKSPACE_DISABLED = "Connector is disabled for this workspace."

# Sentinel for "argument not supplied" — must precede the class body
# because it's used as a default value in DraftService._next_version.
_UNSET = object()

# Stable string used as ``approval.metadata['kind']`` so the worker-side
# resolution branch can disambiguate draft-send approvals from generic
# action approvals without sniffing other fields.
_APPROVAL_KIND_DRAFT_SEND = "draft_send"

_BODY_PREVIEW_MAX_CHARS = 400


class DraftService:
    """Orchestrates draft reads and writes against the store, approval system, and audit log."""

    def __init__(
        self,
        *,
        store: DraftStorePort,
        persistence: object | None = None,
        auth_gate: CapabilityAuthGate | None = None,
        event_producer: object | None = None,
        write_stager: object | None = None,
    ) -> None:
        self._store = store
        self._persistence = persistence
        self._auth_gate = auth_gate
        self._event_producer = event_producer
        # PRD-D1: optional, duck-typed like ``event_producer``. When wired AND
        # ``SURFACES_V2`` is on, ``send()`` stages the write through the ledger
        # instead of the v1 approval row. ``None`` ⇒ the v1 path regardless of
        # the flag (mirrors the ``event_producer is None`` degrade-open pattern).
        self._write_stager = write_stager

    # -- read paths ----------------------------------------------------------

    async def list_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> DraftListResponse:
        """Return all current draft versions for a conversation."""
        records = await self._store.latest_for_conversation(
            org_id=org_id, conversation_id=conversation_id
        )
        return DraftListResponse(drafts=tuple(_to_draft(record) for record in records))

    async def get(
        self,
        *,
        org_id: str,
        draft_id: str,
        version: int | None = None,
    ) -> Draft:
        """Return a specific draft version, or the latest version when ``version`` is omitted."""
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
        """Apply a user edit to an existing draft; raises 409 on version conflict or terminal state."""
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
        persisted = await self._store.insert_version(next_record)
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
        """Initiate a draft send: auth-gate check → insert v+1 → approval row → event."""
        latest = await self._expect(
            org_id=org_id,
            draft_id=draft_id,
            expected_version=request.expected_version,
        )
        if latest.status in {DraftStatus.SENT, DraftStatus.DISCARDED}:
            raise self._immutable_status_error(latest.status)

        # 1. Auth pre-check — fail fast BEFORE any DB write.
        await self._enforce_auth_gate(
            target_connector=request.target_connector,
            org_id=org_id,
            user_id=user_id,
            conversation_id=latest.conversation_id,
        )

        # 2. Resolve a host run for the approval card.
        host_run_id = await self._resolve_host_run_id(
            org_id=org_id, conversation_id=latest.conversation_id, draft=latest
        )

        # 3. Insert the next draft version (status=send_pending_approval).
        next_record = self._next_version(
            previous=latest,
            run_id=host_run_id,
            user_id=user_id,
            content_text=latest.content_text,
            target_connector=request.target_connector,
            target_metadata=dict(request.target_metadata or {}),
            status=DraftStatus.SEND_PENDING_APPROVAL,
        )
        persisted = await self._store.insert_version(next_record)

        # 3b. PRD-D1 branch — when v2 staging is wired AND the flag is on, the
        # write stages on the ledger (write.staged + revision.added rev 1); no
        # v1 approval row, no APPROVAL_REQUESTED event. Nothing executes here.
        # The v1 path (steps 4-6) is byte-identical when the flag is off or the
        # stager is unwired.
        if self._write_stager is not None and SurfacesV2Flag.enabled():
            return await self._stage_send_v2(
                org_id=org_id,
                user_id=user_id,
                host_run_id=host_run_id,
                draft=persisted,
                request=request,
            )

        # 4. Persist the approval row keyed to the host run.
        approval = await self._create_approval(
            org_id=org_id,
            user_id=user_id,
            host_run_id=host_run_id,
            draft=persisted,
            request=request,
        )

        # 5. Emit APPROVAL_REQUESTED on host run's stream so the FE renders
        #    the inline ApprovalTool card without an extra fetch.
        await self._emit_approval_requested(
            org_id=org_id,
            host_run_id=host_run_id,
            approval=approval,
            draft=persisted,
        )

        # 6. Audit chain.
        await self._audit(
            org_id=org_id,
            user_id=user_id,
            event_type=_AUDIT_DRAFT_SEND_PROPOSED,
            record=persisted,
            extra_metadata={
                "approval_id": approval.approval_id,
                "host_run_id": host_run_id,
                "target_connector": request.target_connector,
            },
        )

        return DraftSendResponse(
            draft=_to_draft(persisted),
            approval_id=approval.approval_id,
            run_id=host_run_id,
        )

    async def discard(
        self,
        *,
        org_id: str,
        user_id: str,
        draft_id: str,
        request: DraftDiscardRequest,
    ) -> Draft:
        """Mark a draft as discarded; raises 409 if already sent (sent is irreversible)."""
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
        persisted = await self._store.insert_version(next_record)
        await self._audit(
            org_id=org_id,
            user_id=user_id,
            event_type=_AUDIT_DRAFT_DISCARDED,
            record=persisted,
        )
        return _to_draft(persisted)

    # -- PRD-D1 staged-write branch ------------------------------------------

    # Wire op for a draft-send target when the caller supplies none. The op is
    # presentation-only here (D1 never executes); D2's CommitEngine resolves the
    # real connector operation.
    _DEFAULT_SEND_OP = "send"

    async def _stage_send_v2(
        self,
        *,
        org_id: str,
        user_id: str,
        host_run_id: str,
        draft: DraftRecord,
        request: DraftSendRequest,
    ) -> DraftSendResponse:
        """Stage a v2 write instead of the v1 approval row (flag on + stager wired).

        Resolves the host run, delegates to ``WriteStager.stage`` (emits
        write.staged + revision.added rev 1), keeps the existing
        ``draft.send.proposed`` audit, and returns the ``stage_id`` so the FE can
        bind the staged-draft surface. NOTHING executes: no approval row, no
        APPROVAL_REQUESTED event, no connector call.
        """

        run = await self._get_run(org_id=org_id, run_id=host_run_id)
        if run is None:
            raise RuntimeApiError(
                code=RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                safe_message=_NO_HOST_RUN,
                http_status=status.HTTP_409_CONFLICT,
                details={"error_code": "no_host_run"},
            )
        target_op = self._target_op_for(request)
        state = await self._write_stager.stage(  # type: ignore[union-attr]
            run=run,
            org_id=org_id,
            run_id=host_run_id,
            draft=draft,
            target_connector=request.target_connector,
            target_op=target_op,
        )
        await self._audit(
            org_id=org_id,
            user_id=user_id,
            event_type=_AUDIT_DRAFT_SEND_PROPOSED,
            record=draft,
            extra_metadata={
                "stage_id": state.stage_id,
                "surface_id": state.surface_id,
                "host_run_id": host_run_id,
                "target_connector": request.target_connector,
                "surfaces_v2": True,
            },
        )
        return DraftSendResponse(
            draft=_to_draft(draft),
            approval_id=None,
            run_id=host_run_id,
            stage_id=state.stage_id,
        )

    @classmethod
    def _target_op_for(cls, request: DraftSendRequest) -> str:
        """Resolve the target op from the request metadata, else the send default."""

        raw = (request.target_metadata or {}).get("op")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return cls._DEFAULT_SEND_OP

    async def _get_run(self, *, org_id: str, run_id: str) -> object | None:
        """Fetch the run record via persistence (None when unavailable)."""

        if self._persistence is None:
            return None
        get_run = getattr(self._persistence, "get_run", None)
        if get_run is None:
            return None
        return await get_run(org_id=org_id, run_id=run_id)

    # -- internal helpers ----------------------------------------------------

    async def _enforce_auth_gate(
        self,
        *,
        target_connector: str,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> None:
        """Verify connector auth state before any write; raises on non-authenticated outcomes."""
        if self._auth_gate is None:
            # Legacy / unconfigured deployments — degrade open; configure the gate at app boot.
            return
        runtime_context = _RuntimeContextStub(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        check: CapabilityAuthCheck = await self._auth_gate.check(
            target_connector=target_connector,
            runtime_context=runtime_context,
        )
        if check.outcome is CapabilityAuthOutcome.AUTHENTICATED:
            return
        details: dict[str, Any] = {"target_connector": target_connector}
        if check.mcp_server_id is not None:
            details["mcp_server_id"] = check.mcp_server_id
        if check.outcome is CapabilityAuthOutcome.NOT_AUTHENTICATED:
            details["error_code"] = "connector_auth_required"
            raise RuntimeApiError(
                code=RuntimeErrorCode.PERMISSION_DENIED,
                safe_message=check.safe_message or _CONNECTOR_AUTH_REQUIRED,
                http_status=status.HTTP_409_CONFLICT,
                details=details,
            )
        if check.outcome is CapabilityAuthOutcome.WORKSPACE_DISABLED:
            details["error_code"] = "connector_workspace_disabled"
            raise RuntimeApiError(
                code=RuntimeErrorCode.PERMISSION_DENIED,
                safe_message=check.safe_message or _CONNECTOR_WORKSPACE_DISABLED,
                http_status=status.HTTP_403_FORBIDDEN,
                details=details,
            )
        details["error_code"] = "invalid_target_connector"
        raise RuntimeApiError(
            code=RuntimeErrorCode.VALIDATION_ERROR,
            safe_message=check.safe_message or _INVALID_TARGET_CONNECTOR,
            http_status=status.HTTP_400_BAD_REQUEST,
            details=details,
        )

    async def _resolve_host_run_id(
        self,
        *,
        org_id: str,
        conversation_id: str,
        draft: DraftRecord,
    ) -> str:
        """Find the run to anchor the approval card on; raises 409 if no run exists."""
        # 1. Prefer the run that produced this draft (when present).
        if draft.run_id:
            return draft.run_id
        # 2. Fall back to the latest run on the conversation.
        list_messages = getattr(self._persistence, "list_messages", None)
        if list_messages is not None:
            try:
                messages = await list_messages(
                    org_id=org_id,
                    conversation_id=conversation_id,
                    limit=50,
                )
            except Exception:
                messages = ()
            for msg in reversed(tuple(messages)):
                run_id = getattr(msg, "run_id", None)
                if run_id:
                    return run_id
        # 3. No host run available — surface a clean 409.
        raise RuntimeApiError(
            code=RuntimeErrorCode.VALIDATION_ERROR,
            safe_message=_NO_HOST_RUN,
            http_status=status.HTTP_409_CONFLICT,
            details={"error_code": "no_host_run"},
        )

    async def _create_approval(
        self,
        *,
        org_id: str,
        user_id: str,
        host_run_id: str,
        draft: DraftRecord,
        request: DraftSendRequest,
    ) -> ApprovalRequestRecord:
        """Persist an approval request row and return it (no-op when persistence is absent)."""
        record = ApprovalRequestRecord(
            run_id=host_run_id,
            conversation_id=draft.conversation_id,
            org_id=org_id,
            user_id=user_id,
            metadata={
                "kind": _APPROVAL_KIND_DRAFT_SEND,
                Keys.Field.APPROVAL_KIND: ApiValues.ApprovalKind.ACTION,
                "draft_id": draft.draft_id,
                "draft_version": draft.version,
                "target_connector": request.target_connector,
                "target_metadata": dict(request.target_metadata or {}),
                "summary": _approval_summary(draft, request),
                "body_preview": draft.content_text[:_BODY_PREVIEW_MAX_CHARS],
            },
        )
        if self._persistence is None:
            return record
        create = getattr(self._persistence, "create_approval_request", None)
        if create is None:
            return record
        return await create(record=record)

    async def _emit_approval_requested(
        self,
        *,
        org_id: str,
        host_run_id: str,
        approval: ApprovalRequestRecord,
        draft: DraftRecord,
    ) -> None:
        """Append an APPROVAL_REQUESTED event to the host run's stream (best-effort)."""
        if self._event_producer is None or self._persistence is None:
            return
        get_run = getattr(self._persistence, "get_run", None)
        if get_run is None:
            return
        run = await get_run(org_id=org_id, run_id=host_run_id)
        if run is None:
            return
        append = getattr(self._event_producer, "append_api_event", None)
        if append is None:
            return
        payload: dict[str, object] = {
            Keys.Field.APPROVAL_ID: approval.approval_id,
            Keys.Field.APPROVAL_KIND: ApiValues.ApprovalKind.ACTION,
            "kind": _APPROVAL_KIND_DRAFT_SEND,
            "draft_id": draft.draft_id,
            "draft_version": draft.version,
            "target_connector": draft.target_connector,
            "target_metadata": draft.target_metadata or None,
            Keys.Field.SUMMARY: approval.metadata.get("summary"),
            "body_preview": approval.metadata.get("body_preview"),
            Keys.Field.STATUS: ApiValues.Status.WAITING,
        }
        await append(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=payload,
            summary=str(payload.get(Keys.Field.SUMMARY) or "Send draft"),
            status=ApiValues.Status.WAITING,
        )

    async def _load(
        self,
        *,
        org_id: str,
        draft_id: str,
        version: int | None,
    ) -> DraftRecord:
        """Fetch a draft by version or latest; raises 404 when absent."""
        if version is not None:
            record = await self._store.get_version(
                org_id=org_id, draft_id=draft_id, version=version
            )
        else:
            record = await self._store.latest(org_id=org_id, draft_id=draft_id)
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
        """Fetch the draft at the expected version; raises 404/409 on miss or conflict."""
        try:
            return await self._store.expect_status(
                org_id=org_id,
                draft_id=draft_id,
                expected_version=expected_version,
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
        """Build a 409 error indicating the draft is in a final, non-writable state."""
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
        """Build the next immutable version record, inheriting unchanged fields from previous."""
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
        """Write an audit log row for a draft mutation; silent no-op when persistence is absent."""
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
        await write_audit(event_type=event_type, record=metadata)


# -- module-level helpers -----------------------------------------------------


class _RuntimeContextStub:
    """Minimal identity carrier satisfying the auth gate's duck-type contract without full context construction."""

    __slots__ = ("org_id", "user_id", "conversation_id", "permission_scopes")

    def __init__(self, *, org_id: str, user_id: str, conversation_id: str) -> None:
        self.org_id = org_id
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.permission_scopes = frozenset()


def _title_for(content: str, *, fallback: str = "") -> str:
    """Extract a display title from markdown content: first ``# `` heading, then first non-blank line."""
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
    """Parse markdown into heading/body pairs; headingless content becomes a section with an empty heading."""
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


def _approval_summary(draft: DraftRecord, request: DraftSendRequest) -> str:
    """Build the human-readable approval card summary line."""
    title = (draft.title or "").strip() or "Untitled draft"
    target = (request.target_connector or "").strip() or "connector"
    return f"Send {title} to {target}"


def _to_draft(record: DraftRecord) -> Draft:
    """Project a persisted ``DraftRecord`` into the public ``Draft`` wire shape."""
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
