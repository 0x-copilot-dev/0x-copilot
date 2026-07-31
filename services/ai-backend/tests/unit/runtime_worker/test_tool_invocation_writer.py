"""PRD-08 D1b — the tool-invocation writer, opening AND closing.

A completed MCP tool call writes ONE ``runtime_tool_invocations`` row whose
``connector_slug`` is the resolved MCP server slug; a native (connector-less)
tool call writes one row with ``connector_slug = None`` (a step, not an app). The
write is best-effort — a persistence error must never propagate into the run.

The closing half is pinned here too, and pinned on the FAILING path first. Only
the ``running`` row was ever written, so every invocation in the desktop ledger
sat at ``status: running`` with ``completed_at: null`` and
``safe_error_code: null`` long after its run had finished and answered the user.
A passing-path test cannot catch that: the bug is not that success is recorded
wrong, it is that nothing terminal is recorded at all.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.tool_outcomes import ToolInvocationOutcome
from agent_runtime.persistence.records.common import ToolInvocationStatus
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


class _ToolCallDriverMixin:
    """Drive a real tool call through the processor: started, then settled."""

    @staticmethod
    async def start_call(
        processor: StreamMessageProcessor,
        run: RunRecord,
        *,
        call_id: str,
        args: dict[str, object] | None = None,
    ) -> None:
        """Emit the TOOL_CALL_STARTED half of an MCP tool call."""
        await processor.append_tool_call_chunk_event(
            run=run,
            namespace=StreamNamespace.from_value(()),
            tool_call={
                "name": "call_mcp_tool",
                "id": call_id,
                "args": args or {"server_name": "linear", "tool_name": "list_issues"},
            },
            metadata={},
            parent_task_id=None,
        )

    @staticmethod
    async def settle_call(
        processor: StreamMessageProcessor,
        run: RunRecord,
        *,
        call_id: str,
        status: str,
        output: object,
    ) -> None:
        """Emit the tool-result half, exactly as the LangGraph stream would."""
        await processor.process(
            run=run,
            namespace=StreamNamespace.from_value(()),
            message={
                "type": "tool",
                "tool_call_id": call_id,
                "name": "call_mcp_tool",
                "status": status,
                "output": output,
            },
            delta=None,
        )


class TestToolInvocationLedgerCloses(_ToolCallDriverMixin):
    """The terminal ``put`` must land for success, failure, and cancellation."""

    async def test_failing_call_closes_with_status_and_error_recorded(self) -> None:
        # THE regression. The reported run finished and answered the user while
        # both of its invocations stayed `running` — so the store built to say
        # what a tool did and why it failed said neither, and the failure had to
        # be reconstructed from raw HTTP status codes instead.
        persistence = _CapturingPersistence()
        processor = _processor(persistence)
        run = _run_record()
        await self.start_call(processor, run, call_id="call_fail_1")
        await self.settle_call(
            processor,
            run,
            call_id="call_fail_1",
            status="error",
            output={"message": "connector rejected the request"},
        )

        assert len(persistence.records) == 2
        opened, closed = persistence.records
        # Same row, updated — not a second row appended beside the first.
        assert closed.invocation_id == opened.invocation_id
        assert opened.status is ToolInvocationStatus.RUNNING
        assert closed.status is ToolInvocationStatus.FAILED
        assert closed.completed_at is not None
        assert closed.safe_error_code == "tool_exception"
        assert closed.safe_error_message
        assert "connector rejected the request" in closed.safe_error_message

    async def test_successful_call_closes_without_inventing_an_error(self) -> None:
        persistence = _CapturingPersistence()
        processor = _processor(persistence)
        run = _run_record()
        await self.start_call(processor, run, call_id="call_ok_1")
        await self.settle_call(
            processor,
            run,
            call_id="call_ok_1",
            status="success",
            output={"issues": []},
        )

        closed = persistence.records[-1]
        assert closed.status is ToolInvocationStatus.COMPLETED
        assert closed.completed_at is not None
        assert closed.safe_error_code is None
        assert closed.safe_error_message is None

    async def test_cancelled_call_closes_as_cancelled_not_failed(self) -> None:
        persistence = _CapturingPersistence()
        processor = _processor(persistence)
        run = _run_record()
        await self.start_call(processor, run, call_id="call_cancel_1")
        # The shape the run handler's terminal reconciliation emits.
        await processor.close_tool_invocation(
            run=run,
            call_id="call_cancel_1",
            **ToolInvocationOutcome.from_result_payload(
                {
                    "status": "cancelled",
                    "error_code": "tool_cancelled",
                    "error_message": "Run cancelled",
                }
            ),
        )

        closed = persistence.records[-1]
        assert closed.status is ToolInvocationStatus.CANCELLED
        assert closed.completed_at is not None
        assert closed.safe_error_code == "tool_cancelled"

    async def test_close_records_the_arguments_the_call_was_made_with(self) -> None:
        # `args` was `{}` on every row, which is why the failing request payload
        # could not be recovered — the thing that blocked diagnosing the 400.
        # It is not redacted by policy (`JsonObjectCoercer` performs no
        # redaction); it was simply never written.
        persistence = _CapturingPersistence()
        processor = _processor(persistence)
        run = _run_record()
        await self.start_call(
            processor,
            run,
            call_id="call_args_1",
            args={
                "server_name": "linear",
                "tool_name": "list_issues",
                "arguments": {"assignee": "me", "token": "sk-should-not-persist"},
            },
        )
        await self.settle_call(
            processor,
            run,
            call_id="call_args_1",
            status="error",
            output={"message": "bad request"},
        )

        closed = persistence.records[-1]
        assert closed.args["tool_name"] == "list_issues"
        assert closed.args["arguments"]["assignee"] == "me"
        # Credential-shaped keys are stripped at every depth: the column is
        # `args_json_redacted` and it now earns the name.
        assert closed.args["arguments"]["token"] == "[redacted]"


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
