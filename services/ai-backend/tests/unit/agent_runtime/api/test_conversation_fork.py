"""Unit tests for the PR 6.2 ``ConversationForkService``.

Covers the full permission matrix (workspace gate, specific gate,
cross-org refusal, revoked share, expired share, source soft-deleted),
the message-snapshot clamp + cap, audit emission, lineage pointers on
the new conversation, and the post-commit notification fan-out.

The tests use the in-memory persistence + share snapshot adapters; no
network, no real codec, no LLM.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from agent_runtime.api.conversation_fork import ConversationForkService
from agent_runtime.api.notifications import LoggingNotificationDispatcher
from agent_runtime.execution.contracts import RuntimeErrorCode
from runtime_adapters.in_memory.async_runtime_api_store import (
    AsyncInMemoryRuntimeApiStore,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.share_snapshot_store import InMemoryShareSnapshotStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ConversationRecord,
    CreateConversationRequest,
    ForkRequest,
    MessageRecord,
    ShareSnapshot,
)
from runtime_api.schemas.common import (
    MessageRole,
    MessageStatus,
)
from runtime_worker.audit import WorkerAuditEmitter

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _ForkFixtureMixin:
    class Values:
        ORG = "org_acme"
        OTHER_ORG = "org_intruder"
        SHARING_USER = "user_sarah"
        RECIPIENT = "user_marcus"
        OUTSIDER = "user_priya"
        SOURCE_CONV = "conv_launch"
        SHARE_TOKEN = "s_3f7b2c9a04d1e8c5"
        SHARE_ID = "share_01HZ"
        REVOKED_TOKEN = "s_revoked"
        EXPIRED_TOKEN = "s_expired"
        SPECIFIC_TOKEN = "s_specific"
        ORPHAN_TOKEN = "s_orphan_source"
        TITLE = "FY26 Q1 launch announcement draft"

    def make_store(
        self,
    ) -> tuple[AsyncInMemoryRuntimeApiStore, InMemoryRuntimeApiStore]:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        return async_store, sync_store

    def make_share_store(self) -> InMemoryShareSnapshotStore:
        return InMemoryShareSnapshotStore()

    def make_audit(self, store) -> WorkerAuditEmitter:
        # The in-memory store implements ``write_audit_log``; the
        # emitter writes the audit row into ``store.audit_events``.
        return WorkerAuditEmitter(store)

    def make_service(
        self,
        *,
        async_store: AsyncInMemoryRuntimeApiStore,
        share_store: InMemoryShareSnapshotStore,
        audit: WorkerAuditEmitter,
        notifications=None,
        max_messages: int | None = None,
    ) -> ConversationForkService:
        return ConversationForkService(
            persistence=async_store,
            share_snapshots=share_store,
            audit=audit,
            notifications=notifications or LoggingNotificationDispatcher(),
            max_messages=max_messages,
        )

    async def seed_source_conversation(
        self,
        async_store: AsyncInMemoryRuntimeApiStore,
        *,
        message_count: int = 3,
        deleted: bool = False,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> ConversationRecord:
        org_id = org_id or self.Values.ORG
        user_id = user_id or self.Values.SHARING_USER
        record = await async_store.create_conversation(
            CreateConversationRequest(
                org_id=org_id,
                user_id=user_id,
                title=self.Values.TITLE,
            )
        )
        # The default record is fine — overwrite the conversation_id so
        # later assertions can pin the share row to a known id.
        record = record.model_copy(update={"conversation_id": self.Values.SOURCE_CONV})
        async_store.underlying.conversations[self.Values.SOURCE_CONV] = record
        if deleted:
            async_store.underlying.conversations[self.Values.SOURCE_CONV] = (
                record.model_copy(update={"deleted_at": datetime.now(timezone.utc)})
            )
        for index in range(message_count):
            message = MessageRecord(
                message_id=f"m{index}",
                conversation_id=self.Values.SOURCE_CONV,
                org_id=org_id,
                run_id="run_seed",
                role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                content_text=f"turn {index}",
                parent_message_id=f"m{index - 1}" if index > 0 else None,
                source_message_id=None,
                branch_id="branch_alpha",
                status=MessageStatus.CREATED,
                created_at=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)
                + timedelta(seconds=index),
            )
            await async_store.append_message(message)
        return async_store.underlying.conversations[self.Values.SOURCE_CONV]

    def register_share(
        self,
        share_store: InMemoryShareSnapshotStore,
        *,
        token: str | None = None,
        view_access: str = "workspace",
        recipient_user_ids: tuple[str, ...] = (),
        snapshot_at: datetime | None = None,
        sources_visible_to_viewer: bool = False,
        org_id: str | None = None,
        conversation_id: str | None = None,
        share_id: str | None = None,
    ) -> ShareSnapshot:
        snapshot = ShareSnapshot(
            share_id=share_id or self.Values.SHARE_ID,
            org_id=org_id or self.Values.ORG,
            conversation_id=conversation_id or self.Values.SOURCE_CONV,
            snapshot_at=snapshot_at or datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc),
            view_access=view_access,
            recipient_user_ids=recipient_user_ids,
            sources_visible_to_viewer=sources_visible_to_viewer,
            created_by_user_id=self.Values.SHARING_USER,
        )
        share_store.register(token=token or self.Values.SHARE_TOKEN, snapshot=snapshot)
        return snapshot


class TestForkHappyPath(_ForkFixtureMixin):
    async def test_workspace_share_creates_owned_conversation_with_lineage(
        self,
    ) -> None:
        async_store, sync_store = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=2)
        snapshot = self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        response = await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )

        assert response.parent_conversation_id == self.Values.SOURCE_CONV
        assert response.forked_from_share_id == snapshot.share_id
        assert response.fork_message_count == 2
        assert response.user_id == self.Values.RECIPIENT

        new_conv = sync_store.conversations[response.conversation_id]
        assert new_conv.user_id == self.Values.RECIPIENT
        assert new_conv.org_id == self.Values.ORG
        assert new_conv.parent_conversation_id == self.Values.SOURCE_CONV
        assert new_conv.forked_from_share_id == snapshot.share_id
        # The fork starts with empty connector scope so the workspace
        # defaults / next-run resolution chain (PR 1.6) applies cleanly.
        assert new_conv.enabled_connectors == {}
        assert new_conv.metadata.get("forked_from_share_id") == snapshot.share_id

    async def test_message_copies_get_new_ids_and_reset_runs(self) -> None:
        async_store, sync_store = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=3)
        self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        response = await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )

        copies = [
            message
            for message in sync_store.messages.values()
            if message.conversation_id == response.conversation_id
        ]
        assert len(copies) == 3
        for copy in copies:
            assert copy.run_id is None
            assert copy.source_message_id is None
            assert copy.branch_id is None
            assert copy.org_id == self.Values.ORG
            assert copy.metadata["original_conversation_id"] == self.Values.SOURCE_CONV
        # Parent rewrite: every non-root message points at one of our
        # copies, never the source ids.
        new_ids = {copy.message_id for copy in copies}
        for copy in copies:
            if copy.parent_message_id is not None:
                assert copy.parent_message_id in new_ids
                assert not copy.parent_message_id.startswith("m")

    async def test_audit_row_emitted_with_lineage_metadata(self) -> None:
        async_store, sync_store = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=2)
        snapshot = self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )

        fork_audit = [
            (event_type, record)
            for event_type, record in sync_store.audit_log
            if event_type == "conversation.fork"
        ]
        assert len(fork_audit) == 1
        _, record = fork_audit[0]
        metadata = record.get("metadata") or {}
        assert metadata["source_conversation_id"] == self.Values.SOURCE_CONV
        assert metadata["share_id"] == snapshot.share_id
        assert metadata["message_count"] == 2

    async def test_default_title_falls_back_to_forked_from_source(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        response = await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )

        assert response.title == f"Forked from {self.Values.TITLE}"

    async def test_explicit_title_and_folder_override_defaults(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        response = await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(title="My exploration", folder="Launches"),
        )

        assert response.title == "My exploration"
        assert response.folder == "Launches"


class TestForkPermissionMatrix(_ForkFixtureMixin):
    async def test_unknown_token_returns_404(self) -> None:
        async_store, _ = self.make_store()
        service = self.make_service(
            async_store=async_store,
            share_store=self.make_share_store(),
            audit=self.make_audit(async_store),
        )

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                share_token="s_unknown",
                recipient_org_id=self.Values.ORG,
                recipient_user_id=self.Values.RECIPIENT,
                request=ForkRequest(),
            )

        assert exc_info.value.envelope.code == RuntimeErrorCode.CAPABILITY_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_revoked_token_returns_404(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        self.register_share(share_store, token=self.Values.REVOKED_TOKEN)
        share_store.revoke(self.Values.REVOKED_TOKEN)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                share_token=self.Values.REVOKED_TOKEN,
                recipient_org_id=self.Values.ORG,
                recipient_user_id=self.Values.RECIPIENT,
                request=ForkRequest(),
            )

        assert exc_info.value.http_status == 404

    async def test_expired_token_returns_404(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        snapshot = ShareSnapshot(
            share_id=self.Values.SHARE_ID,
            org_id=self.Values.ORG,
            conversation_id=self.Values.SOURCE_CONV,
            snapshot_at=datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc),
            view_access="workspace",
            sources_visible_to_viewer=False,
            created_by_user_id=self.Values.SHARING_USER,
        )
        # Expired one second in the past.
        share_store.register(
            token=self.Values.EXPIRED_TOKEN,
            snapshot=snapshot,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                share_token=self.Values.EXPIRED_TOKEN,
                recipient_org_id=self.Values.ORG,
                recipient_user_id=self.Values.RECIPIENT,
                request=ForkRequest(),
            )

        assert exc_info.value.http_status == 404

    async def test_cross_org_caller_returns_404_no_leak(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                share_token=self.Values.SHARE_TOKEN,
                recipient_org_id=self.Values.OTHER_ORG,
                recipient_user_id=self.Values.RECIPIENT,
                request=ForkRequest(),
            )

        assert exc_info.value.envelope.code == RuntimeErrorCode.CAPABILITY_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_specific_share_rejects_unlisted_recipient_with_403(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        self.register_share(
            share_store,
            token=self.Values.SPECIFIC_TOKEN,
            view_access="specific",
            recipient_user_ids=(self.Values.RECIPIENT,),
        )
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                share_token=self.Values.SPECIFIC_TOKEN,
                recipient_org_id=self.Values.ORG,
                recipient_user_id=self.Values.OUTSIDER,
                request=ForkRequest(),
            )

        assert exc_info.value.envelope.code == RuntimeErrorCode.PERMISSION_DENIED
        assert exc_info.value.http_status == 403

    async def test_specific_share_accepts_listed_recipient(self) -> None:
        async_store, sync_store = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        self.register_share(
            share_store,
            token=self.Values.SPECIFIC_TOKEN,
            view_access="specific",
            recipient_user_ids=(self.Values.RECIPIENT,),
        )
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        response = await service.fork(
            share_token=self.Values.SPECIFIC_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )

        assert sync_store.conversations[response.conversation_id].user_id == (
            self.Values.RECIPIENT
        )

    async def test_soft_deleted_source_returns_404(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1, deleted=True)
        self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                share_token=self.Values.SHARE_TOKEN,
                recipient_org_id=self.Values.ORG,
                recipient_user_id=self.Values.RECIPIENT,
                request=ForkRequest(),
            )

        assert exc_info.value.http_status == 404


class TestForkBoundaries(_ForkFixtureMixin):
    async def test_message_cap_exceeded_returns_422(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=5)
        self.register_share(share_store)
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
            max_messages=4,
        )

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                share_token=self.Values.SHARE_TOKEN,
                recipient_org_id=self.Values.ORG,
                recipient_user_id=self.Values.RECIPIENT,
                request=ForkRequest(),
            )

        assert exc_info.value.envelope.code == RuntimeErrorCode.VALIDATION_ERROR
        assert exc_info.value.http_status == 422

    async def test_snapshot_clamp_excludes_messages_after_snapshot_at(self) -> None:
        async_store, sync_store = self.make_store()
        share_store = self.make_share_store()
        # 5 messages at offsets 0..4 seconds. Snapshot before #4 means
        # only messages 0..2 are in the copy set (offset 3 is past the
        # snapshot_at).
        await self.seed_source_conversation(async_store, message_count=5)
        self.register_share(
            share_store,
            snapshot_at=datetime(2026, 5, 5, 12, 0, 2, tzinfo=timezone.utc),
        )
        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
        )

        response = await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )

        assert response.fork_message_count == 3


class TestForkNotificationFanOut(_ForkFixtureMixin):
    async def test_notification_dispatcher_invoked_after_commit(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        snapshot = self.register_share(share_store)

        observed: list[dict[str, Any]] = []

        class RecordingDispatcher(LoggingNotificationDispatcher):
            async def notify_share_forked(
                self,
                *,
                share,
                forked_by_user_id: str,
                new_conversation_id: str,
            ) -> None:
                observed.append(
                    {
                        "share_id": share.share_id,
                        "forked_by_user_id": forked_by_user_id,
                        "new_conversation_id": new_conversation_id,
                    }
                )

        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
            notifications=RecordingDispatcher(),
        )

        response = await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )
        # The dispatcher fires off the request thread via
        # ``asyncio.create_task``; yield once so the task completes
        # before we assert.
        await asyncio.sleep(0)

        assert observed == [
            {
                "share_id": snapshot.share_id,
                "forked_by_user_id": self.Values.RECIPIENT,
                "new_conversation_id": response.conversation_id,
            }
        ]

    async def test_notification_failure_does_not_abort_fork(self) -> None:
        async_store, _ = self.make_store()
        share_store = self.make_share_store()
        await self.seed_source_conversation(async_store, message_count=1)
        self.register_share(share_store)

        class BoomDispatcher(LoggingNotificationDispatcher):
            async def notify_share_forked(self, **kwargs) -> None:
                raise RuntimeError("notify pipeline down")

        service = self.make_service(
            async_store=async_store,
            share_store=share_store,
            audit=self.make_audit(async_store),
            notifications=BoomDispatcher(),
        )

        response = await service.fork(
            share_token=self.Values.SHARE_TOKEN,
            recipient_org_id=self.Values.ORG,
            recipient_user_id=self.Values.RECIPIENT,
            request=ForkRequest(),
        )
        await asyncio.sleep(0)

        # Fork still committed; recipient sees the new conversation.
        assert response.conversation_id is not None
