"""Approval-handler tests for the ask_a_question HITL flow."""

from __future__ import annotations


from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
)
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
    # PR #43 — ask_a_question is a single-action interrupt; seed its 1-item
    # batch so the handler's atomic transition gate completes on the only
    # item and the resume proceeds.
    await store.insert_approval_batch(
        spec=ApprovalBatchSpec.build(
            batch=ApprovalBatchRecord(
                batch_id=_Values.APPROVAL_ID,
                run_id=_Values.RUN_ID,
                org_id=_Values.ORG_ID,
            ),
            items=[
                ApprovalBatchItemRecord(
                    item_id=_Values.APPROVAL_ID,
                    batch_id=_Values.APPROVAL_ID,
                    index=0,
                ),
            ],
        )
    )


class _FakeHarness:
    pass


async def _empty_resumer(
    harness: object, resume: object, *, interrupt_id: str | None = None
):
    if False:
        yield {}


def _resume_capturing_resumer(captured: list[object]):
    async def _resumer(
        harness: object, resume: object, *, interrupt_id: str | None = None
    ):
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

    def test_resume_payload_carries_the_decision_scope_when_one_was_sent(self) -> None:
        """The hop that makes ``always`` reachable inside the graph.

        A parked WRITE borrows this ``ask_a_question`` shape, and this dict is
        the LangGraph resume value ``ToolAccessGate._interpret_resume`` reads
        back as ``GateResume.decision_scope``. Without the key the policy lane
        that raised the gate has no way to learn which scope the user picked.
        """

        command = RuntimeApprovalResolvedCommand(
            approval_id=_Values.APPROVAL_ID,
            run_id=_Values.RUN_ID,
            org_id=_Values.ORG_ID,
            decision=ApprovalDecision.APPROVED,
            decision_scope="always",
        )

        resume = RuntimeApprovalHandler._resume_payload(
            command, metadata={"approval_kind": "ask_a_question"}
        )

        assert resume == {
            "approval_id": _Values.APPROVAL_ID,
            "decision": "approved",
            "answer": None,
            "decision_scope": "always",
        }

    def test_resume_payload_omits_the_scope_key_when_none_was_sent(self) -> None:
        """This value is persisted in the checkpoint, so an unconditional
        ``decision_scope: None`` would rewrite the stored shape of every plain
        ask-a-question resume to say something the caller never said. Absent and
        ``None`` are the same answer to the only reader (``.get``)."""

        command = RuntimeApprovalResolvedCommand(
            approval_id=_Values.APPROVAL_ID,
            run_id=_Values.RUN_ID,
            org_id=_Values.ORG_ID,
            decision=ApprovalDecision.APPROVED,
            answer="Tokyo",
        )

        resume = RuntimeApprovalHandler._resume_payload(
            command, metadata={"approval_kind": "ask_a_question"}
        )

        assert "decision_scope" not in resume

    def test_a_command_written_before_the_field_existed_still_deserializes(
        self,
    ) -> None:
        """The durable queue contract: ``decision_scope`` is optional and typed
        as a plain string, so a command enqueued by an older API is readable."""

        command = RuntimeApprovalResolvedCommand.model_validate(
            {
                "approval_id": _Values.APPROVAL_ID,
                "run_id": _Values.RUN_ID,
                "org_id": _Values.ORG_ID,
                "decision": "approved",
            }
        )

        assert command.decision_scope is None

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

    def test_every_other_lane_DROPS_the_scope_even_when_one_was_sent(self) -> None:
        """The fact the client's once/always control is scoped to.

        Two lanes advertise ``grant_options: [..., "allow_always"]``, and they
        mean different things by it. The FILESYSTEM lane
        (``runtime_worker/stream_events.py:227-234``) means "attach this folder"
        -- a durable workspace grant, wider than the path the card named, which
        the OS-dialog + ``WorkspaceGrantPort`` flow settles. Nothing on the
        ``/decision`` path performs it, and this test is why: the resume builder
        forwards ``decision_scope`` on the ``ask_a_question`` shape ALONE, so a
        scope sent with any other kind is silently dropped on the floor.

        The chat surface therefore renders its run-scoped "always" only where
        that forwarding happens (``allowsRunScopedGrant``). If a future change
        makes another lane honour the scope, this test fails first -- which is
        the signal to widen the client predicate, rather than discovering a live
        control that has quietly been doing nothing.
        """

        for approval_kind in ("filesystem_access", "mcp_tool", "mcp_auth"):
            command = RuntimeApprovalResolvedCommand(
                approval_id=f"appr_{approval_kind}",
                run_id=_Values.RUN_ID,
                org_id=_Values.ORG_ID,
                decision=ApprovalDecision.APPROVED,
                decision_scope="always",
            )

            resume = RuntimeApprovalHandler._resume_payload(
                command, metadata={"approval_kind": approval_kind}
            )

            assert "decision_scope" not in resume, approval_kind

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
