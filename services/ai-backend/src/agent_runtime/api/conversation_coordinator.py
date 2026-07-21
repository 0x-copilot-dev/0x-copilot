"""Conversation lifecycle coordinator — write side of the CQRS-lite split.

Owns all conversation mutations: create, update, connector-scope patch, soft
delete, restore, and bulk user-history deletion. Read paths live on
:class:`ConversationQueryService` to keep query and command concerns separate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from starlette import status

from agent_runtime.api.constants import Messages
from agent_runtime.api.ports import PersistencePort
from agent_runtime.api.project_resolver import (
    NullProjectResolver,
    ProjectResolverPort,
)
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.settings import RuntimeSettings
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    CancelRunRequest,
    ConversationConnectorScopesResponse,
    ConversationRecord,
    ConversationResponse,
    ConversationStatus,
    CreateConversationRequest,
    HistoryDeletionResponse,
    UpdateConversationConnectorsRequest,
    UpdateConversationRequest,
)


def _conversation_lifecycle_audit_metadata(
    *,
    before: ConversationRecord,
    after: ConversationRecord,
    fields_set: frozenset[str] | set[str],
) -> dict[str, object]:
    """Build a structured before/after/diff blob for a conversation PATCH audit row.

    Only fields present in ``fields_set`` (i.e., explicitly sent by the caller)
    are compared, so a partial update doesn't bloat the audit log with unchanged
    fields reporting no diff.
    """

    diff_keys: list[str] = []
    before_blob: dict[str, object] = {}
    after_blob: dict[str, object] = {}
    if "title" in fields_set:
        before_blob["title"] = before.title
        after_blob["title"] = after.title
        if before.title != after.title:
            diff_keys.append("title")
    if "folder" in fields_set:
        before_blob["folder"] = before.folder
        after_blob["folder"] = after.folder
        if before.folder != after.folder:
            diff_keys.append("folder")
    if "archived" in fields_set:
        before_blob["archived"] = before.status == ConversationStatus.ARCHIVED
        after_blob["archived"] = after.status == ConversationStatus.ARCHIVED
        if before_blob["archived"] != after_blob["archived"]:
            diff_keys.append("archived")
    return {
        "before": before_blob,
        "after": after_blob,
        "diff_keys": diff_keys,
    }


def _connector_scope_audit_metadata(
    *,
    before: dict[str, tuple[str, ...] | None],
    patch: dict[str, tuple[str, ...] | None],
    after: dict[str, tuple[str, ...] | None],
) -> dict[str, object]:
    """Build the audit metadata blob for a per-chat connector scope change.

    Only connectors mentioned in the patch appear in before/after so audit
    consumers can reconstruct exactly what the caller changed without
    diffing the entire scope map.
    """

    def _to_json(
        value: dict[str, tuple[str, ...] | None],
    ) -> dict[str, list[str] | None]:
        return {
            connector_id: (list(scopes) if scopes is not None else None)
            for connector_id, scopes in value.items()
        }

    diff_keys = sorted(patch.keys())
    return {
        "before": _to_json({k: before.get(k) for k in diff_keys}),
        "after": _to_json({k: after.get(k) for k in diff_keys}),
        "diff_keys": diff_keys,
    }


class ConversationCoordinator:
    """Service layer for conversation lifecycle mutations with integrated audit logging.

    Depends on ``RunCoordinator`` (as an ``object`` to avoid a circular import)
    for cancelling active runs when a conversation is deleted.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        settings: RuntimeSettings,
        run_coordinator: object,  # RunCoordinator — typed as object to break import cycle
        project_resolver: ProjectResolverPort | None = None,
    ) -> None:
        self._persistence = persistence
        self._settings = settings
        self._run_coordinator = run_coordinator
        # P6.5-A2 — owner of the project ``default_connector_allowlist``
        # inheritance hook at conversation create. Defaults to a no-op
        # resolver so tests / dev environments that don't wire the
        # trusted backend lane still construct the coordinator cleanly.
        self._project_resolver: ProjectResolverPort = (
            project_resolver or NullProjectResolver()
        )

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationResponse:
        """Create a conversation and seed connector defaults per the inheritance ladder.

        Inheritance ladder (PRD §5.4 + workspace defaults):

        1. **Caller-explicit wins.** If the request carries a non-``None``
           ``enabled_connectors`` map (including an explicit ``{}``), the
           coordinator writes that map verbatim and skips both project
           and workspace seeding — the user's composer choice is
           authoritative.
        2. **Project allowlist.** If ``project_id`` is set and the
           project's ``default_connector_allowlist`` is non-``None`` (a
           tuple — possibly empty), the coordinator materializes the
           allowlist as ``enabled_connectors`` (each slug → active with
           no extra scopes). Empty tuple → empty map (explicit denial).
        3. **Workspace defaults.** Falls through to
           :meth:`_seed_default_connectors_if_needed` for the existing
           Phase 1 behavior — owner-wide defaults seed the chat.

        Idempotent when the request carries an idempotency key: a
        duplicate call returns the existing record rather than inserting
        a new row.
        """

        conversation = await self._persistence.create_conversation(request)
        seeded, inherited_from_project = await self._apply_connector_inheritance(
            conversation=conversation,
            request=request,
        )
        await self._persistence.write_audit_log(
            event_type="conversation_created",
            record={
                "org_id": seeded.org_id,
                "user_id": seeded.user_id,
                "resource_type": "conversation",
                "resource_id": seeded.conversation_id,
                "outcome": "success",
                # PRD §10.3 audit context: did this chat inherit its
                # connectors from the project default? Ops dashboards
                # answer "what fraction of chats inherit project
                # defaults vs. user-chosen?" from this flag.
                "context": {
                    "project_id": request.project_id,
                    "inherited_from_project_default": inherited_from_project,
                },
            },
        )
        return seeded.to_response()

    async def update_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        request: UpdateConversationRequest,
        allow_admin_override: bool = False,
    ) -> ConversationResponse:
        """Apply a partial update (title, folder, archived) to the conversation.

        When ``allow_admin_override`` is set and the conversation belongs to a
        different user, the admin path is taken and the audit row records the
        override. Caller must pass only fields that are explicitly set in the
        request body; unset fields are ignored by the store.
        """

        before, is_admin_override = await self._conversation_for_owner_or_admin(
            org_id=org_id,
            actor_user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )
        fields_set = request.model_fields_set
        title_changed = "title" in fields_set
        folder_changed = "folder" in fields_set
        archived_changed = "archived" in fields_set
        now = datetime.now(timezone.utc)
        updated = await self._persistence.update_conversation(
            org_id=org_id,
            user_id=before.user_id,
            conversation_id=conversation_id,
            title=request.title,
            title_changed=title_changed,
            folder=request.folder,
            folder_changed=folder_changed,
            archived=request.archived,
            archived_changed=archived_changed,
            now=now,
        )
        if updated is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        audit_metadata = _conversation_lifecycle_audit_metadata(
            before=before,
            after=updated,
            fields_set=fields_set,
        )
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = before.user_id
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_UPDATE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        return updated.to_response()

    async def update_conversation_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        request: UpdateConversationConnectorsRequest,
        allow_admin_override: bool = False,
    ) -> ConversationConnectorScopesResponse:
        """Merge-patch the conversation's per-chat connector scope overrides.

        Only connectors included in the request are modified; others keep their
        current value. ``None`` in the patch removes the override for that connector.
        """

        before, is_admin_override = await self._conversation_for_owner_or_admin(
            org_id=org_id,
            actor_user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )
        now = datetime.now(timezone.utc)
        updated = await self._persistence.update_conversation_connectors(
            org_id=org_id,
            user_id=before.user_id,
            conversation_id=conversation_id,
            scopes_patch=request.scopes,
            now=now,
        )
        if updated is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        audit_metadata = _connector_scope_audit_metadata(
            before=before.enabled_connectors,
            patch=request.scopes,
            after=updated.enabled_connectors,
        )
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = before.user_id
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_CONNECTORS_UPDATE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        return ConversationConnectorScopesResponse(
            conversation_id=updated.conversation_id,
            scopes=updated.enabled_connectors,
            updated_at=updated.connectors_updated_at,
        )

    async def delete_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        allow_admin_override: bool = False,
    ) -> None:
        """Soft-delete a conversation, cancelling any active run first.

        Soft delete preserves the row for retention-policy sweep; the ``deleted_at``
        timestamp drives hard-delete scheduling via ``_resolve_conversation_retention_until``.
        """

        conversation, is_admin_override = await self._conversation_for_owner_or_admin(
            org_id=org_id,
            actor_user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )
        await self._cancel_active_run_for_conversation(
            org_id=org_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
        )
        now = datetime.now(timezone.utc)
        await self._persistence.soft_delete_conversation(
            org_id=org_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
            now=now,
        )
        retention_until = await self._resolve_conversation_retention_until(
            org_id=org_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
            assistant_id=conversation.assistant_id,
            deleted_at=now,
        )
        audit_metadata: dict[str, object] = {
            "conversation_id": conversation_id,
            "folder": conversation.folder,
            "retention_until": (
                retention_until.isoformat() if retention_until is not None else None
            ),
        }
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = conversation.user_id
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_DELETE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )

    async def restore_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        allow_admin_override: bool = False,
    ) -> ConversationResponse:
        """Restore a soft-deleted conversation back to active status."""

        now = datetime.now(timezone.utc)
        owner_user_id = user_id
        is_admin_override = False
        if allow_admin_override:
            owner_lookup = await self._persistence.get_conversation(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if owner_lookup is None:
                admin_view = await self._persistence.get_conversation_for_org(
                    org_id=org_id, conversation_id=conversation_id
                )
                if admin_view is None:
                    raise RuntimeApiError(
                        RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                        Messages.Error.CONVERSATION_NOT_FOUND,
                        http_status=status.HTTP_404_NOT_FOUND,
                        retryable=False,
                    )
                owner_user_id = admin_view.user_id
                is_admin_override = True
        restored = await self._persistence.restore_conversation(
            org_id=org_id,
            user_id=owner_user_id,
            conversation_id=conversation_id,
            now=now,
        )
        if restored is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        audit_metadata: dict[str, object] = {"conversation_id": conversation_id}
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = owner_user_id
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_RESTORE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        return restored.to_response()

    async def set_conversation_pinned(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        pinned: bool,
        allow_admin_override: bool = False,
    ) -> ConversationResponse:
        """Pin or unpin a conversation and emit a before/after audit row (PRD-H.4).

        Idempotent: re-pinning an already-pinned chat returns the current
        row unchanged. When ``allow_admin_override`` is set and the chat
        belongs to another user, the admin path is taken and the override
        is recorded in the audit metadata.
        """

        before, is_admin_override = await self._conversation_for_owner_or_admin(
            org_id=org_id,
            actor_user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )
        now = datetime.now(timezone.utc)
        updated = await self._persistence.set_conversation_pinned(
            org_id=org_id,
            user_id=before.user_id,
            conversation_id=conversation_id,
            pinned=pinned,
            now=now,
        )
        if updated is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        audit_metadata: dict[str, object] = {
            "conversation_id": conversation_id,
            "pinned_before": before.pinned,
            "pinned_after": updated.pinned,
        }
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = before.user_id
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_PIN,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        return updated.to_response()

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Bulk-delete all conversation history for a user and record evidence.

        The store archiving/tombstoning is delegated to persistence; this method
        appends the audit row so deletion is traceable regardless of which code
        path triggered it.
        """

        result = await self._persistence.delete_user_history(
            org_id=org_id, user_id=user_id, reason=reason
        )
        await self._persistence.write_audit_log(
            event_type="user_history_deleted",
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "user_history",
                "resource_id": user_id,
                "outcome": "success",
                "metadata": {
                    "reason": reason,
                    "conversations_archived": result.conversations_archived,
                    "messages_tombstoned": result.messages_tombstoned,
                    "runs_cancelled": result.runs_cancelled,
                    "events_retained": result.events_retained,
                },
            },
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _conversation_for_scope(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ):
        """Return the conversation or raise 404 if it is outside the caller's scope."""
        conv = await self._persistence.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conv is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return conv

    async def _conversation_for_owner_or_admin(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        conversation_id: str,
        allow_admin_override: bool,
    ) -> tuple[ConversationRecord, bool]:
        """Resolve the conversation for owner access or, if permitted, admin override.

        Returns ``(record, is_admin_override)`` so callers can attach override
        evidence to their audit rows.
        """
        conversation = await self._persistence.get_conversation(
            org_id=org_id,
            user_id=actor_user_id,
            conversation_id=conversation_id,
        )
        if conversation is not None:
            # Fast path: actor is the owner.
            return conversation, False
        if not allow_admin_override:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        # Slow path: org-wide lookup without user filter for admin actors.
        admin_view = await self._persistence.get_conversation_for_org(
            org_id=org_id,
            conversation_id=conversation_id,
        )
        if admin_view is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return admin_view, True

    async def _cancel_active_run_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> None:
        """Best-effort cancel of any active run before the conversation is deleted.

        No-op when the conversation has no active run.
        """
        active_run = await self._persistence.get_active_run_for_conversation(
            org_id=org_id, conversation_id=conversation_id
        )
        if active_run is None:
            return
        await self._run_coordinator.cancel_run(
            org_id=org_id,
            user_id=user_id,
            run_id=active_run.run_id,
            request=CancelRunRequest(
                requested_by_user_id=user_id,
                reason="conversation_deleted",
            ),
        )

    async def _apply_connector_inheritance(
        self,
        *,
        conversation: ConversationRecord,
        request: CreateConversationRequest,
    ) -> tuple[ConversationRecord, bool]:
        """Resolve the connector-inheritance ladder for a freshly created conversation.

        Returns ``(conversation_record, inherited_from_project_default)``
        so the caller can stamp the audit row's
        ``context.inherited_from_project_default`` flag.

        Order of precedence (PRD §5.4):

        1. **Caller-explicit map** (``request.enabled_connectors`` is not
           ``None``) — write verbatim; both project and workspace
           seeding are skipped. ``inherited_from_project_default`` is
           ``False``.
        2. **Project allowlist** (``project_id`` set and the project's
           ``default_connector_allowlist`` resolves to a tuple — empty
           or non-empty). Empty tuple → write an empty map (explicit
           denial; PRD §5.4). Non-empty → materialize each slug as an
           active entry. ``inherited_from_project_default`` is ``True``.
        3. **Workspace defaults fall-through** — existing Phase 1
           behavior; the workspace's ``default_connectors`` map seeds
           the chat. ``inherited_from_project_default`` is ``False``.
        """

        # Rule 1: caller-explicit wins. ``None`` means "not passed" —
        # eligible for inheritance. ``{}`` means "explicit empty" —
        # caller wins and we skip both project and workspace seeding.
        if request.enabled_connectors is not None:
            if not request.enabled_connectors:
                return conversation, False
            now = datetime.now(timezone.utc)
            updated = await self._persistence.update_conversation_connectors(
                org_id=conversation.org_id,
                user_id=conversation.user_id,
                conversation_id=conversation.conversation_id,
                scopes_patch=dict(request.enabled_connectors),
                now=now,
            )
            return updated or conversation, False

        # Rule 2: project allowlist. Only consulted when ``project_id``
        # is set. The resolver returns ``None`` on every failure mode
        # (network, non-2xx, missing column, unknown project) so a bad
        # project id never blocks create — we just fall through to
        # workspace defaults.
        if request.project_id is not None:
            allowlist = await self._project_resolver.fetch_connector_allowlist(
                org_id=conversation.org_id,
                user_id=conversation.user_id,
                project_id=request.project_id,
            )
            if allowlist is not None:
                # Materialize: each slug → active with no extra scopes.
                # An empty allowlist materializes to an empty map; the
                # PRD's "explicit denial" reading means we still stamp
                # ``inherited_from_project_default=True`` so audit
                # consumers can answer "what fraction of chats inherited
                # from the project default?" — including the empty case.
                materialized: dict[str, tuple[str, ...] | None] = {
                    slug: () for slug in allowlist
                }
                if not materialized:
                    # No write needed — the conversation already has an
                    # empty enabled_connectors map; we keep the record
                    # as-is. The flag still reads True because the
                    # project's policy was consulted and applied.
                    return conversation, True
                now = datetime.now(timezone.utc)
                updated = await self._persistence.update_conversation_connectors(
                    org_id=conversation.org_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    scopes_patch=materialized,
                    now=now,
                )
                return updated or conversation, True

        # Rule 3: workspace-defaults fall-through (existing behavior).
        seeded = await self._seed_default_connectors_if_needed(
            conversation=conversation
        )
        return seeded, False

    async def _seed_default_connectors_if_needed(
        self, *, conversation: ConversationRecord
    ) -> ConversationRecord:
        """Apply workspace default connectors to a newly created conversation.

        Skipped when the conversation already has explicit connector assignments,
        or when the workspace has no defaults configured.
        """
        if conversation.enabled_connectors:
            return conversation
        defaults = await self._workspace_defaults().get_record(
            org_id=conversation.org_id
        )
        if defaults is None or not defaults.default_connectors:
            return conversation
        now = datetime.now(timezone.utc)
        updated = await self._persistence.update_conversation_connectors(
            org_id=conversation.org_id,
            user_id=conversation.user_id,
            conversation_id=conversation.conversation_id,
            scopes_patch=defaults.default_connectors,
            now=now,
        )
        return updated or conversation

    def _workspace_defaults(self):
        """Return a ``WorkspaceDefaultsService`` instance bound to this coordinator's deps.

        Imported lazily to avoid a module-level circular dependency.
        """
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        return WorkspaceDefaultsService(
            persistence=self._persistence,
            settings=self._settings,
        )

    async def _resolve_conversation_retention_until(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        assistant_id: str,
        deleted_at: datetime,
    ) -> datetime | None:
        """Compute the hard-delete deadline for a soft-deleted conversation.

        Returns ``None`` when no retention policy applies, meaning the store
        may hard-delete immediately (or rely on its own default sweep).
        Imports are deferred to keep the module-load cost low for code paths
        that never touch retention.
        """
        from datetime import timedelta

        from agent_runtime.persistence.records.retention import RetentionKind
        from agent_runtime.retention import (
            DEPLOYMENT_DEFAULT_TTL_SECONDS,
            RetentionPolicyResolver,
        )

        policies = await self._persistence.list_retention_policies(org_id=org_id)
        resolver = RetentionPolicyResolver(
            org_id=org_id,
            policies=policies,
            deployment_defaults=DEPLOYMENT_DEFAULT_TTL_SECONDS,
        )
        resolved = resolver.resolve(
            kind=RetentionKind.MESSAGES,
            conversation_id=conversation_id,
            user_id=user_id,
            assistant_id=assistant_id,
        )
        if resolved.ttl_seconds is None:
            return None
        return deleted_at + timedelta(seconds=resolved.ttl_seconds)
