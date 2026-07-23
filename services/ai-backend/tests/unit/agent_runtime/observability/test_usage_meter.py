"""Unit tests for the PRD-A2 recording seam (:class:`UsageMeter`).

Covers the exhaustive purpose→ledger mapping, the row-write / event-emit split,
flag-off silence, background-purpose row-only behaviour, the exact SDR §5 payload
shape, and fail-soft emit swallowing. Fakes only — no network, no live model.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.execution.contracts import AgentRuntimeContext, JsonObject
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_meter import (
    MeteredModelInvocation,
    UsageMeter,
)
from agent_runtime.observability.usage_recorder import InMemoryUsageRecorder
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord
from agent_runtime.surfaces_v2.ledger_models import UsagePurpose
from runtime_api.schemas import RunRecord


class _RecordingEmitter:
    """Capture every ``usage.recorded`` payload handed to the meter."""

    def __init__(self) -> None:
        self.payloads: list[JsonObject] = []

    async def __call__(self, payload: JsonObject) -> None:
        self.payloads.append(payload)


class _RaisingEmitter:
    """Emitter that always raises — proves fail-soft swallowing."""

    def __init__(self) -> None:
        self.called = 0

    async def __call__(self, payload: JsonObject) -> None:
        self.called += 1
        raise RuntimeError("simulated event-store failure")


class UsageMeterFixtureMixin:
    """Shared record/run builders + meter assembly."""

    @staticmethod
    def _call_record(
        *,
        purpose: str = "main",
        model_provider: str = "openai",
        model_name: str = "gpt-5.4-mini",
        input_tokens: int = 100,
        output_tokens: int = 40,
        surface_id: str | None = None,
        user_id: str | None = "user_1",
    ) -> RuntimeModelCallUsageRecord:
        return RuntimeModelCallUsageRecord(
            org_id="org_a",
            run_id="run-1",
            conversation_id="conv-1",
            trace_id="trace-1",
            user_id=user_id,
            model_provider=model_provider,
            model_name=model_name,
            purpose=purpose,
            surface_id=surface_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            created_at=datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),
        )

    @staticmethod
    def _run_record() -> RunRecord:
        return RunRecord(
            run_id="run-1",
            org_id="org_a",
            user_id="user_1",
            conversation_id="conv-1",
            user_message_id="msg-user-1",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            trace_id="trace-1",
            runtime_context=AgentRuntimeContext(
                org_id="org_a",
                user_id="user_1",
                roles=["employee"],
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                run_id="run-1",
                trace_id="trace-1",
            ),
        )

    @staticmethod
    def _pricing_at() -> datetime:
        return datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


class TestLedgerPurposeMapping(UsageMeterFixtureMixin):
    def test_ledger_purpose_mapping_is_exhaustive_over_purpose_enum(self) -> None:
        # Every store Purpose must be classified (row-only or a ledger purpose),
        # so a future Purpose member can't silently fall through unmapped.
        assert set(UsageMeter._PURPOSE_TO_LEDGER.keys()) == set(Purpose)

    def test_run_bucket_purposes_map_to_run(self) -> None:
        for purpose in (
            Purpose.MAIN,
            Purpose.TOOL_PLANNING,
            Purpose.TOOL_INTERPRETATION,
            Purpose.CONTEXT_COMPRESSION,
        ):
            assert UsageMeter.ledger_purpose_for(purpose.value) is UsagePurpose.RUN

    def test_subagent_and_shaping_purposes_map_to_themselves(self) -> None:
        assert UsageMeter.ledger_purpose_for("subagent_work") is UsagePurpose.SUBAGENT
        assert (
            UsageMeter.ledger_purpose_for("view_shaping") is UsagePurpose.VIEW_SHAPING
        )
        assert (
            UsageMeter.ledger_purpose_for("shape_request") is UsagePurpose.SHAPE_REQUEST
        )

    def test_background_purposes_map_to_none(self) -> None:
        for purpose in (
            Purpose.TODO_EXTRACTION,
            Purpose.LIBRARY_RETRIEVAL,
            Purpose.LIBRARY_INDEXING,
            Purpose.PALETTE_RANKING,
            Purpose.MEMORY_RETRIEVAL,
            Purpose.MEMORY_INDEXING,
            Purpose.MEMORY_EXTRACTION,
        ):
            assert UsageMeter.ledger_purpose_for(purpose.value) is None

    def test_unknown_purpose_string_maps_to_none(self) -> None:
        # Fail-soft: a legacy/unknown purpose never crashes the seam.
        assert UsageMeter.ledger_purpose_for("not_a_real_purpose") is None


class TestRecordCall(UsageMeterFixtureMixin):
    async def test_record_call_writes_row_via_recorder(self) -> None:
        recorder = InMemoryUsageRecorder()
        emitter = _RecordingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=True)
        record = self._call_record(purpose="main")

        await meter.record_call(record, pricing_at=self._pricing_at())

        assert len(recorder.calls) == 1
        assert recorder.calls[0] is record
        # main → run → event emitted.
        assert len(emitter.payloads) == 1

    async def test_flag_off_emits_no_event(self) -> None:
        # Adversarial: emitter is wired, purpose maps to a ledger purpose, yet
        # flag-off ⇒ the row is still written but NO event fires.
        recorder = InMemoryUsageRecorder()
        emitter = _RecordingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=False)

        await meter.record_call(self._call_record(), pricing_at=self._pricing_at())

        assert len(recorder.calls) == 1
        assert emitter.payloads == []

    async def test_background_purpose_writes_row_but_no_event(self) -> None:
        recorder = InMemoryUsageRecorder()
        emitter = _RecordingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=True)

        await meter.record_call(
            self._call_record(purpose="todo_extraction"),
            pricing_at=self._pricing_at(),
        )

        assert len(recorder.calls) == 1
        assert emitter.payloads == []

    async def test_no_emitter_wired_writes_row_only(self) -> None:
        recorder = InMemoryUsageRecorder()
        meter = UsageMeter(recorder=recorder, emit_event=None, surfaces_v2=True)

        await meter.record_call(self._call_record(), pricing_at=self._pricing_at())

        assert len(recorder.calls) == 1

    async def test_payload_contains_exactly_sdr_fields(self) -> None:
        recorder = InMemoryUsageRecorder()
        emitter = _RecordingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=True)

        await meter.record_call(
            self._call_record(
                purpose="subagent_work",
                model_provider="anthropic",
                model_name="claude-haiku-4-5",
                input_tokens=123,
                output_tokens=45,
            ),
            pricing_at=self._pricing_at(),
        )

        assert len(emitter.payloads) == 1
        payload = emitter.payloads[0]
        # surface_id omitted when None — exactly the SDR §5 required set.
        assert set(payload.keys()) == {
            "v",
            "purpose",
            "model",
            "tokens_in",
            "tokens_out",
        }
        assert payload == {
            "v": 1,
            "purpose": "subagent",
            "model": "anthropic:claude-haiku-4-5",
            "tokens_in": 123,
            "tokens_out": 45,
        }

    async def test_payload_includes_surface_id_when_present(self) -> None:
        recorder = InMemoryUsageRecorder()
        emitter = _RecordingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=True)

        await meter.record_call(
            self._call_record(purpose="shape_request", surface_id="surface-42"),
            pricing_at=self._pricing_at(),
        )

        assert emitter.payloads[0]["surface_id"] == "surface-42"
        assert emitter.payloads[0]["purpose"] == "shape_request"

    async def test_emitter_failure_is_swallowed_and_logged(self) -> None:
        recorder = InMemoryUsageRecorder()
        emitter = _RaisingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=True)

        # Must NOT raise into the caller — usage never breaks a run.
        await meter.record_call(self._call_record(), pricing_at=self._pricing_at())

        assert emitter.called == 1
        # The row still landed despite the emit failure.
        assert len(recorder.calls) == 1


class TestMeteredModelInvocation(UsageMeterFixtureMixin):
    async def test_record_attempt_builds_row_from_run_and_model_id(self) -> None:
        recorder = InMemoryUsageRecorder()
        emitter = _RecordingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=True)
        invocation = MeteredModelInvocation(
            meter=meter, run=self._run_record(), purpose=Purpose.VIEW_SHAPING
        )

        await invocation.record_attempt(
            model_id="anthropic:claude-haiku-4-5",
            input_tokens=120,
            output_tokens=48,
            duration_ms=321,
        )

        assert len(recorder.calls) == 1
        row = recorder.calls[0]
        # Attribution copied from the bound run.
        assert row.org_id == "org_a"
        assert row.run_id == "run-1"
        assert row.conversation_id == "conv-1"
        assert row.user_id == "user_1"
        assert row.trace_id == "trace-1"
        # model_id split into the shaping model's provider/name (NOT the run's).
        assert row.model_provider == "anthropic"
        assert row.model_name == "claude-haiku-4-5"
        assert row.purpose == "view_shaping"
        assert row.surface_id is None
        assert row.input_tokens == 120
        assert row.output_tokens == 48
        assert row.duration_ms == 321
        # view_shaping → ledger event emitted.
        assert emitter.payloads[0]["purpose"] == "view_shaping"

    async def test_none_tokens_record_zeros(self) -> None:
        recorder = InMemoryUsageRecorder()
        meter = UsageMeter(recorder=recorder, emit_event=None, surfaces_v2=True)
        invocation = MeteredModelInvocation(
            meter=meter, run=self._run_record(), purpose=Purpose.VIEW_SHAPING
        )

        await invocation.record_attempt(
            model_id="openai:gpt-5-mini",
            input_tokens=None,
            output_tokens=None,
            duration_ms=10,
        )

        assert recorder.calls[0].input_tokens == 0
        assert recorder.calls[0].output_tokens == 0

    async def test_shape_request_carries_surface_id(self) -> None:
        recorder = InMemoryUsageRecorder()
        emitter = _RecordingEmitter()
        meter = UsageMeter(recorder=recorder, emit_event=emitter, surfaces_v2=True)
        invocation = MeteredModelInvocation(
            meter=meter, run=self._run_record(), purpose=Purpose.SHAPE_REQUEST
        )

        await invocation.record_attempt(
            model_id="openai:gpt-5-mini",
            input_tokens=10,
            output_tokens=5,
            duration_ms=7,
            surface_id="surface-9",
        )

        assert recorder.calls[0].surface_id == "surface-9"
        assert emitter.payloads[0]["surface_id"] == "surface-9"
        assert emitter.payloads[0]["purpose"] == "shape_request"

    async def test_bad_model_id_is_swallowed(self) -> None:
        # A malformed model id must not raise into the generation loop.
        recorder = InMemoryUsageRecorder()
        meter = UsageMeter(recorder=recorder, emit_event=None, surfaces_v2=True)
        invocation = MeteredModelInvocation(
            meter=meter, run=self._run_record(), purpose=Purpose.VIEW_SHAPING
        )

        await invocation.record_attempt(
            model_id="",  # SurfaceModelConfigFactory rejects empty ids
            input_tokens=1,
            output_tokens=1,
            duration_ms=1,
        )

        # Nothing recorded, but no exception propagated.
        assert recorder.calls == []
