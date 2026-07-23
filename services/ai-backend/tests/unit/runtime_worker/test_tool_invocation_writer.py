"""PRD-08 D1b — the tool-invocation writer at the TOOL_CALL_STARTED seam.

A completed MCP tool call writes ONE ``runtime_tool_invocations`` row whose
``connector_slug`` is the resolved MCP server slug; a native (connector-less)
tool call writes one row with ``connector_slug = None`` (a step, not an app). The
write is best-effort — a persistence error must never propagate into the run.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_api.schemas import RunRecord
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.stream_tools import StreamMessageProcessor


class _CapturingPersistence:
    def __init__(self) -> None:
        self.records: list[object] = []

    async def record_tool_invocation(self, record: object) -> None:
        self.records.append(record)


class _RaisingPersistence:
    async def record_tool_invocation(self, record: object) -> None:
        raise RuntimeError("ledger down")


class _RecordingEventProducer:
    def __init__(self, persistence: object) -> None:
        self.events: list[dict[str, object]] = []
        self.persistence = persistence

    async def append_api_event(self, **kwargs: object) -> None:
        self.events.append(kwargs)


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run_ti",
        conversation_id="conv_ti",
        org_id="org_ti",
        user_id="user_ti",
        user_message_id="msg_ti",
        trace_id="trace_ti",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_ti",
            org_id="org_ti",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run_ti",
            trace_id="trace_ti",
        ),
    )


def _processor(persistence: object) -> StreamMessageProcessor:
    producer = _RecordingEventProducer(persistence)
    return StreamMessageProcessor(producer, StreamUpdateProcessor(producer))


async def test_mcp_tool_call_writes_row_with_resolved_connector_slug() -> None:
    persistence = _CapturingPersistence()
    processor = _processor(persistence)
    await processor.append_tool_call_chunk_event(
        run=_run_record(),
        namespace=StreamNamespace.from_value(()),
        tool_call={
            "name": "call_mcp_tool",
            "id": "call_mcp_1",
            "args": {"server_name": "github", "tool_name": "create_issue"},
        },
        metadata={},
        parent_task_id=None,
    )
    assert len(persistence.records) == 1
    record = persistence.records[0]
    assert record.run_id == "run_ti"
    assert record.org_id == "org_ti"
    assert record.call_id == "call_mcp_1"
    assert record.connector_slug == "github"


async def test_native_tool_call_writes_row_with_null_connector_slug() -> None:
    persistence = _CapturingPersistence()
    processor = _processor(persistence)
    await processor.append_tool_call_chunk_event(
        run=_run_record(),
        namespace=StreamNamespace.from_value(()),
        tool_call={
            "name": "web_search",
            "id": "call_native_1",
            "args": {"query": "latest release"},
        },
        metadata={},
        parent_task_id=None,
    )
    assert len(persistence.records) == 1
    assert persistence.records[0].connector_slug is None


async def test_writer_failure_never_propagates_into_the_run() -> None:
    processor = _processor(_RaisingPersistence())
    # The STARTED event must still be emitted; the write error is swallowed.
    await processor.append_tool_call_chunk_event(
        run=_run_record(),
        namespace=StreamNamespace.from_value(()),
        tool_call={
            "name": "web_search",
            "id": "call_native_2",
            "args": {"query": "x"},
        },
        metadata={},
        parent_task_id=None,
    )
