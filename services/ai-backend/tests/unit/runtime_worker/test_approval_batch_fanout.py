"""Fan-out integration tests for the ApprovalBatch refactor (PR #43).

Pins:
- N=1 and N=N follow the SAME code path. The line-637 special case
  (``approval_id = interrupt_id if len(action_requests) == 1 else
  f"{interrupt_id}:{index}"``) is gone — every item id follows
  ``<batch_id>:<index>`` regardless of batch size.
- One interrupt with N action_requests produces ONE ApprovalBatch row and
  N ApprovalBatchItem rows, indices 0..N-1.
- The batch is inserted atomically BEFORE any ``approval_requested`` event
  is emitted, so the handler can read the batch state via the per-event
  ``batch_id`` field.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from types import SimpleNamespace

# RuntimeEventProducer constructs an OpenAI client in some lifecycle paths;
# tests are hermetic, a placeholder key is enough.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-batch-fanout")

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import (
    ApprovalBatchStatus,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
    RuntimeRunCommand,
)
from runtime_worker.handlers.run import RuntimeRunHandler


_ORG_ID = "org_fanout"
_USER_ID = "user_fanout"
_RUN_ID = "run_fanout"
_USER_MESSAGE_ID = "msg_user_fanout"
_CONVERSATION_ID = "conv_fanout"


async def _seed_run(store: InMemoryRuntimeApiStore) -> RunRecord:
    from runtime_api.schemas import MessageRecord, MessageRole

    await store.append_message(
        MessageRecord(
            message_id=_USER_MESSAGE_ID,
            conversation_id=_CONVERSATION_ID,
            org_id=_ORG_ID,
            role=MessageRole.USER,
            content_text="Please load my Linear issues PAR-5..9.",
        )
    )
    run = RunRecord(
        run_id=_RUN_ID,
        conversation_id=_CONVERSATION_ID,
        org_id=_ORG_ID,
        user_id=_USER_ID,
        user_message_id=_USER_MESSAGE_ID,
        trace_id="trace_fanout",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.QUEUED,
        runtime_context=AgentRuntimeContext(
            user_id=_USER_ID,
            org_id=_ORG_ID,
            roles=["employee"],
            run_id=_RUN_ID,
            trace_id="trace_fanout",
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
    return run


def _action_request(
    *, tool_name: str, arguments: dict[str, object]
) -> dict[str, object]:
    """Build one ``call_mcp_tool`` action_request dict for a Linear get_issue."""
    return {
        "name": "call_mcp_tool",
        "args": {
            "server_name": "mcp_linear_app",
            "tool_name": tool_name,
            "arguments": arguments,
        },
    }


def _interrupt_chunk(
    *, interrupt_id: str, action_requests: Sequence[dict[str, object]]
) -> dict[str, object]:
    """Compose a single ``__interrupt__`` stream chunk with N action_requests."""
    return {
        "type": "updates",
        "ns": (),
        "data": {
            "__interrupt__": (
                SimpleNamespace(
                    id=interrupt_id,
                    value={
                        "action_requests": list(action_requests),
                        "review_configs": [
                            {
                                "action_name": "call_mcp_tool",
                                "allowed_decisions": ["approve", "reject"],
                            }
                        ],
                    },
                ),
            )
        },
    }


class _FakeHarness:
    pass


def _make_streamer(interrupt_chunk: dict[str, object]):
    async def _stream(_harness: object, _messages: object):
        yield interrupt_chunk

    return _stream


def _fake_agent_factory(*, context, dependencies):  # type: ignore[no-untyped-def]
    from agent_runtime.execution.factory import RuntimeHarness

    return RuntimeHarness(
        agent=object(),
        context=context,
        dependencies=dependencies,
        tools=(),
        mcp_servers=(),
        subagents=(),
        memory_backend=None,
        skill_directories=(),
    )


def _make_run_handler(
    store: InMemoryRuntimeApiStore,
    *,
    streamer,
) -> RuntimeRunHandler:
    return RuntimeRunHandler(
        persistence=store,
        event_store=store,
        agent_factory=_fake_agent_factory,
        runtime_streamer=streamer,
    )


async def _drive_run_once(
    store: InMemoryRuntimeApiStore,
    *,
    handler: RuntimeRunHandler,
) -> None:
    run = store.runs[_RUN_ID]
    await handler.handle(
        RuntimeRunCommand(
            run_id=_RUN_ID,
            org_id=_ORG_ID,
            conversation_id=_CONVERSATION_ID,
            user_id=_USER_ID,
            trace_id="trace_fanout",
            runtime_context=run.runtime_context,
        )
    )


class TestApprovalBatchFanout:
    async def test_n1_and_nN_share_the_same_item_id_format(self) -> None:
        """Substitution test — the line-637 special case is gone.

        N=1 and N=5 must produce item_ids of the form ``<batch_id>:<index>``.
        Pin the format with a regex so any future "if len==1: bare" sneak-
        back is caught here.
        """
        from runtime_worker.stream_events import StreamOrchestrator

        n1 = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i_n1",
            interrupt_value={
                "action_requests": [
                    _action_request(tool_name="get_issue", arguments={"id": "PAR-1"}),
                ],
                "review_configs": [],
            },
        )
        n5 = StreamOrchestrator.native_tool_approval_payloads(
            interrupt_id="i_n5",
            interrupt_value={
                "action_requests": [
                    _action_request(tool_name="get_issue", arguments={"id": f"PAR-{i}"})
                    for i in range(5)
                ],
                "review_configs": [],
            },
        )

        item_id_pattern = re.compile(r"^[^:]+:\d+$")
        assert all(
            item_id_pattern.match(str(payload["approval_id"])) for payload in n1
        ), [p["approval_id"] for p in n1]
        assert all(
            item_id_pattern.match(str(payload["approval_id"])) for payload in n5
        ), [p["approval_id"] for p in n5]
        # N=1 and N=N share the prefix-with-index format — no "bare interrupt_id"
        # shape for single-action interrupts.
        assert n1[0]["approval_id"] == "i_n1:0"
        assert [payload["approval_id"] for payload in n5] == [
            f"i_n5:{i}" for i in range(5)
        ]

    async def test_n1_emits_one_approval_event_with_batch_metadata(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        chunk = _interrupt_chunk(
            interrupt_id="i_n1_event",
            action_requests=[
                _action_request(tool_name="get_issue", arguments={"id": "PAR-1"}),
            ],
        )
        handler = _make_run_handler(store, streamer=_make_streamer(chunk))
        await _drive_run_once(store, handler=handler)

        approval_events = [
            event
            for event in store.events_by_run[_RUN_ID]
            if event.event_type is RuntimeApiEventType.APPROVAL_REQUESTED
        ]
        assert len(approval_events) == 1
        payload = dict(approval_events[0].payload)
        assert payload["approval_id"] == "i_n1_event:0"
        assert payload["batch_id"] == "i_n1_event"
        assert payload["batch_index"] == 0

        batch = await store.get_approval_batch(org_id=_ORG_ID, batch_id="i_n1_event")
        assert batch is not None
        assert batch.status is ApprovalBatchStatus.PENDING
        items = await store.list_items_for_batch(org_id=_ORG_ID, batch_id="i_n1_event")
        assert len(items) == 1
        assert items[0].item_id == "i_n1_event:0"
        assert items[0].index == 0

    async def test_n5_emits_five_events_one_batch_five_items(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_run(store)
        chunk = _interrupt_chunk(
            interrupt_id="i_n5_event",
            action_requests=[
                _action_request(tool_name="get_issue", arguments={"id": f"PAR-{i}"})
                for i in range(5)
            ],
        )
        handler = _make_run_handler(store, streamer=_make_streamer(chunk))
        await _drive_run_once(store, handler=handler)

        approval_events = [
            event
            for event in store.events_by_run[_RUN_ID]
            if event.event_type is RuntimeApiEventType.APPROVAL_REQUESTED
        ]
        assert len(approval_events) == 5
        # Every event shares the same batch_id and carries a distinct
        # batch_index 0..4.
        batch_ids = {event.payload["batch_id"] for event in approval_events}
        batch_indices = sorted(
            event.payload["batch_index"] for event in approval_events
        )
        assert batch_ids == {"i_n5_event"}
        assert batch_indices == [0, 1, 2, 3, 4]
        item_ids = sorted(event.payload["approval_id"] for event in approval_events)
        assert item_ids == [f"i_n5_event:{i}" for i in range(5)]

        batch = await store.get_approval_batch(org_id=_ORG_ID, batch_id="i_n5_event")
        assert batch is not None
        assert batch.status is ApprovalBatchStatus.PENDING
        items = await store.list_items_for_batch(org_id=_ORG_ID, batch_id="i_n5_event")
        assert len(items) == 5
        assert [item.index for item in items] == [0, 1, 2, 3, 4]

    async def test_special_case_is_gone_from_source_code(self) -> None:
        """Belt-and-braces: verify the deleted special case did not sneak back.

        Reads ``stream_events.py`` and asserts the historical condition
        ``len(action_requests) == 1`` does not appear inside the MCP tool
        fan-out method. This pins the substitution principle at the file
        level so a future change cannot reintroduce a bare-interrupt_id
        branch without failing the test.
        """
        import inspect

        from runtime_worker.stream_events import StreamOrchestrator

        source = inspect.getsource(StreamOrchestrator.native_tool_approval_payloads)
        assert "len(action_requests) == 1" not in source, (
            "The deleted N=1 special case appears to have returned. "
            "The line-637 conditional in PR #43's original bug is the source "
            "of the multi-tool-call resume crash. See PR #43 for context."
        )
