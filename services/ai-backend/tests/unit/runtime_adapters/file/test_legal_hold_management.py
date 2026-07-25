"""Durability and lifecycle parity tests for file-backed legal holds."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from agent_runtime.persistence.records import (
    LegalHoldReasonCode,
    LegalHoldRecord,
    LegalHoldScope,
)
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest

_ORG = "org_file_hold"
_USER = "user_file_hold"


def _record(*, conversation_id: str) -> LegalHoldRecord:
    return LegalHoldRecord(
        id="lh_file_persisted",
        org_id=_ORG,
        scope=LegalHoldScope.CONVERSATION,
        resource_id=conversation_id,
        subject_user_id=_USER,
        reason_code=LegalHoldReasonCode.LEGAL_REQUEST,
        created_by_user_id="user_retention_admin",
        create_idempotency_key="file-create-hold-001",
        create_request_digest=hashlib.sha256(b"file-create").hexdigest(),
    )


def _audit(*, action: str, hold_id: str) -> dict[str, object]:
    return {
        "org_id": _ORG,
        "user_id": "user_retention_admin",
        "actor_type": "user",
        "action": action,
        "resource_type": "legal_hold",
        "resource_id": hold_id,
        "outcome": "success",
        "metadata": {"scope": "conversation", "reason_code": "legal_request"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class TestFileLegalHoldManagement:
    async def test_hold_survives_restart_and_blocks_until_revisioned_release(
        self, tmp_path
    ) -> None:
        root = tmp_path / "legal-hold-file-store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation = await store.create_conversation(
            CreateConversationRequest(
                org_id=_ORG,
                user_id=_USER,
                assistant_id="assistant_file_hold",
            )
        )
        record = _record(conversation_id=conversation.conversation_id)
        created = await store.create_legal_hold(
            record=record,
            audit_event=_audit(action="legal_hold.created", hold_id=record.id),
        )
        assert created.replayed is False
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        persisted = await reopened.list_legal_holds(
            org_id=_ORG, include_released=False, limit=10
        )
        assert [hold.id for hold in persisted] == [record.id]
        assert persisted[0].resource_id == conversation.conversation_id

        retry = await reopened.create_legal_hold(
            record=record,
            audit_event=_audit(action="legal_hold.created", hold_id=record.id),
        )
        assert retry.replayed is True

        blocked = await reopened.soft_delete_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            now=datetime.now(timezone.utc),
        )
        assert blocked is not None
        assert blocked.deleted_at is None

        released = await reopened.release_legal_hold(
            org_id=_ORG,
            hold_id=record.id,
            expected_revision=1,
            released_by_user_id="user_retention_admin",
            idempotency_key="file-release-hold-001",
            request_digest=hashlib.sha256(b"file-release").hexdigest(),
            released_at=datetime.now(timezone.utc),
            audit_event=_audit(action="legal_hold.released", hold_id=record.id),
        )
        assert released is not None
        assert released.hold.revision == 2
        await reopened.close()

        after_release = FileRuntimeApiStore(root)
        await after_release.open()
        all_holds = await after_release.list_legal_holds(
            org_id=_ORG, include_released=True, limit=10
        )
        assert len(all_holds) == 1
        assert all_holds[0].released_at is not None
        deleted = await after_release.soft_delete_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            now=datetime.now(timezone.utc),
        )
        assert deleted is not None
        assert deleted.deleted_at is not None
        await after_release.close()
