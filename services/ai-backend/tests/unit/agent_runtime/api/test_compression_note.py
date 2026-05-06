"""PR A1 — `RuntimeEventProducer.append_compression_note` emission shape."""

from __future__ import annotations

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


class _RecordingPersistence:
    def __init__(self) -> None:
        self.latest_sequence_no: int | None = None

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> None:
        self.latest_sequence_no = latest_sequence_no


class _RecordingEventStore:
    def __init__(self) -> None:
        self.drafts: list[RuntimeEventDraft] = []

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        self.drafts.append(event)
        return RuntimeEventEnvelope(
            run_id=event.run_id,
            conversation_id=event.conversation_id,
            sequence_no=len(self.drafts),
            source=event.source,
            event_type=event.event_type,
            trace_id=event.trace_id,
            parent_event_id=event.parent_event_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            parent_task_id=event.parent_task_id,
            task_id=event.task_id,
            subagent_id=event.subagent_id,
            display_title=event.display_title,
            summary=event.summary,
            status=event.status,
            activity_kind=event.activity_kind
            or RuntimeEventPresentationProjector.activity_kind_for(
                event_type=event.event_type,
                source=event.source,
            ),
            visibility=event.visibility,
            redaction_state=event.redaction_state,
            presentation=event.presentation,
            payload=event.payload,
            metadata=event.metadata,
        )


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run_compression",
        conversation_id="conversation_123",
        org_id="org_123",
        user_id="user_123",
        user_message_id="message_123",
        trace_id="trace_123",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_123",
            org_id="org_123",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run_compression",
            trace_id="trace_123",
        ),
    )


async def test_append_compression_note_emits_note_envelope_with_payload_fields() -> (
    None
):
    """The helper appends a single ``COMPRESSION_NOTE`` envelope with the
    redacted token-budget fields the FE NoteCard renders inline."""

    event_store = _RecordingEventStore()
    persistence = _RecordingPersistence()
    producer = RuntimeEventProducer(persistence=persistence, event_store=event_store)

    envelope = await producer.append_compression_note(
        run=_run_record(),
        before_tokens=12_400,
        after_tokens=3_200,
        strategy="summarize",
        summary="Summarised 3 older messages.",
        payload_refs={"summary_id": "ctx_sum_42"},
    )

    assert envelope.event_type == RuntimeApiEventType.COMPRESSION_NOTE
    assert envelope.source == StreamEventSource.RUNTIME
    assert envelope.activity_kind == "note"
    assert envelope.payload["before_tokens"] == 12_400
    assert envelope.payload["after_tokens"] == 3_200
    assert envelope.payload["strategy"] == "summarize"
    assert envelope.payload["summary"] == "Summarised 3 older messages."
    assert envelope.payload["payload_refs"] == {"summary_id": "ctx_sum_42"}
    assert persistence.latest_sequence_no == 1


async def test_append_compression_note_omits_optional_fields_when_unset() -> None:
    event_store = _RecordingEventStore()
    persistence = _RecordingPersistence()
    producer = RuntimeEventProducer(persistence=persistence, event_store=event_store)

    envelope = await producer.append_compression_note(
        run=_run_record(),
        before_tokens=8_000,
        after_tokens=1_500,
        strategy="offload",
    )

    assert "summary" not in envelope.payload
    assert "payload_refs" not in envelope.payload
    assert envelope.payload["strategy"] == "offload"


async def test_append_compression_note_rejects_inconsistent_token_counts() -> None:
    event_store = _RecordingEventStore()
    persistence = _RecordingPersistence()
    producer = RuntimeEventProducer(persistence=persistence, event_store=event_store)

    with pytest.raises(ValueError):
        await producer.append_compression_note(
            run=_run_record(),
            before_tokens=1_000,
            after_tokens=2_000,
            strategy="summarize",
        )

    with pytest.raises(ValueError):
        await producer.append_compression_note(
            run=_run_record(),
            before_tokens=-1,
            after_tokens=0,
            strategy="summarize",
        )

    with pytest.raises(ValueError):
        await producer.append_compression_note(
            run=_run_record(),
            before_tokens=100,
            after_tokens=10,
            strategy="   ",
        )
