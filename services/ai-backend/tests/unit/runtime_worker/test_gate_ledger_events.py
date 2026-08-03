"""PRD-C2 — ``gate.opened`` emission beside a gate interrupt (worker side).

Two gate kinds ride two different interrupts and both must open a ledger gate:

* the OAuth-**connect** gate on ``mcp_auth_required`` (shipped with PRD-C2), and
* the P1b **write-approval** gate on ``approval_requested`` / ``ask_a_question``,
  which emitted nothing at all — a write could park, be approved by a human and
  execute while the run ledger stayed silent about all three.

Exercises ``StreamOrchestrator._maybe_emit_gate_opened`` with a recording
producer for the payload shapes, then with the *real* ``RuntimeEventProducer``
for the property a recorder cannot see: that the append lands inside the run's
causal ledger prefix.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.surfaces_v2.ledger_models import GateAuthState
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.stream_events import StreamOrchestrator

from tests.unit.runtime_worker.test_ledger_seal_invariant import SealedRunMixin


@pytest.fixture(autouse=True)
def _fake_model(monkeypatch) -> None:
    """Run creation consults the real credential gate; the fake model opens it."""

    monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")


class RecordingProducer:
    """Captures ``append_api_event`` kwargs; optionally fails one event type."""

    def __init__(self, *, fail_on: RuntimeApiEventType | None = None) -> None:
        self.events: list[dict[str, object]] = []
        self._fail_on = fail_on

    async def append_api_event(self, **kwargs: object) -> None:
        if self._fail_on is not None and kwargs.get("event_type") is self._fail_on:
            raise RuntimeError("boom")
        self.events.append(kwargs)


class GateInterruptMixin:
    """Interrupt payloads in the exact shape ``ToolAccessGate`` raises them."""

    CONNECT_GATE_ID = "mcp_auth:run_123:seed:linear"
    WRITE_GATE_ID = "mcp_write:run_123:call_1"

    @staticmethod
    def run() -> RunRecord:
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

    @classmethod
    def connect_payload(cls, *, with_gate: bool) -> dict[str, object]:
        """``ToolAccessGate.park`` — the OAuth-connect gate."""

        payload: dict[str, object] = {
            "api_event_type": "mcp_auth_required",
            "event_type": "mcp_auth_required",
            "approval_id": cls.CONNECT_GATE_ID,
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

    @classmethod
    def write_payload(cls, *, with_gate: bool) -> dict[str, object]:
        """``ToolAccessGate.park_for_approval`` — the write-approval gate.

        Without the block this is an ordinary ``ask_a_question`` from the model's
        own question tool, which raises a byte-identical interrupt shape.
        """

        payload: dict[str, object] = {
            "api_event_type": "approval_requested",
            "event_type": "approval_requested",
            "approval_id": cls.WRITE_GATE_ID,
            "action_id": cls.WRITE_GATE_ID,
            "approval_kind": "ask_a_question",
            "server_name": "linear",
            "display_name": "Linear",
            "question": "Allow Linear to run create_issue?",
            "status": "pending",
        }
        if with_gate:
            payload["gate"] = {
                "v": 1,
                # The card's purpose embeds a sanitised primary ARGUMENT.
                "purpose": "to run create_issue on Linear: Fix login",
                "scopes": ["docs:read", "docs:write"],
                "op": "create_issue",
                "op_class": "write",
            }
        return payload

    @classmethod
    def emit(
        cls,
        *,
        producer: object,
        event_type: RuntimeApiEventType,
        payload: dict[str, object],
    ) -> None:
        orchestrator = StreamOrchestrator(event_producer=producer)  # type: ignore[arg-type]
        asyncio.run(
            orchestrator._maybe_emit_gate_opened(
                run=cls.run(), event_type=event_type, payload=payload
            )
        )


class TestConnectGateOpened(GateInterruptMixin):
    def test_gate_opened_emitted_beside_mcp_auth_event_flag_on(self) -> None:
        producer = RecordingProducer()
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload=self.connect_payload(with_gate=True),
        )

        assert len(producer.events) == 1
        event = producer.events[0]
        assert event["event_type"] is RuntimeApiEventType.GATE_OPENED
        assert event["source"] is StreamEventSource.SYSTEM
        assert event["payload"] == {
            "v": 1,
            "gate_id": self.CONNECT_GATE_ID,
            "connector": "linear",
            "purpose": "to run create_issue on Linear",
            "scopes": ["docs:read", "docs:write"],
            "auth_state": "missing",
        }

    def test_flag_off_no_gate_events_stream_byte_identical(self) -> None:
        producer = RecordingProducer()
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload=self.connect_payload(with_gate=False),
        )

        assert producer.events == []

    def test_payload_delivered_under_a_foreign_event_type_emits_nothing(self) -> None:
        """A connect payload announced as an approval classifies as neither."""

        producer = RecordingProducer()
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=self.connect_payload(with_gate=True),
        )

        assert producer.events == []

    def test_non_gate_event_type_never_emits_gate_opened(self) -> None:
        producer = RecordingProducer()
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
            payload=self.connect_payload(with_gate=True),
        )

        assert producer.events == []

    def test_ledger_emit_failure_swallowed_park_still_happens(self) -> None:
        producer = RecordingProducer(fail_on=RuntimeApiEventType.GATE_OPENED)
        # Must not raise — parking / approval correctness never depend on the emit.
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload=self.connect_payload(with_gate=True),
        )

        assert producer.events == []


class TestWriteGateOpened(GateInterruptMixin):
    def test_parked_write_emits_gate_opened_with_the_safe_field_set(self) -> None:
        """The audit gap, closed: a parked write now opens a ledger gate.

        Exact equality is the assertion — which connector, which operation, and
        ``auth_state: insufficient`` marking this a policy gate rather than a
        broken credential. The tool argument the human sees on the card
        ("Fix login") must not appear anywhere in the durable row.
        """

        producer = RecordingProducer()
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=self.write_payload(with_gate=True),
        )

        assert len(producer.events) == 1
        event = producer.events[0]
        assert event["event_type"] is RuntimeApiEventType.GATE_OPENED
        assert event["source"] is StreamEventSource.SYSTEM
        assert event["payload"] == {
            "v": 1,
            "gate_id": self.WRITE_GATE_ID,
            "connector": "linear",
            "purpose": "approve write create_issue on linear",
            "display_title": "Create issue · Linear",
            "scopes": ["docs:read", "docs:write"],
            "auth_state": GateAuthState.INSUFFICIENT.value,
        }
        assert "Fix login" not in repr(event["payload"])

    def test_plain_ask_a_question_emits_nothing(self) -> None:
        producer = RecordingProducer()
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=self.write_payload(with_gate=False),
        )

        assert producer.events == []

    def test_write_gate_emit_failure_swallowed(self) -> None:
        producer = RecordingProducer(fail_on=RuntimeApiEventType.GATE_OPENED)
        self.emit(
            producer=producer,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=self.write_payload(with_gate=True),
        )

        assert producer.events == []


class TestWriteGateOpensInsideTheCausalPrefix(GateInterruptMixin, SealedRunMixin):
    """The property a recording producer cannot see (``api/ledger_seal.py``).

    A run seals its causal prefix on its terminal event, so a gate appended
    afterwards is one no live client can ever receive. The write gate opens
    while the run is mid-flight, on the same pass as the interrupt it
    accompanies, and therefore needs no amendment.
    """

    async def test_gate_opened_lands_before_any_terminal_event(self) -> None:
        store = InMemoryRuntimeApiStore()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run = await self._seed_run(store)

        orchestrator = StreamOrchestrator(event_producer=producer)
        await orchestrator._maybe_emit_gate_opened(
            run=run,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=self.write_payload(with_gate=True),
        )

        names = self._event_names(store, run.run_id)
        assert names[-1] == RuntimeApiEventType.GATE_OPENED.value
        assert not (self.TERMINAL_EVENT_TYPES & set(names))
        appended = store.events_by_run[run.run_id][-1]
        assert appended.payload["gate_id"] == self.WRITE_GATE_ID
        assert appended.payload["auth_state"] == GateAuthState.INSUFFICIENT.value

    async def test_gate_opened_after_the_seal_is_refused_without_breaking_the_run(
        self,
    ) -> None:
        """The seal wins, and the refusal stays inside the best-effort guard."""

        store = InMemoryRuntimeApiStore()
        producer, run = await self._sealed_run(store)
        before = self._event_names(store, run.run_id)

        orchestrator = StreamOrchestrator(event_producer=producer)
        await orchestrator._maybe_emit_gate_opened(
            run=run,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload=self.write_payload(with_gate=True),
        )

        assert self._event_names(store, run.run_id) == before

    async def test_a_real_langgraph_interrupt_opens_the_gate_end_to_end(self) -> None:
        """Drive the production entry point, not the emitter in isolation.

        ``_maybe_emit_gate_opened`` is reached through
        ``append_native_interrupt_events`` → ``native_interrupt_payloads``, which
        normalises the interrupt before the emitter ever sees it. Calling the
        emitter directly would still pass if that projection dropped the
        additive ``gate`` block on the way — the block would simply be absent and
        the emit would silently no-op, which is precisely the failure mode being
        fixed. So park on a real ``__interrupt__`` value instead.
        """

        store = InMemoryRuntimeApiStore()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run = await self._seed_run(store)
        orchestrator = StreamOrchestrator(event_producer=producer)

        appended = await orchestrator.append_native_interrupt_events(
            run=run,
            value={
                "__interrupt__": [
                    {
                        "id": "write_interrupt_1",
                        "value": self.write_payload(with_gate=True),
                    }
                ]
            },
        )

        assert appended is True
        names = self._event_names(store, run.run_id)
        # The gate opens beside the interrupt it accompanies, on the same pass.
        assert names[-2:] == [
            RuntimeApiEventType.APPROVAL_REQUESTED.value,
            RuntimeApiEventType.GATE_OPENED.value,
        ]
        assert not (self.TERMINAL_EVENT_TYPES & set(names))
        gate_event = store.events_by_run[run.run_id][-1]
        assert gate_event.payload == {
            "v": 1,
            "gate_id": self.WRITE_GATE_ID,
            "connector": "linear",
            "purpose": "approve write create_issue on linear",
            "display_title": "Create issue · Linear",
            "scopes": ["docs:read", "docs:write"],
            "auth_state": GateAuthState.INSUFFICIENT.value,
        }
