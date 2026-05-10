"""Unit tests for the PR A3 / 8.0.3c ``SelfForkService``.

The owner-driven self-fork mirrors the share-fork's persistence path
without the share-token machinery: the only authority required is the
caller owning the source conversation in the same tenant. These tests
cover the happy path, message-id mismatch (404), source-not-found
(404), source-deleted (404), and the message-cap clamp.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.self_fork import SelfForkService
from agent_runtime.execution.contracts import RuntimeErrorCode
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    CreateConversationRequest,
    MessageRecord,
    SelfForkRequest,
)
from runtime_api.schemas.common import MessageRole, MessageStatus
from runtime_worker.audit import WorkerAuditEmitter

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Values:
    ORG = "org_acme"
    OTHER_ORG = "org_intruder"
    OWNER = "user_sarah"
    OUTSIDER = "user_priya"
    SOURCE_CONV = "conv_self_fork"
    TITLE = "FY26 Q1 launch announcement draft"


def _make_store() -> tuple[InMemoryRuntimeApiStore, InMemoryRuntimeApiStore]:
    sync_store = InMemoryRuntimeApiStore()
    async_store = sync_store
    return async_store, sync_store


def _make_service(
    *,
    async_store: InMemoryRuntimeApiStore,
    max_messages: int | None = None,
) -> SelfForkService:
    return SelfForkService(
        persistence=async_store,
        audit=WorkerAuditEmitter(async_store),
        max_messages=max_messages,
    )


async def _seed_source(
    async_store: InMemoryRuntimeApiStore,
    *,
    message_count: int = 4,
    deleted: bool = False,
    org_id: str = _Values.ORG,
    user_id: str = _Values.OWNER,
):
    record = await async_store.create_conversation(
        CreateConversationRequest(
            org_id=org_id,
            user_id=user_id,
            title=_Values.TITLE,
        )
    )
    record = record.model_copy(update={"conversation_id": _Values.SOURCE_CONV})
    async_store.conversations[_Values.SOURCE_CONV] = record
    if deleted:
        async_store.conversations[_Values.SOURCE_CONV] = record.model_copy(
            update={"deleted_at": datetime.now(timezone.utc)}
        )
    for index in range(message_count):
        message = MessageRecord(
            message_id=f"m{index}",
            conversation_id=_Values.SOURCE_CONV,
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
    return async_store.conversations[_Values.SOURCE_CONV]


class TestSelfForkHappyPath:
    async def test_fork_from_message_creates_owned_conversation_with_message_lineage(
        self,
    ) -> None:
        async_store, sync_store = _make_store()
        await _seed_source(async_store, message_count=4)
        service = _make_service(async_store=async_store)

        response = await service.fork(
            conversation_id=_Values.SOURCE_CONV,
            actor_org_id=_Values.ORG,
            actor_user_id=_Values.OWNER,
            request=SelfForkRequest(from_message_id="m2"),
        )

        # Lineage. The new row points at the source via
        # ``parent_conversation_id`` and at the fork point via
        # ``forked_from_message_id``; ``forked_from_share_id`` stays
        # NULL on every self-fork row.
        assert response.parent_conversation_id == _Values.SOURCE_CONV
        assert response.forked_from_message_id == "m2"
        assert response.forked_from_share_id is None
        assert response.fork_message_count == 3  # m0, m1, m2
        assert response.user_id == _Values.OWNER

        # The source's owner owns the new row, in the same tenant.
        new_conv = sync_store.conversations[response.conversation_id]
        assert new_conv.org_id == _Values.ORG
        assert new_conv.user_id == _Values.OWNER
        assert new_conv.parent_conversation_id == _Values.SOURCE_CONV
        assert new_conv.forked_from_message_id == "m2"
        assert new_conv.forked_from_share_id is None
        # Default title carries the share-fork prefix shape so the
        # sidebar treats both fork pathways the same visually.
        assert new_conv.title == f"Forked from {_Values.TITLE}"

        # Copied messages — exactly the slice up to and including m2.
        copied = [
            m
            for m in sync_store.messages.values()
            if m.conversation_id == response.conversation_id
        ]
        assert len(copied) == 3
        assert {m.role for m in copied} == {MessageRole.USER, MessageRole.ASSISTANT}
        # ``run_id`` / ``source_message_id`` / ``branch_id`` are reset
        # by the MessageCopyPlanner so the next prompt creates a fresh
        # run; the original message id lives on
        # ``metadata.original_message_id`` for forensics.
        assert {m.run_id for m in copied} == {None}
        assert {m.source_message_id for m in copied} == {None}
        original_ids = {(m.metadata or {}).get("original_message_id") for m in copied}
        assert original_ids == {"m0", "m1", "m2"}

    async def test_explicit_title_and_folder_override_defaults(self) -> None:
        async_store, sync_store = _make_store()
        await _seed_source(async_store, message_count=2)
        service = _make_service(async_store=async_store)

        response = await service.fork(
            conversation_id=_Values.SOURCE_CONV,
            actor_org_id=_Values.ORG,
            actor_user_id=_Values.OWNER,
            request=SelfForkRequest(
                from_message_id="m1",
                title="Try the press-friendly framing",
                folder="Launches",
            ),
        )

        new_conv = sync_store.conversations[response.conversation_id]
        assert new_conv.title == "Try the press-friendly framing"
        assert new_conv.folder == "Launches"


class TestSelfForkFailureModes:
    async def test_unknown_conversation_returns_404(self) -> None:
        async_store, _ = _make_store()
        service = _make_service(async_store=async_store)

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                conversation_id="conv_missing",
                actor_org_id=_Values.ORG,
                actor_user_id=_Values.OWNER,
                request=SelfForkRequest(from_message_id="m0"),
            )
        assert exc_info.value.envelope.code == RuntimeErrorCode.CAPABILITY_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_cross_tenant_caller_returns_404_not_403(self) -> None:
        async_store, _ = _make_store()
        await _seed_source(async_store, message_count=2)
        service = _make_service(async_store=async_store)

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                conversation_id=_Values.SOURCE_CONV,
                actor_org_id=_Values.OTHER_ORG,
                actor_user_id=_Values.OUTSIDER,
                request=SelfForkRequest(from_message_id="m1"),
            )
        # Cross-tenant must look identical to "doesn't exist" so the
        # endpoint never leaks tenant-existence across boundaries.
        assert exc_info.value.http_status == 404

    async def test_soft_deleted_source_returns_404(self) -> None:
        async_store, _ = _make_store()
        await _seed_source(async_store, message_count=2, deleted=True)
        service = _make_service(async_store=async_store)

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                conversation_id=_Values.SOURCE_CONV,
                actor_org_id=_Values.ORG,
                actor_user_id=_Values.OWNER,
                request=SelfForkRequest(from_message_id="m0"),
            )
        assert exc_info.value.http_status == 404

    async def test_unknown_message_id_returns_404(self) -> None:
        async_store, _ = _make_store()
        await _seed_source(async_store, message_count=2)
        service = _make_service(async_store=async_store)

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                conversation_id=_Values.SOURCE_CONV,
                actor_org_id=_Values.ORG,
                actor_user_id=_Values.OWNER,
                request=SelfForkRequest(from_message_id="m_missing"),
            )
        assert exc_info.value.http_status == 404

    async def test_message_cap_overflow_returns_422(self) -> None:
        async_store, _ = _make_store()
        await _seed_source(async_store, message_count=4)
        service = _make_service(async_store=async_store, max_messages=2)

        with pytest.raises(RuntimeApiError) as exc_info:
            await service.fork(
                conversation_id=_Values.SOURCE_CONV,
                actor_org_id=_Values.ORG,
                actor_user_id=_Values.OWNER,
                request=SelfForkRequest(from_message_id="m3"),
            )
        assert exc_info.value.http_status == 422
        assert exc_info.value.envelope.code == RuntimeErrorCode.VALIDATION_ERROR


class TestSelfForkAudit:
    async def test_audit_row_carries_from_message_id_not_share_id(self) -> None:
        async_store, sync_store = _make_store()
        await _seed_source(async_store, message_count=3)
        service = _make_service(async_store=async_store)

        response = await service.fork(
            conversation_id=_Values.SOURCE_CONV,
            actor_org_id=_Values.ORG,
            actor_user_id=_Values.OWNER,
            request=SelfForkRequest(from_message_id="m1"),
        )

        # The audit emitter writes into the in-memory store. Self-fork
        # rows MUST carry ``from_message_id`` and MUST NOT carry
        # ``share_id`` — that's how SIEM exports tell the two fork
        # pathways apart from a single audit-row shape.
        fork_rows = [
            record
            for event_type, record in sync_store.audit_log
            if event_type == "conversation.fork"
        ]
        assert len(fork_rows) == 1, "expected one fork audit row"
        record = fork_rows[0]
        # Audit fields land on the row itself (resource_id, actor_user_id),
        # the original metadata is nested under "metadata".
        metadata = record.get("metadata") or {}
        assert metadata.get("from_message_id") == "m1"
        assert metadata.get("source_conversation_id") == _Values.SOURCE_CONV
        assert metadata.get("target_conversation_id") == response.conversation_id
        assert "share_id" not in metadata
