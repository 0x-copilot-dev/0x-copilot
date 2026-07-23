"""PRD-C2 — ``gate.opened`` emission beside the mcp_auth interrupt (worker side).

Exercises ``StreamOrchestrator._maybe_emit_gate_opened`` directly with a
recording event producer: a v2 gate block ⇒ a ``gate.opened`` SYSTEM event with
the SDR §5 payload; no block (flag off) ⇒ nothing emitted (byte-identical
stream); an emit failure is swallowed (parking never depends on the ledger).
"""

from __future__ import annotations

import asyncio


from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.stream_events import StreamOrchestrator


class _RecordingProducer:
    def __init__(self, *, fail_on: RuntimeApiEventType | None = None) -> None:
        self.events: list[dict[str, object]] = []
        self._fail_on = fail_on

    async def append_api_event(self, **kwargs: object) -> None:
        if self._fail_on is not None and kwargs.get("event_type") is self._fail_on:
            raise RuntimeError("boom")
        self.events.append(kwargs)


def _run() -> RunRecord:
    return RunRecord(
        run_id="run_123",
        conversation_id="conv_123",
        org_id="org_123",
        user_id="user_123",
        user_message_id="msg_123",
        trace_id="trace_123",
        model_provider="openai",
        model_name="gpt-4o-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_123",
            org_id="org_123",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-4o-mini",
                "max_input_tokens": 4096,
                "timeout_seconds": 30,
                "temperature": 0,
            },
            run_id="run_123",
            trace_id="trace_123",
        ),
    )


def _mcp_auth_payload(*, with_gate: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "api_event_type": "mcp_auth_required",
        "event_type": "mcp_auth_required",
        "approval_id": "mcp_auth:run_123:seed:linear",
        "approval_kind": "mcp_auth",
        "server_id": "seed:linear",
        "server_name": "linear",
        "display_name": "Linear",
    }
    if with_gate:
        payload["gate"] = {
            "v": 1,
            "purpose": "to run create_issue on Linear",
            "scopes": ["docs:read", "docs:write"],
            "auth_state": "missing",
            "op": "create_issue",
            "op_class": "write",
        }
    return payload


def test_gate_opened_emitted_beside_mcp_auth_event_flag_on() -> None:
    producer = _RecordingProducer()
    orch = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    asyncio.run(
        orch._maybe_emit_gate_opened(
            run=_run(),
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload=_mcp_auth_payload(with_gate=True),
        )
    )
    assert len(producer.events) == 1
    event = producer.events[0]
    assert event["event_type"] is RuntimeApiEventType.GATE_OPENED
    assert event["source"] is StreamEventSource.SYSTEM
    assert event["payload"] == {
        "v": 1,
        "gate_id": "mcp_auth:run_123:seed:linear",
        "connector": "linear",
        "purpose": "to run create_issue on Linear",
        "scopes": ["docs:read", "docs:write"],
        "auth_state": "missing",
    }


def test_flag_off_no_gate_events_stream_byte_identical() -> None:
    producer = _RecordingProducer()
    orch = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    asyncio.run(
        orch._maybe_emit_gate_opened(
            run=_run(),
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload=_mcp_auth_payload(with_gate=False),
        )
    )
    assert producer.events == []


def test_non_mcp_auth_event_never_emits_gate_opened() -> None:
    producer = _RecordingProducer()
    orch = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    asyncio.run(
        orch._maybe_emit_gate_opened(
            run=_run(),
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=_mcp_auth_payload(with_gate=True),
        )
    )
    assert producer.events == []


def test_ledger_emit_failure_swallowed_park_still_happens() -> None:
    producer = _RecordingProducer(fail_on=RuntimeApiEventType.GATE_OPENED)
    orch = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
    # Must not raise — parking / approval correctness never depend on the emit.
    asyncio.run(
        orch._maybe_emit_gate_opened(
            run=_run(),
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload=_mcp_auth_payload(with_gate=True),
        )
    )
    assert producer.events == []
