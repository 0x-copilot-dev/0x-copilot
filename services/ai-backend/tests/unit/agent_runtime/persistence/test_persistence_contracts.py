from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.persistence import (
    AuditActorType,
    AuditLogRecord,
    AuditOutcome,
    OutboxEventRecord,
    ToolInvocationRecord,
)


class PersistenceContractsTestMixin:
    class Values:
        ORG_ID = "org_123"
        USER_ID = "user_123"
        RUN_ID = "run_123"
        TOOL_NAME = "doc_search"
        TOKEN = "secret-token"
        SHA256 = "a" * 64


class TestPersistenceContracts(PersistenceContractsTestMixin):
    def test_runtime_persistence_records_validate_and_coerce_json_payloads(
        self,
    ) -> None:
        outbox = OutboxEventRecord(
            aggregate_type="agent_run",
            aggregate_id=self.Values.RUN_ID,
            org_id=self.Values.ORG_ID,
            event_type="run_requested",
            payload={"token": self.Values.TOKEN, "run_id": self.Values.RUN_ID},
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

        # P11.5: persistence records no longer scrub credential-shaped
        # keys at the validation boundary. Values flow through whole;
        # logs filter via their own deny-key boundary at emission time.
        assert outbox.payload["token"] == self.Values.TOKEN
        assert tool.args["authorization"] == self.Values.TOKEN
        assert audit.metadata["token"] == self.Values.TOKEN

    def test_extra_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OutboxEventRecord(
                aggregate_type="agent_run",
                aggregate_id=self.Values.RUN_ID,
                org_id=self.Values.ORG_ID,
                event_type="run_requested",
                unexpected=True,
            )
