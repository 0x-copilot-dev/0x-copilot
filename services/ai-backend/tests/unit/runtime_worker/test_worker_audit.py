"""Tests for the worker-side audit emitter.

Covers the contract from runtime_worker/audit.py: every emit method writes
one chained audit record with the right ``event_type``, ``actor_type``,
``resource_type``, ``outcome``, and metadata. Inputs that could carry
content (LLM I/O, tool args/results) are never propagated -- we only emit
identifiers, counts, and outcome enums.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
)
from runtime_adapters.async_wrappers import adapt_persistence_to_async
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestRecord,
    RunRecord,
)
from runtime_worker.audit import WorkerAuditEmitter


class WorkerAuditEmitterMixin:
    """Build a fresh in-memory persistence + audit emitter for each test."""

    @staticmethod
    def make_emitter() -> tuple[WorkerAuditEmitter, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        emitter = WorkerAuditEmitter(persistence=adapt_persistence_to_async(store))
        return emitter, store

    @staticmethod
    def make_run(
        *, run_id: str = "run_a", org_id: str = "org_a", user_id: str = "user_u"
    ) -> RunRecord:
        return RunRecord(
            run_id=run_id,
            conversation_id="conv_a",
            org_id=org_id,
            user_id=user_id,
            user_message_id="msg_user_a",
            trace_id="trace_a",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=AgentRuntimeContext(
                org_id=org_id,
                user_id=user_id,
                roles=["employee"],
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                run_id=run_id,
                trace_id="trace_a",
            ),
        )

    @staticmethod
    def make_approval(
        *,
        approval_id: str = "ap_a",
        run_id: str = "run_a",
        org_id: str = "org_a",
        user_id: str = "user_u",
    ) -> ApprovalRequestRecord:
        return ApprovalRequestRecord(
            approval_id=approval_id,
            run_id=run_id,
            conversation_id="conv_a",
            org_id=org_id,
            user_id=user_id,
            status="pending",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metadata={},
        )


class TestRunLifecycleEmits(WorkerAuditEmitterMixin):
    def test_run_started_emits_one_chained_record(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(emitter.emit_run_started(run))

        assert len(store.audit_log) == 1
        event_type, record = store.audit_log[0]
        assert event_type == "run_started"
        assert record["actor_type"] == "worker"
        assert record["resource_type"] == "agent_run"
        assert record["resource_id"] == run.run_id
        assert record["outcome"] == "success"
        assert record["org_id"] == run.org_id
        assert record["user_id"] == run.user_id
        # Chain fields populated by the store.
        assert record["seq"] == 1
        assert record["signature"] is not None

    def test_run_completed_carries_duration_metadata(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(emitter.emit_run_completed(run, duration_ms=1234))

        _, record = store.audit_log[0]
        assert record["metadata"]["duration_ms"] == 1234
        assert record["metadata"]["status"] == "completed"
        assert record["outcome"] == "success"

    def test_run_failed_for_timeout_uses_run_timed_out_event(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(
            emitter.emit_run_failed(
                run,
                status=AgentRunStatus.TIMED_OUT,
                error_class="TimeoutError",
                error_code="tool_run_timeout",
                duration_ms=60_000,
            )
        )

        event_type, record = store.audit_log[0]
        assert event_type == "run_timed_out"
        assert record["outcome"] == "failure"
        assert record["metadata"]["status"] == "timed_out"
        assert record["metadata"]["error_class"] == "TimeoutError"
        assert record["metadata"]["error_code"] == "tool_run_timeout"

    def test_run_failed_for_exception_uses_run_failed_event(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(
            emitter.emit_run_failed(
                run,
                status=AgentRunStatus.FAILED,
                error_class="ValueError",
                error_code="tool_exception",
            )
        )

        event_type, record = store.audit_log[0]
        assert event_type == "run_failed"
        assert record["metadata"]["error_class"] == "ValueError"


class TestApprovalDecisionEmit(WorkerAuditEmitterMixin):
    def test_approved_decision_records_success_outcome(self) -> None:
        emitter, store = self.make_emitter()
        approval = self.make_approval()

        asyncio.run(
            emitter.emit_approval_decision(
                approval,
                decision=ApprovalDecision.APPROVED,
                decided_by_user_id="user_u",
            )
        )

        event_type, record = store.audit_log[0]
        assert event_type == "approval_decision"
        assert record["actor_type"] == "user"
        assert record["resource_type"] == "approval"
        assert record["resource_id"] == approval.approval_id
        assert record["outcome"] == "success"
        assert record["metadata"]["decision"] == "approved"
        assert record["metadata"]["decided_by_user_id"] == "user_u"
        assert record["metadata"]["run_id"] == approval.run_id

    def test_rejected_decision_records_denied_outcome(self) -> None:
        emitter, store = self.make_emitter()
        approval = self.make_approval()

        asyncio.run(
            emitter.emit_approval_decision(
                approval,
                decision=ApprovalDecision.REJECTED,
                decided_by_user_id=None,
            )
        )

        _, record = store.audit_log[0]
        assert record["outcome"] == "denied"
        assert record["metadata"]["decision"] == "rejected"
        assert "decided_by_user_id" not in record["metadata"]


class TestToolCallOutcomeEmit(WorkerAuditEmitterMixin):
    def test_completed_tool_call_records_success(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(
            emitter.emit_tool_call_outcome(
                run,
                tool_name="web_search",
                call_id="call_42",
                outcome="completed",
                duration_ms=750,
            )
        )

        event_type, record = store.audit_log[0]
        assert event_type == "tool_call_outcome"
        assert record["resource_type"] == "tool_call"
        assert record["resource_id"] == "call_42"
        assert record["outcome"] == "success"
        assert record["metadata"]["tool_name"] == "web_search"
        assert record["metadata"]["duration_ms"] == 750

    def test_failed_tool_call_records_failure(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(
            emitter.emit_tool_call_outcome(
                run,
                tool_name="mcp.read",
                call_id="call_99",
                outcome="failed",
                error_code="tool_exception",
            )
        )

        _, record = store.audit_log[0]
        assert record["outcome"] == "failure"
        assert record["metadata"]["error_code"] == "tool_exception"


class TestChainContinuesAcrossEmits(WorkerAuditEmitterMixin):
    def test_consecutive_emits_form_one_chain(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(emitter.emit_run_started(run))
        asyncio.run(emitter.emit_run_completed(run, duration_ms=12))

        assert len(store.audit_log) == 2
        first = store.audit_log[0][1]
        second = store.audit_log[1][1]
        assert first["seq"] == 1
        assert second["seq"] == 2
        # Second row's prev_hash is the first row's signature, hex-encoded.
        assert second["prev_hash"] == first["signature"]

    def test_audit_emit_failure_does_not_raise(self) -> None:
        """Audit emission must never break the worker; a store error is logged."""

        class _ExplodingStore:
            async def write_audit_log(
                self, *, event_type: str, record: dict[str, object]
            ) -> None:
                raise RuntimeError("disk full")

        emitter = WorkerAuditEmitter(persistence=_ExplodingStore())  # type: ignore[arg-type]
        run = self.make_run()

        # Must not propagate the underlying RuntimeError.
        asyncio.run(emitter.emit_run_completed(run, duration_ms=1))


class TestNoContentLeak(WorkerAuditEmitterMixin):
    """Audit metadata must never carry LLM I/O or tool argument content."""

    def test_metadata_keys_are_typed_safe_only(self) -> None:
        emitter, store = self.make_emitter()
        run = self.make_run()

        asyncio.run(
            emitter.emit_tool_call_outcome(
                run,
                tool_name="web_search",
                call_id="call_1",
                outcome="completed",
                duration_ms=1,
            )
        )
        _, record = store.audit_log[0]
        # Allowed keys only: tool_name, call_id, outcome, duration_ms.
        forbidden = {
            "args",
            "arguments",
            "result",
            "tool_input",
            "tool_output",
            "messages",
            "prompt",
        }
        assert not (set(record["metadata"].keys()) & forbidden)
