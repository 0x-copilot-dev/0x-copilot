"""Bug R1: the approval-resume path offloads and composes read routes.

Before the fix, ``RuntimeApprovalHandler`` built its ``StreamOrchestrator`` with
no ``tool_result_offloader`` and its resume ``dependencies`` from the bare
factory. On the desktop file store that meant, after an approval:

* a large tool result was persisted **inline** in ``events.jsonl`` instead of
  offloaded to the object store; and
* a ``/large_tool_results/<sha>`` or ``/subagents/<task>/…`` reference produced
  **before** the pause was unreadable after resume (no composed route).

These tests pin the corrected wiring, and assert non-file backends are
unchanged (offloader ``None`` → inline; no file-only read routes).
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENAI_API_KEY", "sk-test-resume-offload")

from agent_runtime.api.constants import Keys
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.execution.factory import _composed_deep_backend
from runtime_adapters.file import FileOffloadWriter, FileRuntimeApiStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalRequestRecord,
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventRedactionState,
)
from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.stream_parts import StreamNamespace


_ORG_ID = "org_resume"
_USER_ID = "user_resume"
_CONVERSATION_ID = "conv_resume"
_RUN_ID = "run_resume"
_USER_MESSAGE_ID = "msg_user_resume"
_APPROVAL_ID = "appr_resume"
_TASK = "task-resume"


_LINE = "SEARCH RESULT LINE"
_COUNT = 5_000


def _large_output() -> str:
    # Comfortably over the offloader's ~8k-token (~32k-char) inline budget.
    return f"{_LINE}\n" * _COUNT


class _FakeHarness:
    pass


async def _empty_resumer(_: object, __: object):
    if False:
        yield {}


async def _seed_run_and_approval(store) -> RunRecord:
    await store.append_message(
        MessageRecord(
            message_id=_USER_MESSAGE_ID,
            conversation_id=_CONVERSATION_ID,
            org_id=_ORG_ID,
            role=MessageRole.USER,
            content_text="Run the MCP call.",
        )
    )
    run = RunRecord(
        run_id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        org_id=_ORG_ID,
        user_id=_USER_ID,
        user_message_id=_USER_MESSAGE_ID,
        trace_id="trace_resume",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        runtime_context=AgentRuntimeContext(
            user_id=_USER_ID,
            org_id=_ORG_ID,
            roles=["employee"],
            run_id=_RUN_ID,
            trace_id="trace_resume",
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
    store.runs[_RUN_ID] = run
    store.events_by_run.setdefault(_RUN_ID, [])
    await store.seed_approval_request(
        ApprovalRequestRecord(
            approval_id=_APPROVAL_ID,
            run_id=_RUN_ID,
            conversation_id=_CONVERSATION_ID,
            org_id=_ORG_ID,
            user_id=_USER_ID,
            metadata={
                "approval_kind": "action",
                "native_interrupt_id": _APPROVAL_ID,
                "tool_name": "mcp_call",
            },
        )
    )
    await store.insert_approval_batch(
        spec=ApprovalBatchSpec.build(
            batch=ApprovalBatchRecord(
                batch_id=_APPROVAL_ID, run_id=_RUN_ID, org_id=_ORG_ID
            ),
            items=[
                ApprovalBatchItemRecord(
                    item_id=_APPROVAL_ID, batch_id=_APPROVAL_ID, index=0
                ),
            ],
        )
    )
    return run


class TestApprovalResumeOffloadsLargeToolResult:
    async def test_large_tool_result_after_approval_offloads_to_object(
        self, tmp_path
    ) -> None:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        run = await _seed_run_and_approval(store)

        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_empty_resumer,
        )

        # The handler's orchestrator is what the resumed stream drives; on the
        # file store it must carry an offloader (the bug: it was ``None``).
        offloader = handler.stream_event_mapper.message_processor._tool_result_offloader
        assert offloader is not None

        # Drive a large tool result through the resume orchestrator, exactly as
        # the resumed LangGraph stream would (mirrors test_stream_events).
        namespace = StreamNamespace.from_value(())
        await (
            handler.stream_event_mapper.message_processor.append_tool_call_chunk_event(
                run=run,
                namespace=namespace,
                tool_call={
                    "name": "mcp_call",
                    "id": "call_big",
                    "index": 0,
                    "args": {"q": "x"},
                },
                metadata={},
                parent_task_id=None,
            )
        )
        content = _large_output()
        await handler.stream_event_mapper.message_processor.process(
            run=run,
            namespace=namespace,
            message={
                "type": "tool",
                "name": "mcp_call",
                "tool_call_id": "call_big",
                "content": content,
                "status": "success",
            },
            delta=None,
        )

        tool_results = [
            e
            for e in store.events_by_run[_RUN_ID]
            if e.event_type is RuntimeApiEventType.TOOL_RESULT
        ]
        assert len(tool_results) == 1
        result = tool_results[0]
        payload = dict(result.payload)
        # Offloaded to an object: the event carries a `/large_tool_results/` ref
        # and is flagged OFFLOADED. Before the fix the resume orchestrator had no
        # offloader, so this event had no ref at all — the full result was inline.
        ref = payload[Keys.Field.OUTPUT_REF]
        assert ref.startswith("/large_tool_results/")
        assert result.redaction_state is RuntimeEventRedactionState.OFFLOADED
        # The full content is durable in the content-addressed object store.
        sha = ref.removeprefix("/large_tool_results/")
        blob = store.object_store.get(sha).decode("utf-8")
        assert blob.count(_LINE) == _COUNT
        await store.close()

    async def test_non_file_store_resume_keeps_inline_behavior(self) -> None:
        store = InMemoryRuntimeApiStore()
        run = await _seed_run_and_approval(store)
        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_empty_resumer,
        )
        # Non-file backend: offloader stays None → inline, byte-identical.
        assert (
            handler.stream_event_mapper.message_processor._tool_result_offloader is None
        )

        # Driving the same large result leaves it fully inline (no `output_ref`).
        namespace = StreamNamespace.from_value(())
        await (
            handler.stream_event_mapper.message_processor.append_tool_call_chunk_event(
                run=run,
                namespace=namespace,
                tool_call={
                    "name": "mcp_call",
                    "id": "call_big",
                    "index": 0,
                    "args": {},
                },
                metadata={},
                parent_task_id=None,
            )
        )
        await handler.stream_event_mapper.message_processor.process(
            run=run,
            namespace=namespace,
            message={
                "type": "tool",
                "name": "mcp_call",
                "tool_call_id": "call_big",
                "content": _large_output(),
                "status": "success",
            },
            delta=None,
        )
        tool_result = next(
            e
            for e in store.events_by_run[_RUN_ID]
            if e.event_type is RuntimeApiEventType.TOOL_RESULT
        )
        assert Keys.Field.OUTPUT_REF not in dict(tool_result.payload)


class TestApprovalResumeComposesReadBackends:
    async def test_prepause_refs_readable_through_composed_backend_after_resume(
        self, tmp_path
    ) -> None:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        run = await _seed_run_and_approval(store)

        # A subagent trace + an offloaded large result committed BEFORE the pause.
        for draft in (
            RuntimeEventDraft(
                org_id=_ORG_ID,
                run_id=_RUN_ID,
                conversation_id=_CONVERSATION_ID,
                trace_id="trace_resume",
                source=StreamEventSource.SUBAGENT,
                event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                task_id=_TASK,
                payload={"task_id": _TASK, "subagent_name": "researcher"},
            ),
            RuntimeEventDraft(
                org_id=_ORG_ID,
                run_id=_RUN_ID,
                conversation_id=_CONVERSATION_ID,
                trace_id="trace_resume",
                source=StreamEventSource.SUBAGENT,
                event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                task_id=_TASK,
                payload={
                    "task_id": _TASK,
                    "subagent_name": "researcher",
                    "summary": "Deep agents is an agent harness.",
                    "status": "completed",
                },
            ),
        ):
            await store.append_event(draft)
        reference = FileOffloadWriter(store.object_store)(_large_output())

        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_empty_resumer,
        )
        # Build the exact dependencies the resume path threads into the factory.
        dependencies = handler._dependencies_for_resume(run)
        assert dependencies.subagent_artifacts_backend is not None
        assert dependencies.large_tool_results_backend is not None

        # Compose them exactly like ``acreate_agent_runtime`` does and read back.
        composite = _composed_deep_backend(
            dependencies.subagent_artifacts_backend,
            drafts_backend=dependencies.drafts_backend,
            large_tool_results_backend=dependencies.large_tool_results_backend,
        )

        subagent_read = await composite.aread(f"/subagents/{_TASK}/summary.md")
        assert subagent_read.error is None
        assert "Deep agents is an agent harness." in subagent_read.file_data["content"]

        blob_read = await composite.aread(reference)
        assert blob_read.error is None
        assert blob_read.file_data["content"] == _large_output()
        await store.close()

    async def test_non_file_store_resume_composes_no_file_read_routes(self) -> None:
        store = InMemoryRuntimeApiStore()
        run = await _seed_run_and_approval(store)
        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_empty_resumer,
        )
        dependencies = handler._dependencies_for_resume(run)
        # File-only read backends stay unrouted on non-file backends.
        assert dependencies.subagent_artifacts_backend is None
        assert dependencies.large_tool_results_backend is None


class TestApprovalResumeEndToEndOffloadThenRead:
    """The full loop: offload after approval, then read the ref back through the
    composed backend the same handler builds — proving the two halves agree."""

    async def test_offloaded_ref_is_readable_through_resume_backends(
        self, tmp_path
    ) -> None:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        run = await _seed_run_and_approval(store)
        handler = RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=lambda **_: _FakeHarness(),
            runtime_resumer=_empty_resumer,
        )
        namespace = StreamNamespace.from_value(())
        await (
            handler.stream_event_mapper.message_processor.append_tool_call_chunk_event(
                run=run,
                namespace=namespace,
                tool_call={
                    "name": "mcp_call",
                    "id": "call_e2e",
                    "index": 0,
                    "args": {},
                },
                metadata={},
                parent_task_id=None,
            )
        )
        await handler.stream_event_mapper.message_processor.process(
            run=run,
            namespace=namespace,
            message={
                "type": "tool",
                "name": "mcp_call",
                "tool_call_id": "call_e2e",
                "content": _large_output(),
                "status": "success",
            },
            delta=None,
        )
        tool_result = next(
            e
            for e in store.events_by_run[_RUN_ID]
            if e.event_type is RuntimeApiEventType.TOOL_RESULT
        )
        ref = dict(tool_result.payload)[Keys.Field.OUTPUT_REF]

        dependencies = handler._dependencies_for_resume(run)
        composite = _composed_deep_backend(
            dependencies.subagent_artifacts_backend,
            drafts_backend=dependencies.drafts_backend,
            large_tool_results_backend=dependencies.large_tool_results_backend,
        )
        blob_read = await composite.aread(ref)
        assert blob_read.error is None
        # The offloaded result produced after the approval is fully readable
        # back through the composed backend the resume path builds.
        assert blob_read.file_data["content"].count(_LINE) == _COUNT
        await store.close()
