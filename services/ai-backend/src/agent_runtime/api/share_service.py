"""Conversation share lifecycle: create, list, update, revoke, and recipient snapshot view."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from fastapi import status

from agent_runtime.api.notifications import NotificationDispatcher
from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
)
from agent_runtime.api.share_token import ShareTokenIssuer, ShareTokenSecret
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.persistence.ports import CitationStorePort, ShareStorePort
from agent_runtime.persistence.records import (
    ShareRecipientRecord,
    ShareRecord,
    ShareViewAccess,
)
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ConversationResponse,
    ConversationShare,
    CreateShareRequest,
    CreateShareResponse,
    Draft,
    DraftListResponse,
    ListSharesResponse,
    RecipientPreview,
    RuntimeEventEnvelope,
    SharedByUser,
    SharedConversationSummary,
    SharedConversationView,
    ShareSnapshot,
    SourceEntry,
    SubagentEntry,
    UpdateShareRequest,
)

logger = logging.getLogger(__name__)


class _Errors:
    """User-facing error message constants for share operations."""

    NOT_OWNER_OR_ADMIN = "Only the conversation owner or a workspace admin can share."
    SHARE_NOT_FOUND = "Share was not found, has been revoked, or has expired."
    SHARE_NOT_FOR_RECIPIENT = "This share isn't available to your account."
    CONVERSATION_NOT_FOUND = "Conversation was not found for this scope."
    EXPIRES_AT_IN_PAST = "expires_at must be in the future."
    RECIPIENT_OUTSIDE_ORG = "Recipient is not an active member of this workspace."
    EXTERNAL_USER = "Workspace shares cannot include users from other workspaces."


class _AuditActions:
    """Audit log action strings for share create/update/revoke and view events."""

    SHARE_CREATED = "conversation.share.created"
    SHARE_UPDATED = "conversation.share.updated"
    SHARE_REVOKED = "conversation.share.revoked"
    SHARE_RECIPIENT_ADDED = "conversation.share.recipient_added"
    SHARE_RECIPIENT_REMOVED = "conversation.share.recipient_removed"
    SHARE_VIEWED = "conversation.share.viewed"
    SHARE_VIEW_DENIED = "conversation.share.view_denied"


class _ShareLimits:
    """Upper bounds on rows fetched when building the recipient snapshot view."""

    MESSAGES = 500
    EVENTS_PER_RUN = 1000
    SOURCES = 200
    SUBAGENTS = 50
    DRAFTS = 50
    # Rate-limit window for ``share.viewed`` audit emission. One row per
    # (share_id, viewer_user_id) inside this many seconds.
    VIEW_AUDIT_WINDOW_SECONDS = 60


class _ViewDenyReason:
    """Reason codes written to the ``view_denied`` audit row to explain why access was refused."""

    REVOKED = "revoked"
    EXPIRED = "expired"
    FOREIGN_ORG = "foreign_org"
    NOT_RECIPIENT = "not_recipient"
    SHARE_NOT_FOUND = "share_not_found"


class ShareService:
    """Manages conversation share lifecycle and the read-only recipient snapshot view.

    Also implements the ``ShareSnapshotPort`` token-resolution contract so the fork service
    and the recipient view share a single revocation/expiry/cross-org gate.
    """

    _ADMIN_SCOPE = "admin:users"
    """Scope present on workspace admins (matches PR 1.6 / 1.4 / 1.2.1
    convention — see ``enterprise_service_contracts.scopes``)."""

    def __init__(
        self,
        *,
        store: ShareStorePort,
        persistence: PersistencePort,
        event_store: EventStorePort,
        citations: CitationStorePort | None = None,
        workspace_feed_service: object | None = None,
        draft_service: object | None = None,
        notifications: NotificationDispatcher | None = None,
        app_base_url: str = "",
    ) -> None:
        self._store = store
        self._persistence = persistence
        self._event_store = event_store
        self._citations = citations
        self._workspace_feed = workspace_feed_service
        self._drafts = draft_service
        self._notifications = notifications
        # ``app_base_url`` is used to construct the share_url returned in
        # the create response. The frontend uses it verbatim.
        self._app_base_url = app_base_url.rstrip("/")
        # In-memory dedupe cache for ``share.viewed`` audit emission.
        # Keys are ``(share_id, viewer_user_id)``; values are the last
        # emit time. Bounded by share_id's natural cardinality —
        # one entry per active viewer; we don't bother with eviction
        # since the view rate per user is naturally rare.
        self._recent_view_audit: dict[tuple[str, str], datetime] = {}

    # ------------------------------------------------------------------
    # Creator surface
    # ------------------------------------------------------------------

    async def create_share(
        self,
        *,
        org_id: str,
        user_id: str,
        permission_scopes: tuple[str, ...],
        conversation_id: str,
        request: CreateShareRequest,
    ) -> CreateShareResponse:
        """Create a share record, optionally minting a bearer token, and return the response."""
        await self._assert_owner_or_admin(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            permission_scopes=permission_scopes,
        )
        now = datetime.now(timezone.utc)
        if request.expires_at is not None and request.expires_at <= now:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                _Errors.EXPIRES_AT_IN_PAST,
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        token: ShareTokenSecret | None = None
        token_hash: str | None = None
        token_prefix: str | None = None
        if request.include_link:
            token, token_hash, token_prefix = ShareTokenIssuer.mint()

        share_id = f"share_{uuid.uuid4().hex}"
        snapshot_at = now
        recipients = tuple(
            ShareRecipientRecord(share_id=share_id, user_id=user_id_value)
            for user_id_value in request.recipient_user_ids
        )
        record = ShareRecord(
            share_id=share_id,
            org_id=org_id,
            conversation_id=conversation_id,
            created_by_user_id=user_id,
            view_access=request.view_access,
            sources_visible_to_viewer=request.sources_visible_to_viewer,
            share_token_hash=token_hash,
            share_token_prefix=token_prefix,
            snapshot_at=snapshot_at,
            expires_at=request.expires_at,
            created_at=now,
        )
        await self._store.insert_share(share=record, recipients=recipients)
        await self._audit(
            event_type=_AuditActions.SHARE_CREATED,
            org_id=org_id,
            actor_user_id=user_id,
            metadata={
                "share_id": share_id,
                "conversation_id": conversation_id,
                "view_access": request.view_access.value,
                "recipient_count": len(recipients),
                "sources_visible_to_viewer": request.sources_visible_to_viewer,
                "has_token": token_hash is not None,
                "token_prefix": token_prefix,
                "snapshot_at": snapshot_at.isoformat(),
                "expires_at": (
                    request.expires_at.isoformat()
                    if request.expires_at is not None
                    else None
                ),
            },
        )
        share_url = ""
        if token is not None:
            share_url = self._build_share_url(token.expose())
        return CreateShareResponse(
            share_id=share_id,
            share_token=token.expose() if token is not None else "",
            share_token_prefix=token_prefix,
            share_url=share_url,
            view_access=request.view_access,
            recipient_user_ids=tuple(request.recipient_user_ids),
            sources_visible_to_viewer=request.sources_visible_to_viewer,
            snapshot_at=snapshot_at,
            expires_at=request.expires_at,
            revoked_at=None,
            created_by_user_id=user_id,
            created_at=now,
            view_count=0,
        )

    async def list_shares(
        self,
        *,
        org_id: str,
        user_id: str,
        permission_scopes: tuple[str, ...],
        conversation_id: str,
        include_revoked: bool = False,
    ) -> ListSharesResponse:
        """List all shares for a conversation, enforcing owner-or-admin access."""
        await self._assert_owner_or_admin(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            permission_scopes=permission_scopes,
        )
        records = await self._store.list_for_conversation(
            org_id=org_id,
            conversation_id=conversation_id,
            include_revoked=include_revoked,
        )
        shares: list[ConversationShare] = []
        for record in records:
            recipients = await self._store.list_recipients(
                org_id=org_id, share_id=record.share_id
            )
            shares.append(self._to_response(record, recipients))
        return ListSharesResponse(shares=tuple(shares))

    async def update_share(
        self,
        *,
        org_id: str,
        user_id: str,
        permission_scopes: tuple[str, ...],
        share_id: str,
        request: UpdateShareRequest,
    ) -> ConversationShare:
        """Update share settings and/or recipient list; audits before/after diff."""
        existing = await self._store.get_by_id(org_id=org_id, share_id=share_id)
        if existing is None:
            raise self._share_not_found()
        await self._assert_share_owner_or_admin(
            existing=existing,
            user_id=user_id,
            permission_scopes=permission_scopes,
        )
        now = datetime.now(timezone.utc)
        if request.expires_at is not None and request.expires_at <= now:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                _Errors.EXPIRES_AT_IN_PAST,
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        before_payload = self._diff_snapshot(existing)
        updated = (
            await self._store.update_share(
                org_id=org_id,
                share_id=share_id,
                sources_visible_to_viewer=request.sources_visible_to_viewer,
                expires_at=request.expires_at,
                clear_expires_at=request.clear_expires_at,
            )
            or existing
        )
        added_user_ids: Sequence[str] = ()
        removed_user_ids: Sequence[str] = ()
        if request.recipient_user_ids is not None:
            recipients = tuple(
                ShareRecipientRecord(share_id=share_id, user_id=user_id_value)
                for user_id_value in request.recipient_user_ids
            )
            added_user_ids, removed_user_ids = await self._store.replace_recipients(
                org_id=org_id, share_id=share_id, recipients=recipients
            )
        after_payload = self._diff_snapshot(updated)
        diff_keys: list[str] = []
        if request.sources_visible_to_viewer is not None:
            diff_keys.append("sources_visible_to_viewer")
        if request.clear_expires_at or request.expires_at is not None:
            diff_keys.append("expires_at")
        if request.recipient_user_ids is not None:
            diff_keys.append("recipient_user_ids")
        await self._audit(
            event_type=_AuditActions.SHARE_UPDATED,
            org_id=org_id,
            actor_user_id=user_id,
            metadata={
                "share_id": share_id,
                "diff_keys": diff_keys,
                "before": before_payload,
                "after": after_payload,
                "added_user_ids": list(added_user_ids),
                "removed_user_ids": list(removed_user_ids),
            },
        )
        for added in added_user_ids:
            await self._audit(
                event_type=_AuditActions.SHARE_RECIPIENT_ADDED,
                org_id=org_id,
                actor_user_id=user_id,
                metadata={"share_id": share_id, "user_id": added},
            )
        for removed in removed_user_ids:
            await self._audit(
                event_type=_AuditActions.SHARE_RECIPIENT_REMOVED,
                org_id=org_id,
                actor_user_id=user_id,
                metadata={"share_id": share_id, "user_id": removed},
            )
        recipients_now = await self._store.list_recipients(
            org_id=org_id, share_id=share_id
        )
        return self._to_response(updated, recipients_now)

    async def revoke_share(
        self,
        *,
        org_id: str,
        user_id: str,
        permission_scopes: tuple[str, ...],
        share_id: str,
    ) -> None:
        """Permanently revoke a share; idempotent if already revoked."""
        existing = await self._store.get_by_id(org_id=org_id, share_id=share_id)
        if existing is None:
            raise self._share_not_found()
        await self._assert_share_owner_or_admin(
            existing=existing,
            user_id=user_id,
            permission_scopes=permission_scopes,
        )
        if existing.revoked_at is not None:
            return
        now = datetime.now(timezone.utc)
        revoked = await self._store.revoke_share(
            org_id=org_id, share_id=share_id, now=now
        )
        await self._audit(
            event_type=_AuditActions.SHARE_REVOKED,
            org_id=org_id,
            actor_user_id=user_id,
            metadata={
                "share_id": share_id,
                "conversation_id": existing.conversation_id,
                "view_access": existing.view_access.value,
                "revoked_at": (
                    revoked.revoked_at.isoformat() if revoked else now.isoformat()
                ),
            },
        )

    # ------------------------------------------------------------------
    # Recipient surface
    # ------------------------------------------------------------------

    async def preview_share(
        self,
        *,
        share_token: str,
        viewer_org_id: str,
        viewer_user_id: str,
    ) -> RecipientPreview:
        """Return whether the viewer can access the share, without loading conversation data."""
        share = await self._resolve_token(share_token)
        if share is None:
            raise self._share_not_found()
        gate = await self._evaluate_gate(
            share=share,
            viewer_org_id=viewer_org_id,
            viewer_user_id=viewer_user_id,
        )
        return RecipientPreview(
            share=self._to_summary(share),
            can_view=gate.can_view,
            reason=gate.reason,
        )

    async def get_recipient_view(
        self,
        *,
        share_token: str,
        viewer_org_id: str,
        viewer_user_id: str,
    ) -> SharedConversationView:
        """Build the full snapshot view for an authorized recipient, applying source/draft redaction when configured."""
        share = await self._resolve_token(share_token)
        if share is None:
            raise self._share_not_found()
        gate = await self._evaluate_gate(
            share=share,
            viewer_org_id=viewer_org_id,
            viewer_user_id=viewer_user_id,
        )
        if not gate.can_view:
            await self._audit_view_denied(
                share=share, viewer_user_id=viewer_user_id, reason=gate.reason
            )
            raise self._gate_to_error(gate)
        await self._audit_view_if_due(share=share, viewer_user_id=viewer_user_id)

        # Source conversation. Org-scoped admin path so the recipient
        # view works even when the recipient isn't the source's owner.
        source = await self._persistence.get_conversation_for_org(
            org_id=share.org_id, conversation_id=share.conversation_id
        )
        if source is None or source.deleted_at is not None:
            raise self._share_not_found()

        all_messages = await self._persistence.list_messages(
            org_id=share.org_id,
            conversation_id=share.conversation_id,
            limit=_ShareLimits.MESSAGES,
            include_deleted=False,
        )
        snapshot_messages = tuple(
            message.to_response()
            for message in all_messages
            if message.created_at <= share.snapshot_at
        )

        events_by_run = await self._collect_events_for_messages(
            org_id=share.org_id,
            messages=all_messages,
            snapshot_at=share.snapshot_at,
        )
        sources = await self._collect_sources(
            share=share, sources_visible=share.sources_visible_to_viewer
        )
        drafts = await self._collect_drafts(share=share)
        subagents = await self._collect_subagents(share=share)

        # ``sources_visible_to_viewer=False`` redacts every source field
        # that could leak third-party content. Drafts (first-party
        # content) follow the same flag.
        if not share.sources_visible_to_viewer:
            sources = tuple(self._redact_source(entry) for entry in sources)
            drafts = tuple(self._redact_draft(draft) for draft in drafts)
            events_by_run = self._redact_events(events_by_run)

        return SharedConversationView(
            share=self._to_summary(share),
            conversation=self._project_conversation(source),
            messages=snapshot_messages,
            events_by_run_id=events_by_run,
            sources=sources,
            drafts=drafts,
            subagents=subagents,
        )

    # ------------------------------------------------------------------
    # ShareSnapshotPort — token resolution for PR 6.2 fork service
    # ------------------------------------------------------------------

    async def resolve_by_token(self, share_token: str) -> ShareSnapshot | None:
        """Return the active snapshot for a token, or ``None`` if missing, revoked, or expired."""

        share = await self._resolve_token(share_token)
        if share is None:
            return None
        recipients = await self._store.list_recipients(
            org_id=share.org_id, share_id=share.share_id
        )
        return ShareSnapshot(
            share_id=share.share_id,
            org_id=share.org_id,
            conversation_id=share.conversation_id,
            snapshot_at=share.snapshot_at,
            view_access=share.view_access.value,
            recipient_user_ids=tuple(r.user_id for r in recipients),
            sources_visible_to_viewer=share.sources_visible_to_viewer,
            created_by_user_id=share.created_by_user_id,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _assert_owner_or_admin(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        permission_scopes: tuple[str, ...],
    ) -> None:
        if self._ADMIN_SCOPE in permission_scopes:
            # Admin path: prove the conversation exists in this org but
            # don't require ownership.
            conversation = await self._persistence.get_conversation_for_org(
                org_id=org_id, conversation_id=conversation_id
            )
            if conversation is None:
                raise self._conversation_not_found()
            return
        conversation = await self._persistence.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            # Could be foreign-org, foreign-user, or genuinely missing.
            # 404 in all cases — same opacity as elsewhere.
            raise self._conversation_not_found()

    async def _assert_share_owner_or_admin(
        self,
        *,
        existing: ShareRecord,
        user_id: str,
        permission_scopes: tuple[str, ...],
    ) -> None:
        if self._ADMIN_SCOPE in permission_scopes:
            return
        if existing.created_by_user_id == user_id:
            return
        raise RuntimeApiError(
            RuntimeErrorCode.PERMISSION_DENIED,
            _Errors.NOT_OWNER_OR_ADMIN,
            http_status=status.HTTP_403_FORBIDDEN,
        )

    async def _resolve_token(self, share_token: str) -> ShareRecord | None:
        if not share_token:
            return None
        digest = ShareTokenIssuer.hash(share_token)
        share = await self._store.find_by_token_hash(share_token_hash=digest)
        if share is None:
            return None
        now = datetime.now(timezone.utc)
        if share.revoked_at is not None or share.is_expired(now=now):
            return None
        return share

    async def _evaluate_gate(
        self,
        *,
        share: ShareRecord,
        viewer_org_id: str,
        viewer_user_id: str,
    ) -> "_GateOutcome":
        if share.org_id != viewer_org_id:
            return _GateOutcome(can_view=False, reason=_ViewDenyReason.FOREIGN_ORG)
        if share.view_access is ShareViewAccess.WORKSPACE:
            return _GateOutcome(can_view=True, reason="ok")
        recipients = await self._store.list_recipients(
            org_id=share.org_id, share_id=share.share_id
        )
        if any(recipient.user_id == viewer_user_id for recipient in recipients):
            return _GateOutcome(can_view=True, reason="ok")
        return _GateOutcome(can_view=False, reason=_ViewDenyReason.NOT_RECIPIENT)

    def _gate_to_error(self, gate: "_GateOutcome") -> RuntimeApiError:
        if gate.reason == _ViewDenyReason.FOREIGN_ORG:
            return self._share_not_found()
        if gate.reason == _ViewDenyReason.NOT_RECIPIENT:
            return RuntimeApiError(
                RuntimeErrorCode.PERMISSION_DENIED,
                _Errors.SHARE_NOT_FOR_RECIPIENT,
                http_status=status.HTTP_403_FORBIDDEN,
            )
        return self._share_not_found()

    async def _collect_events_for_messages(
        self,
        *,
        org_id: str,
        messages: Sequence[object],
        snapshot_at: datetime,
    ) -> dict[str, tuple[RuntimeEventEnvelope, ...]]:
        run_ids = []
        seen: set[str] = set()
        for message in messages:
            run_id = getattr(message, "run_id", None)
            if not run_id or run_id in seen:
                continue
            seen.add(run_id)
            run_ids.append(run_id)
        events_by_run: dict[str, tuple[RuntimeEventEnvelope, ...]] = {}
        for run_id in run_ids:
            events = await self._event_store.list_events_after(
                org_id=org_id, run_id=run_id, after_sequence=0
            )
            clamped = tuple(
                event for event in events if event.created_at <= snapshot_at
            )
            if clamped:
                events_by_run[run_id] = clamped[: _ShareLimits.EVENTS_PER_RUN]
        return events_by_run

    async def _collect_sources(
        self, *, share: ShareRecord, sources_visible: bool
    ) -> tuple[SourceEntry, ...]:
        if self._workspace_feed is None:
            return ()
        try:
            response = await self._workspace_feed.list_sources(
                org_id=share.org_id,
                conversation_id=share.conversation_id,
                run_id=None,
                limit=_ShareLimits.SOURCES,
            )
            return tuple(response.sources)
        except Exception:  # pragma: no cover — defensive
            logger.warning(
                "share.recipient_view.sources_unavailable",
                extra={"metadata": {"share_id": share.share_id}},
                exc_info=True,
            )
            return ()

    async def _collect_drafts(self, *, share: ShareRecord) -> tuple[Draft, ...]:
        if self._drafts is None:
            return ()
        try:
            response: DraftListResponse = await self._drafts.list_for_conversation(
                org_id=share.org_id, conversation_id=share.conversation_id
            )
            return tuple(
                draft
                for draft in response.drafts
                if draft.created_at <= share.snapshot_at
            )[: _ShareLimits.DRAFTS]
        except Exception:  # pragma: no cover — defensive
            logger.warning(
                "share.recipient_view.drafts_unavailable",
                extra={"metadata": {"share_id": share.share_id}},
                exc_info=True,
            )
            return ()

    async def _collect_subagents(
        self, *, share: ShareRecord
    ) -> tuple[SubagentEntry, ...]:
        if self._workspace_feed is None:
            return ()
        try:
            from runtime_api.schemas import SubagentStatusFilter

            response = await self._workspace_feed.list_subagents(
                org_id=share.org_id,
                conversation_id=share.conversation_id,
                status_filter=SubagentStatusFilter.ALL,
                limit=_ShareLimits.SUBAGENTS,
            )
            return tuple(response.subagents)
        except Exception:  # pragma: no cover — defensive
            logger.warning(
                "share.recipient_view.subagents_unavailable",
                extra={"metadata": {"share_id": share.share_id}},
                exc_info=True,
            )
            return ()

    @staticmethod
    def _redact_source(entry: SourceEntry) -> SourceEntry:
        return entry.model_copy(
            update={
                "title": None,
                "snippet": None,
                "source_url": None,
            }
        )

    @staticmethod
    def _redact_draft(draft: Draft) -> Draft:
        return draft.model_copy(
            update={
                "title": "",
                "content_text": "",
                "sections": (),
                "target_metadata": None,
            }
        )

    @staticmethod
    def _redact_events(
        events_by_run: dict[str, tuple[RuntimeEventEnvelope, ...]],
    ) -> dict[str, tuple[RuntimeEventEnvelope, ...]]:
        # Redact source-bearing fields in event payloads. We strip
        # any well-known leak vectors; the FE's projection layer is
        # the source of truth for *display* fields, so leaving the
        # ``presentation.summary`` alone (it's always operator-vetted)
        # keeps the recipient timeline readable.
        sensitive_keys = {
            "snippet",
            "url",
            "title",
            "excerpt",
            "source_url",
            "source_doc_id",
        }
        redacted: dict[str, tuple[RuntimeEventEnvelope, ...]] = {}
        for run_id, events in events_by_run.items():
            redacted[run_id] = tuple(
                event.model_copy(
                    update={
                        "payload": _strip_keys(event.payload, sensitive_keys),
                    }
                )
                if isinstance(event.payload, dict)
                else event
                for event in events
            )
        return redacted

    @staticmethod
    def _project_conversation(source: object) -> ConversationResponse:
        # Source is a ConversationRecord. Use its public projection
        # so the recipient sees the same shape every other surface uses.
        return source.to_response()  # type: ignore[attr-defined]

    @staticmethod
    def _diff_snapshot(record: ShareRecord) -> dict[str, object]:
        return {
            "sources_visible_to_viewer": record.sources_visible_to_viewer,
            "expires_at": (
                record.expires_at.isoformat() if record.expires_at is not None else None
            ),
        }

    def _to_response(
        self,
        record: ShareRecord,
        recipients: Sequence[ShareRecipientRecord],
    ) -> ConversationShare:
        return ConversationShare(
            share_id=record.share_id,
            share_token_prefix=record.share_token_prefix,
            view_access=record.view_access,
            recipient_user_ids=tuple(recipient.user_id for recipient in recipients),
            sources_visible_to_viewer=record.sources_visible_to_viewer,
            snapshot_at=record.snapshot_at,
            expires_at=record.expires_at,
            revoked_at=record.revoked_at,
            created_by_user_id=record.created_by_user_id,
            created_at=record.created_at,
        )

    @staticmethod
    def _to_summary(record: ShareRecord) -> SharedConversationSummary:
        return SharedConversationSummary(
            share_id=record.share_id,
            view_access=record.view_access,
            sources_visible_to_viewer=record.sources_visible_to_viewer,
            snapshot_at=record.snapshot_at,
            shared_by=SharedByUser(user_id=record.created_by_user_id),
        )

    def _build_share_url(self, plaintext_token: str) -> str:
        if not self._app_base_url:
            return f"/share/{plaintext_token}"
        return f"{self._app_base_url}/share/{plaintext_token}"

    async def _audit(
        self,
        *,
        event_type: str,
        org_id: str,
        actor_user_id: str,
        metadata: dict[str, object],
    ) -> None:
        await self._persistence.write_audit_log(
            event_type=event_type,
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "actor_type": "user",
                "resource_type": "conversation_share",
                "resource_id": str(metadata.get("share_id", "")),
                "outcome": "success",
                "metadata": metadata,
            },
        )

    async def _audit_view_if_due(
        self, *, share: ShareRecord, viewer_user_id: str
    ) -> None:
        key = (share.share_id, viewer_user_id)
        now = datetime.now(timezone.utc)
        last = self._recent_view_audit.get(key)
        if (
            last is not None
            and (now - last).total_seconds() < _ShareLimits.VIEW_AUDIT_WINDOW_SECONDS
        ):
            return
        self._recent_view_audit[key] = now
        await self._audit(
            event_type=_AuditActions.SHARE_VIEWED,
            org_id=share.org_id,
            actor_user_id=viewer_user_id,
            metadata={
                "share_id": share.share_id,
                "conversation_id": share.conversation_id,
                "view_access": share.view_access.value,
                "sources_visible_to_viewer": share.sources_visible_to_viewer,
            },
        )

    async def _audit_view_denied(
        self, *, share: ShareRecord, viewer_user_id: str, reason: str
    ) -> None:
        await self._audit(
            event_type=_AuditActions.SHARE_VIEW_DENIED,
            org_id=share.org_id,
            actor_user_id=viewer_user_id,
            metadata={
                "share_token_prefix": share.share_token_prefix,
                "reason": reason,
            },
        )

    @staticmethod
    def _share_not_found() -> RuntimeApiError:
        return RuntimeApiError(
            RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            _Errors.SHARE_NOT_FOUND,
            http_status=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _conversation_not_found() -> RuntimeApiError:
        return RuntimeApiError(
            RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            _Errors.CONVERSATION_NOT_FOUND,
            http_status=status.HTTP_404_NOT_FOUND,
        )


class _GateOutcome:
    """Result of the recipient gate evaluation."""

    __slots__ = ("can_view", "reason")

    def __init__(self, *, can_view: bool, reason: str) -> None:
        self.can_view = can_view
        self.reason = reason


def _strip_keys(value: object, sensitive_keys: set[str]) -> object:
    if isinstance(value, dict):
        return {
            key: None if key in sensitive_keys else _strip_keys(child, sensitive_keys)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_strip_keys(item, sensitive_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_keys(item, sensitive_keys) for item in value)
    return value


__all__ = ["ShareService"]
