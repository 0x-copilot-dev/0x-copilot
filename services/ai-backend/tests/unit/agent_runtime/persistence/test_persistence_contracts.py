from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.persistence import (
    AuditActorType,
    AuditLogRecord,
    AuditOutcome,
    CheckpointRecord,
    ContextPayloadRecord,
    MemoryItemRecord,
    MemoryScopeRecord,
    OutboxEventRecord,
    PayloadKind,
    PayloadStorageBackend,
    RuntimeMemoryScopeType,
    ToolInvocationRecord,
)


class PersistenceContractsTestMixin:
    class Values:
        ORG_ID = "org_123"
        USER_ID = "user_123"
        RUN_ID = "run_123"
        SCOPE_ID = "scope_123"
        THREAD_ID = "thread_123"
        TOOL_NAME = "doc_search"
        TOKEN = "secret-token"
        SHA256 = "a" * 64

    def memory_scope(self) -> MemoryScopeRecord:
        return MemoryScopeRecord(
            scope_id=self.Values.SCOPE_ID,
            org_id=self.Values.ORG_ID,
            user_id=self.Values.USER_ID,
            scope_type=RuntimeMemoryScopeType.USER,
            namespace_hash=self.Values.SHA256,
            namespace={"token": self.Values.TOKEN, "path": "preferences"},
        )


class TestPersistenceContracts(PersistenceContractsTestMixin):
    def test_prd_three_records_validate_and_redact_json_payloads(self) -> None:
        outbox = OutboxEventRecord(
            aggregate_type="agent_run",
            aggregate_id=self.Values.RUN_ID,
            org_id=self.Values.ORG_ID,
            event_type="run_requested",
            payload={"token": self.Values.TOKEN, "run_id": self.Values.RUN_ID},
        )
        scope = self.memory_scope()
        memory_item = MemoryItemRecord(
            scope_id=scope.scope_id,
            org_id=self.Values.ORG_ID,
            path="/memories/preferences.md",
            content_ref="object://runtime/memory/preferences",
            checksum=self.Values.SHA256,
        )
        payload = ContextPayloadRecord(
            run_id=self.Values.RUN_ID,
            org_id=self.Values.ORG_ID,
            kind=PayloadKind.TOOL_RESULT,
            storage_backend=PayloadStorageBackend.OBJECT_STORAGE,
            storage_uri="s3://runtime-payloads/tool-output",
            sha256=self.Values.SHA256,
            byte_size=42,
            retention_until=datetime.now(UTC) + timedelta(days=7),
        )
        tool = ToolInvocationRecord(
            run_id=self.Values.RUN_ID,
            org_id=self.Values.ORG_ID,
            tool_name=self.Values.TOOL_NAME,
            args={"query": "launch risks", "authorization": self.Values.TOKEN},
        )
        audit = AuditLogRecord(
            org_id=self.Values.ORG_ID,
            user_id=self.Values.USER_ID,
            actor_type=AuditActorType.USER,
            action="runtime.run.created",
            resource_type="agent_run",
            resource_id=self.Values.RUN_ID,
            run_id=self.Values.RUN_ID,
            outcome=AuditOutcome.SUCCESS,
            metadata={"token": self.Values.TOKEN},
        )
        checkpoint = CheckpointRecord(
            org_id=self.Values.ORG_ID,
            thread_id=self.Values.THREAD_ID,
            checkpoint_namespace="supervisor",
            checkpoint_version=1,
            checkpoint_blob_ref="object://checkpoints/thread_123/1",
        )

        assert outbox.payload["token"] == "[redacted]"
        assert scope.namespace["token"] == "[redacted]"
        assert memory_item.version == 1
        assert payload.redaction_state == "offloaded"
        assert tool.args["authorization"] == "[redacted]"
        assert audit.metadata["token"] == "[redacted]"
        assert checkpoint.checkpoint_namespace == "supervisor"

    def test_hash_validation_and_extra_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryScopeRecord(
                org_id=self.Values.ORG_ID,
                scope_type=RuntimeMemoryScopeType.USER,
                namespace_hash="not-a-sha",
                namespace={},
            )

        with pytest.raises(ValidationError):
            OutboxEventRecord(
                aggregate_type="agent_run",
                aggregate_id=self.Values.RUN_ID,
                org_id=self.Values.ORG_ID,
                event_type="run_requested",
                unexpected=True,
            )
