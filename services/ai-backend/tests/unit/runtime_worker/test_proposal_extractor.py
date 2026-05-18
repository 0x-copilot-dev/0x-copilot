"""Unit tests for the P12-A4/A5 proposal extractor worker job.

Covers:

- The extractor LLM call routes through ``build_chat_model`` (monkeypatched
  in this module's namespace) — no direct provider SDK touch.
- A ``RuntimeModelCallUsageRecord`` lands on the injected ``UsageRecorder``
  with ``purpose='memory_extraction'``.
- Valid JSON from the model produces typed MemoryProposal / RoutineProposal /
  AtlasCronSuggestion tuples.
- Malformed model output produces zero candidates (no exception).
- Cost-cap budget: a small cap forces the extractor to skip without
  hitting the LLM and without writing a usage row.
- Empty transcript skips the LLM call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_recorder import InMemoryUsageRecorder
from runtime_worker.jobs import proposal_extractor as proposal_extractor_module
from runtime_worker.jobs.proposal_extractor import (
    AtlasCronSuggestion,
    MemoryProposal,
    ProposalExtractor,
    RoutineProposal,
)


# -- fixtures ----------------------------------------------------------------


@dataclass
class _FakeMessage:
    message_id: str
    role: str
    content_text: str


@dataclass
class _FakePersistence:
    messages: list[_FakeMessage]
    last_args: dict[str, Any] = field(default_factory=dict)

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> list[_FakeMessage]:
        self.last_args = {
            "org_id": org_id,
            "conversation_id": conversation_id,
            "limit": limit,
            "include_deleted": include_deleted,
        }
        return list(self.messages)


@dataclass
class _FakeAIMessage:
    content: Any
    usage_metadata: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)


class _FakeChatModel:
    def __init__(self, response: _FakeAIMessage) -> None:
        self._response = response
        self.invocations: list[Any] = []

    async def ainvoke(self, messages: Any) -> _FakeAIMessage:
        self.invocations.append(messages)
        return self._response


def _model_config() -> ModelConfig:
    return ModelConfig(
        provider="openai",
        model_name="gpt-4o-mini",
        max_input_tokens=8000,
        timeout_seconds=10.0,
        temperature=0.0,
    )


def _patch_build_chat_model(monkeypatch, model: _FakeChatModel) -> None:
    """Patch ``build_chat_model`` in the extractor module's namespace.

    Mirrors test_todo_extractor's discipline — the CI guard verifies the
    canonical import path; patching the reference here swaps the bind
    without bypassing the entry point.
    """
    monkeypatch.setattr(
        proposal_extractor_module, "build_chat_model", lambda *_a, **_k: model
    )


# -- tests -------------------------------------------------------------------


class TestProposalExtractor:
    """Behavior of the run-scoped proposal extractor."""

    async def test_extract_persists_memories_routines_and_crons(
        self, monkeypatch
    ) -> None:
        persistence = _FakePersistence(
            messages=[
                _FakeMessage(
                    message_id="m1",
                    role="user",
                    content_text=(
                        "I always run the Monday standup notes through "
                        "the slack-summary skill. Schedule it weekly."
                    ),
                ),
                _FakeMessage(
                    message_id="m2",
                    role="assistant",
                    content_text="Got it — I'll set up the routine.",
                ),
            ]
        )
        recorder = InMemoryUsageRecorder()

        model = _FakeChatModel(
            _FakeAIMessage(
                content=(
                    "{"
                    '"memories":[{"kind":"preference","title":"slack-summary skill",'
                    '"body":"Uses slack-summary for standup notes",'
                    '"confidence":0.7}],'
                    '"routines":[{"title":"Weekly standup summary",'
                    '"trigger_hint":"Monday 09:00","confidence":0.85}],'
                    '"atlas_crons":[{"title":"Run standup summary",'
                    '"cadence_hint":"weekly","confidence":0.8}]'
                    "}"
                ),
                usage_metadata={"input_tokens": 80, "output_tokens": 40},
            )
        )
        _patch_build_chat_model(monkeypatch, model)

        extractor = ProposalExtractor(
            persistence=persistence,
            usage_recorder=recorder,
            model_config=_model_config(),
            clock=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )

        result = await extractor.extract(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-001",
            conversation_id="conv-001",
            trace_id="trace-001",
        )

        assert result.skipped_reason is None
        assert result.total_count == 3
        assert len(result.memories) == 1
        assert len(result.routines) == 1
        assert len(result.atlas_crons) == 1
        assert isinstance(result.memories[0], MemoryProposal)
        assert result.memories[0].kind == "preference"
        assert result.memories[0].title == "slack-summary skill"
        assert isinstance(result.routines[0], RoutineProposal)
        assert result.routines[0].trigger_hint == "Monday 09:00"
        assert isinstance(result.atlas_crons[0], AtlasCronSuggestion)
        assert result.atlas_crons[0].cadence_hint == "weekly"

        # Exactly one usage row landed with MEMORY_EXTRACTION purpose.
        assert len(recorder.calls) == 1
        usage_row = recorder.calls[0]
        assert usage_row.purpose == Purpose.MEMORY_EXTRACTION.value
        assert usage_row.purpose == "memory_extraction"
        assert usage_row.org_id == "acme"
        assert usage_row.run_id == "run-001"
        assert usage_row.input_tokens == 80
        assert usage_row.output_tokens == 40
        assert usage_row.model_provider == "openai"

    async def test_malformed_model_output_yields_no_proposals(
        self, monkeypatch
    ) -> None:
        persistence = _FakePersistence(
            messages=[
                _FakeMessage(
                    message_id="m1",
                    role="user",
                    content_text="Plan the offsite.",
                )
            ]
        )
        recorder = InMemoryUsageRecorder()
        model = _FakeChatModel(
            _FakeAIMessage(
                content="I cannot produce JSON output reliably here.",
                usage_metadata={"input_tokens": 12, "output_tokens": 8},
            )
        )
        _patch_build_chat_model(monkeypatch, model)

        extractor = ProposalExtractor(
            persistence=persistence,
            usage_recorder=recorder,
            model_config=_model_config(),
        )
        result = await extractor.extract(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-002",
            conversation_id="conv-001",
            trace_id="trace-002",
        )
        assert result.total_count == 0
        assert result.skipped_reason is None
        # Usage WAS recorded — the LLM call did happen.
        assert len(recorder.calls) == 1
        assert recorder.calls[0].purpose == Purpose.MEMORY_EXTRACTION.value

    async def test_empty_transcript_does_not_call_model(self, monkeypatch) -> None:
        persistence = _FakePersistence(messages=[])
        recorder = InMemoryUsageRecorder()

        class _PoisonModel:
            async def ainvoke(self, _messages: Any) -> Any:
                raise AssertionError("model should not be invoked")

        monkeypatch.setattr(
            proposal_extractor_module,
            "build_chat_model",
            lambda *_a, **_k: _PoisonModel(),
        )

        extractor = ProposalExtractor(
            persistence=persistence,
            usage_recorder=recorder,
            model_config=_model_config(),
        )
        result = await extractor.extract(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-empty",
            conversation_id="conv-empty",
            trace_id="trace-empty",
        )
        assert result.total_count == 0
        assert result.skipped_reason == "empty_transcript"
        assert recorder.calls == []


class TestCostCap:
    """Per-run budget cap is enforced BEFORE the LLM call."""

    async def test_default_cap_documented(self) -> None:
        """The default cap matches sub-PRD §9 ($0.001 / run)."""
        extractor = ProposalExtractor(
            persistence=_FakePersistence(messages=[]),
            usage_recorder=InMemoryUsageRecorder(),
            model_config=_model_config(),
        )
        assert extractor.cost_cap_usd_per_run == 0.001

    async def test_configurable_cap(self) -> None:
        """The cap is a configurable parameter."""
        extractor = ProposalExtractor(
            persistence=_FakePersistence(messages=[]),
            usage_recorder=InMemoryUsageRecorder(),
            model_config=_model_config(),
            cost_cap_usd_per_run=0.005,
        )
        assert extractor.cost_cap_usd_per_run == 0.005

    async def test_budget_exceeded_skips_llm_call(self, monkeypatch) -> None:
        """When the estimator says the call exceeds the cap, we skip
        without invoking the model and without writing a usage row.
        """
        # 100 short messages → enough chars that the default rate
        # ($0.001 / 1k input tokens) will exceed a $0.0000001 cap.
        persistence = _FakePersistence(
            messages=[
                _FakeMessage(
                    message_id=f"m{i}",
                    role="user",
                    content_text="some content " * 20,
                )
                for i in range(30)
            ]
        )
        recorder = InMemoryUsageRecorder()

        class _PoisonModel:
            async def ainvoke(self, _messages: Any) -> Any:
                raise AssertionError("model should not be invoked under cap")

        monkeypatch.setattr(
            proposal_extractor_module,
            "build_chat_model",
            lambda *_a, **_k: _PoisonModel(),
        )

        extractor = ProposalExtractor(
            persistence=persistence,
            usage_recorder=recorder,
            model_config=_model_config(),
            cost_cap_usd_per_run=0.0000001,  # absurdly low
        )
        result = await extractor.extract(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-cap",
            conversation_id="conv-cap",
            trace_id="trace-cap",
        )
        assert result.total_count == 0
        assert result.skipped_reason == "cost_cap_exceeded"
        # Critically: no usage row, no LLM call.
        assert recorder.calls == []
