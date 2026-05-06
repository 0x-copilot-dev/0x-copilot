"""Unit tests for the PR 6.2 ``MessageCopyPlanner``.

The helper is pure: takes a sequence of source messages + a target
conversation id and emits ready-to-insert copy records with new ids,
parent_message_id rewritten via the id_map, run_id / source_message_id
/ branch_id reset to NULL, and original-row pointers stamped into
metadata.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from agent_runtime.persistence.message_copy import (
    CopiedMessages,
    ForkOrphanWarning,
    MessageCopyPlanner,
)
from runtime_api.schemas import MessageRecord
from runtime_api.schemas.common import MessageRole, MessageStatus


class _FixtureMixin:
    class Values:
        SRC_ORG = "org_src"
        TARGET_ORG = "org_src"  # cross-org forks are out of scope (PR 6.2 §1.3)
        SRC_CONV = "conv_src"
        TARGET_CONV = "conv_target"
        TARGET_USER = "user_recipient"

    @classmethod
    def make_record(
        cls,
        *,
        message_id: str,
        role: MessageRole = MessageRole.USER,
        content: str = "hello",
        parent_message_id: str | None = None,
        run_id: str | None = "run_src",
        source_message_id: str | None = None,
        branch_id: str | None = None,
        offset_seconds: int = 0,
        metadata: dict | None = None,
    ) -> MessageRecord:
        return MessageRecord(
            message_id=message_id,
            conversation_id=cls.Values.SRC_CONV,
            org_id=cls.Values.SRC_ORG,
            run_id=run_id,
            role=role,
            content_text=content,
            parent_message_id=parent_message_id,
            source_message_id=source_message_id,
            branch_id=branch_id,
            status=MessageStatus.CREATED,
            created_at=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)
            + timedelta(seconds=offset_seconds),
            metadata=metadata or {},
        )

    @classmethod
    def now(cls) -> datetime:
        return datetime(2026, 5, 5, 18, 30, tzinfo=timezone.utc)


class TestEmptyAndSingle(_FixtureMixin):
    def test_empty_source_returns_empty_copy(self) -> None:
        result = MessageCopyPlanner.plan(
            source_messages=(),
            target_conversation_id=self.Values.TARGET_CONV,
            target_org_id=self.Values.TARGET_ORG,
            now=self.now(),
        )
        assert isinstance(result, CopiedMessages)
        assert result.records == ()
        assert result.orphan_warnings == ()
        assert result.id_map == {}

    def test_single_message_resets_run_and_branch_pointers(self) -> None:
        source = self.make_record(
            message_id="m1",
            run_id="run_old",
            source_message_id="m_other",
            branch_id="branch_alpha",
        )
        result = MessageCopyPlanner.plan(
            source_messages=(source,),
            target_conversation_id=self.Values.TARGET_CONV,
            target_org_id=self.Values.TARGET_ORG,
            now=self.now(),
        )
        assert len(result) == 1
        copy = result.records[0]
        assert copy.message_id != "m1"
        assert copy.message_id == result.id_map["m1"]
        assert copy.conversation_id == self.Values.TARGET_CONV
        assert copy.org_id == self.Values.TARGET_ORG
        assert copy.run_id is None
        assert copy.source_message_id is None
        assert copy.branch_id is None
        assert copy.parent_message_id is None
        # created_at is the fork moment so retention sees the fork's age.
        assert copy.created_at == self.now()
        # original_* metadata pointers preserved for forensic reads.
        assert copy.metadata["original_message_id"] == "m1"
        assert copy.metadata["original_conversation_id"] == self.Values.SRC_CONV
        assert copy.metadata["original_created_at"].startswith("2026-05-05T12:00")


class TestParentRewrite(_FixtureMixin):
    def test_linear_chain_parents_resolve_within_new_ids(self) -> None:
        m1 = self.make_record(message_id="m1", offset_seconds=0)
        m2 = self.make_record(
            message_id="m2",
            parent_message_id="m1",
            offset_seconds=1,
            role=MessageRole.ASSISTANT,
        )
        m3 = self.make_record(
            message_id="m3",
            parent_message_id="m2",
            offset_seconds=2,
        )
        result = MessageCopyPlanner.plan(
            source_messages=(m1, m2, m3),
            target_conversation_id=self.Values.TARGET_CONV,
            target_org_id=self.Values.TARGET_ORG,
            now=self.now(),
        )
        assert len(result) == 3
        new_ids = [record.message_id for record in result.records]
        assert len(set(new_ids)) == 3, "every copy gets a new id"
        # Parents threaded through the id_map.
        assert result.records[0].parent_message_id is None
        assert result.records[1].parent_message_id == new_ids[0]
        assert result.records[2].parent_message_id == new_ids[1]
        assert result.orphan_warnings == ()

    def test_orphan_parent_yields_warning_and_nulls_pointer(self) -> None:
        # m1 references m_missing which isn't in the snapshot set; the
        # planner must null the pointer + emit one warning, rather than
        # failing the whole fork.
        orphan = self.make_record(
            message_id="m1",
            parent_message_id="m_missing",
        )
        result = MessageCopyPlanner.plan(
            source_messages=(orphan,),
            target_conversation_id=self.Values.TARGET_CONV,
            target_org_id=self.Values.TARGET_ORG,
            now=self.now(),
        )
        assert len(result) == 1
        assert result.records[0].parent_message_id is None
        assert len(result.orphan_warnings) == 1
        warning = result.orphan_warnings[0]
        assert isinstance(warning, ForkOrphanWarning)
        assert warning.source_message_id == "m1"
        assert warning.missing_parent_id == "m_missing"


class TestMetadataMerge(_FixtureMixin):
    def test_existing_metadata_preserved_alongside_audit_pointers(self) -> None:
        source = self.make_record(
            message_id="m1",
            metadata={"existing_key": "value", "trace": "abc"},
        )
        result = MessageCopyPlanner.plan(
            source_messages=(source,),
            target_conversation_id=self.Values.TARGET_CONV,
            target_org_id=self.Values.TARGET_ORG,
            now=self.now(),
        )
        copy = result.records[0]
        assert copy.metadata["existing_key"] == "value"
        assert copy.metadata["trace"] == "abc"
        assert copy.metadata["original_message_id"] == "m1"
