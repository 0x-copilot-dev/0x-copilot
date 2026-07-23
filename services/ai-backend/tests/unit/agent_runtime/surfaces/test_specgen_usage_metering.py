"""PRD-A2 D5b — per-attempt usage metering of the spec-generation path.

The DoD fake-completion test: a shaping that fails once then succeeds records
TWO usage rows (one per attempt, correct tokens each), attributed to
``view_shaping``. Also proves ``None`` tokens record zeros and that an
un-metered generator still works. Fakes only — no live model.
"""

from __future__ import annotations

import json

from agent_runtime.capabilities.surfaces.generator import (
    GenToolDescriptor,
    SpecCompletionResult,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.spec_models import SurfaceSpec
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_meter import (
    MeteredModelInvocation,
    UsageMeter,
)
from agent_runtime.observability.usage_recorder import InMemoryUsageRecorder
from runtime_api.schemas import RunRecord

_LINEAR_SAMPLE: dict[str, object] = {
    "issue": {
        "id": "uuid-1",
        "identifier": "ENG-1421",
        "title": "Fix login redirect loop",
        "state": {"name": "In Progress"},
        "url": "https://linear.app/acme/issue/ENG-1421",
    }
}

_VALID_CANDIDATE: dict[str, object] = {
    "spec_version": 1,
    "archetype": "record",
    "title_path": "issue.title",
    "subtitle_path": "issue.identifier",
    "fields": [{"label": "State", "path": "issue.state.name", "format": "badge"}],
    "link": {"label": "Open in Linear", "url_path": "issue.url"},
}

_DESCRIPTOR = GenToolDescriptor(name="get_issue", description="Fetch a Linear issue.")
_SHAPING_MODEL = "anthropic:claude-haiku-4-5"


class _FakeCompletionWithTokens:
    """Returns pre-canned ``(candidate, in, out)`` triples, one per attempt."""

    def __init__(self, triples: list[tuple[object, int | None, int | None]]) -> None:
        self._triples = list(triples)

    async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
        candidate, in_tokens, out_tokens = self._triples.pop(0)
        raw = (
            json.dumps(candidate)
            if isinstance(candidate, (dict, list))
            else str(candidate)
        )
        return SpecCompletionResult(
            candidate=candidate,
            raw_text=raw,
            model=_SHAPING_MODEL,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )


class SpecgenMeteringMixin:
    """Shared run + meter assembly for the spec-generation metering tests."""

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

    def _metered_generator(
        self, triples: list[tuple[object, int | None, int | None]]
    ) -> tuple[SurfaceSpecGenerator, InMemoryUsageRecorder]:
        recorder = InMemoryUsageRecorder()
        meter = UsageMeter(recorder=recorder, emit_event=None, surfaces_v2=False)
        invocation = MeteredModelInvocation(
            meter=meter, run=self._run_record(), purpose=Purpose.VIEW_SHAPING
        )
        generator = SurfaceSpecGenerator(
            completion=_FakeCompletionWithTokens(triples), usage_meter=invocation
        )
        return generator, recorder


class TestSpecgenMetering(SpecgenMeteringMixin):
    async def test_retried_attempt_records_per_attempt(self) -> None:
        # First attempt is schema-valid but fails the path lint (a
        # non-resolving title_path); the retry succeeds. Both attempts carry a
        # SpecCompletionResult ⇒ both record a usage row with their own tokens.
        bad = dict(_VALID_CANDIDATE)
        bad["title_path"] = "issue.does_not_exist"
        generator, recorder = self._metered_generator(
            [(bad, 120, 48), (dict(_VALID_CANDIDATE), 200, 60)]
        )

        result = await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )

        assert isinstance(result, SurfaceSpec)
        assert len(recorder.calls) == 2
        # Per-attempt tokens preserved (real spend, not merged/deduped).
        assert (recorder.calls[0].input_tokens, recorder.calls[0].output_tokens) == (
            120,
            48,
        )
        assert (recorder.calls[1].input_tokens, recorder.calls[1].output_tokens) == (
            200,
            60,
        )

    async def test_purpose_is_view_shaping(self) -> None:
        generator, recorder = self._metered_generator([(dict(_VALID_CANDIDATE), 10, 5)])

        await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )

        assert len(recorder.calls) == 1
        assert recorder.calls[0].purpose == "view_shaping"
        # model split from the shaping model id, not the run's main model.
        assert recorder.calls[0].model_provider == "anthropic"
        assert recorder.calls[0].model_name == "claude-haiku-4-5"
        assert recorder.calls[0].user_id == "user_1"

    async def test_none_token_result_records_zeros(self) -> None:
        generator, recorder = self._metered_generator(
            [(dict(_VALID_CANDIDATE), None, None)]
        )

        await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )

        assert len(recorder.calls) == 1
        assert recorder.calls[0].input_tokens == 0
        assert recorder.calls[0].output_tokens == 0

    async def test_model_error_attempt_records_nothing(self) -> None:
        # A model-invocation error yields outcome.result is None ⇒ no attribution.
        class _RaisingCompletion:
            async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
                raise RuntimeError("provider exploded")

        recorder = InMemoryUsageRecorder()
        meter = UsageMeter(recorder=recorder, emit_event=None, surfaces_v2=False)
        invocation = MeteredModelInvocation(
            meter=meter, run=self._run_record(), purpose=Purpose.VIEW_SHAPING
        )
        generator = SurfaceSpecGenerator(
            completion=_RaisingCompletion(), usage_meter=invocation
        )

        await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )

        assert recorder.calls == []

    async def test_no_meter_injected_keeps_generation_working(self) -> None:
        generator = SurfaceSpecGenerator(
            completion=_FakeCompletionWithTokens([(dict(_VALID_CANDIDATE), 10, 5)])
        )

        result = await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )

        assert isinstance(result, SurfaceSpec)
