"""Approval-handler tests for the ask_a_question HITL flow."""

from __future__ import annotations


from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestRecord,
    MessageRole,
    RuntimeApprovalResolvedCommand,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler


class _Values:
    ORG_ID = "org_456"
    USER_ID = "user_123"
    RUN_ID = "run_test_aq"
    CONVERSATION_ID = "conversation_aq"
    USER_MESSAGE_ID = "msg_user"
    APPROVAL_ID = "ask_a_question:run_test_aq:trace_aq"


async def _seed_run_and_approval(store: InMemoryRuntimeApiStore) -> None:
    from agent_runtime.execution.contracts import AgentRuntimeContext
    from runtime_api.schemas import MessageRecord, RunRecord

    await store.append_message(
        MessageRecord(
            message_id=_Values.USER_MESSAGE_ID,
            conversation_id=_Values.CONVERSATION_ID,
            org_id=_Values.ORG_ID,
            role=MessageRole.USER,
            content_text="Plan a trip",
        )
    )
    store.runs[_Values.RUN_ID] = RunRecord(
        run_id=_Values.RUN_ID,
        conversation_id=_Values.CONVERSATION_ID,
        org_id=_Values.ORG_ID,
        user_id=_Values.USER_ID,
        user_message_id=_Values.USER_MESSAGE_ID,
        trace_id="trace_aq",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_Values.USER_ID,
            org_id=_Values.ORG_ID,
            roles=["employee"],
            run_id=_Values.RUN_ID,
            trace_id="trace_aq",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )
    store.events_by_run.setdefault(_Values.RUN_ID, [])
    await store.seed_approval_request(
        ApprovalRequestRecord(
            approval_id=_Values.APPROVAL_ID,
            run_id=_Values.RUN_ID,
            conversation_id=_Values.CONVERSATION_ID,
            org_id=_Values.ORG_ID,
            user_id=_Values.USER_ID,
            metadata={
                "approval_kind": "ask_a_question",
                "native_interrupt_id": _Values.APPROVAL_ID,
                "question": "Where would you like to travel?",
            },
        )
    )


class _FakeHarness:
    pass


async def _empty_resumer(harness: object, resume: object):
    if False:
        yield {}


def _resume_capturing_resumer(captured: list[object]):
    async def _resumer(harness: object, resume: object):
        captured.append(resume)
        if False:
            yield {}

    return _resumer


class TestAskAQuestionApprovalResume:
    def test_resume_payload_includes_answer_for_ask_a_question(self) -> None:
        command = RuntimeApprovalResolvedCommand(
            approval_id=_Values.APPROVAL_ID,
            run_id=_Values.RUN_ID,
            org_id=_Values.ORG_ID,
            decision=ApprovalDecision.APPROVED,
            answer="Tokyo",
        )

        resume = RuntimeApprovalHandler._resume_payload(
            command,
            metadata={"approval_kind": "ask_a_question"},
        )

        assert resume == {
            "approval_id": _Values.APPROVAL_ID,
            "decision": "approved",
            "answer": "Tokyo",
        }

    def test_resume_payload_falls_back_to_action_shape_for_other_kinds(self) -> None:
        command = RuntimeApprovalResolvedCommand(
            approval_id="other",
            run_id=_Values.RUN_ID,
            org_id=_Values.ORG_ID,
            decision=ApprovalDecision.REJECTED,
        )

        resume = RuntimeApprovalHandler._resume_payload(
            command, metadata={"approval_kind": "mcp_tool"}
        )

        assert resume == {"decisions": [{"type": "reject"}]}

    async def test_handle_resumes_run_without_appending_user_message_on_answer(
        self,
    ) -> None:
        """The user's answer must reach the agent via the LangGraph resume value
        (and the tool's return value), NOT as a stray top-level USER message in
        the chat thread. Persisting it as a USER message used to surface a
        duplicate user-bubble in the UI disconnected from the question card."""

        store = InMemoryRuntimeApiStore()
        await _seed_run_and_approval(store)
        captured: list[object] = []
        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_resume_capturing_resumer(captured),
        )
        command = RuntimeApprovalResolvedCommand(
            approval_id=_Values.APPROVAL_ID,
            run_id=_Values.RUN_ID,
            org_id=_Values.ORG_ID,
            decision=ApprovalDecision.APPROVED,
            answer="Tokyo",
        )

        await handler.handle(command)

        run_user_messages = [
            message
            for message in store.messages.values()
            if message.role == MessageRole.USER and message.run_id == _Values.RUN_ID
        ]
        assert run_user_messages == []
        assert captured == [
            {
                "approval_id": _Values.APPROVAL_ID,
                "decision": "approved",
                "answer": "Tokyo",
            }
        ]
        assert store.runs[_Values.RUN_ID].status == AgentRunStatus.COMPLETED

    async def test_handle_skips_user_message_when_no_answer_provided(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run_and_approval(store)
        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_resume_capturing_resumer([]),
        )
        command = RuntimeApprovalResolvedCommand(
            approval_id=_Values.APPROVAL_ID,
            run_id=_Values.RUN_ID,
            org_id=_Values.ORG_ID,
            decision=ApprovalDecision.REJECTED,
            answer=None,
        )

        await handler.handle(command)

        user_messages = [
            message
            for message in store.messages.values()
            if message.role == MessageRole.USER and message.run_id == _Values.RUN_ID
        ]
        assert user_messages == []
