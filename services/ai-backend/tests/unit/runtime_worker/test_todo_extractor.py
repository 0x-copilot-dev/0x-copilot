"""Unit tests for the P3-A2 todo-extractor worker job.

Covers:

- The extractor LLM call routes through ``build_chat_model`` (monkeypatched
  in this module's namespace) — no direct provider SDK touch.
- A ``RuntimeModelCallUsageRecord`` lands on the injected ``UsageRecorder``
  with ``purpose='todo_extraction'``.
- Valid JSON from the model produces persisted ``TodoExtractionRecord`` rows
  with state=PENDING in the in-memory store.
- Cross-tenant isolation: the in-memory store refuses to surface another
  tenant's rows for the same id.
- Malformed model output produces zero candidates (no exception).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_recorder import InMemoryUsageRecorder
from agent_runtime.persistence.records import (
    TodoExtractionRecord,
    TodoExtractionState,
)
from runtime_adapters.in_memory.todo_extraction_store import (
    InMemoryTodoExtractionStore,
)
from runtime_worker.jobs import todo_extractor as todo_extractor_module
from runtime_worker.jobs.todo_extractor import TodoExtractor


# -- fixtures ----------------------------------------------------------------


@dataclass
class _FakeMessage:
    """Minimal stand-in for a ``MessageRecord`` shape used by the extractor."""

    message_id: str
    role: str
    content_text: str


@dataclass
class _FakePersistence:
    """Yields a fixed message list. Tracks the args used for assertions."""

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
    """Stub for the LangChain AIMessage returned by ``model.ainvoke``."""

    content: Any
    usage_metadata: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)


class _FakeChatModel:
    """LangChain-compatible chat model stub with controllable response."""

    def __init__(self, response: _FakeAIMessage) -> None:
        self._response = response
        self.invocations: list[Any] = []

    async def ainvoke(self, messages: Any) -> _FakeAIMessage:
        self.invocations.append(messages)
        return self._response


def _model_config() -> ModelConfig:
    """Return a minimal ModelConfig matching the production contract."""
    return ModelConfig(
        provider="openai",
        model_name="gpt-4o-mini",
        max_input_tokens=8000,
        timeout_seconds=10.0,
        temperature=0.0,
    )


def _patch_build_chat_model(monkeypatch, model: _FakeChatModel) -> None:
    """Monkeypatch ``build_chat_model`` in the extractor module's namespace.

    The CI guard verifies the import path (``build_chat_model`` lives in
    deep_agent_builder.py); patching the reference in the extractor module
    swaps the model bind without bypassing the canonical entry point.
    """
    monkeypatch.setattr(
        todo_extractor_module, "build_chat_model", lambda *_a, **_k: model
    )


# -- tests -------------------------------------------------------------------


class TestTodoExtractor:
    """Behavior of the run-scoped extractor."""

    async def test_extract_persists_candidates_and_records_usage(
        self, monkeypatch
    ) -> None:
        persistence = _FakePersistence(
            messages=[
                _FakeMessage(
                    message_id="m1",
                    role="user",
                    content_text="I need to send the slides to Dana by Friday.",
                ),
                _FakeMessage(
                    message_id="m2",
                    role="assistant",
                    content_text="Got it — I'll also remind you to update the Q4 deck.",
                ),
            ]
        )
        store = InMemoryTodoExtractionStore()
        recorder = InMemoryUsageRecorder()

        model = _FakeChatModel(
            _FakeAIMessage(
                content=(
                    "["
                    '{"text":"Send slides to Dana","due":"2026-05-22","confidence":0.85},'
                    '{"text":"Update Q4 deck","due":null,"confidence":0.6}'
                    "]"
                ),
                usage_metadata={"input_tokens": 50, "output_tokens": 18},
            )
        )
        _patch_build_chat_model(monkeypatch, model)

        extractor = TodoExtractor(
            persistence=persistence,
            extraction_store=store,
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

        assert result.candidate_count == 2
        assert result.persisted_count == 2
        # Persisted records are pending and carry the originating run.
        for record in result.persisted:
            assert record.org_id == "acme"
            assert record.owner_user_id == "sarah"
            assert record.run_id == "run-001"
            assert record.conversation_id == "conv-001"
            assert record.state == TodoExtractionState.PENDING
            assert record.source_message_id == "m2"  # last assistant id
        assert {r.proposed_text for r in result.persisted} == {
            "Send slides to Dana",
            "Update Q4 deck",
        }

        # Exactly one usage row landed on the recorder with TODO_EXTRACTION.
        assert len(recorder.calls) == 1
        usage_row = recorder.calls[0]
        assert usage_row.purpose == Purpose.TODO_EXTRACTION.value
        assert usage_row.purpose == "todo_extraction"
        assert usage_row.org_id == "acme"
        assert usage_row.run_id == "run-001"
        assert usage_row.input_tokens == 50
        assert usage_row.output_tokens == 18
        assert usage_row.model_provider == "openai"
        assert usage_row.model_name == "gpt-4o-mini"

        # Store reflects the same rows.
        listed = await store.list_pending(
            org_id="acme", owner_user_id="sarah", limit=10
        )
        assert len(listed) == 2

    async def test_malformed_model_output_yields_no_candidates(
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
        store = InMemoryTodoExtractionStore()
        recorder = InMemoryUsageRecorder()

        # Free-text response with no JSON — extractor must tolerate.
        model = _FakeChatModel(
            _FakeAIMessage(
                content="I cannot extract anything useful from this.",
                usage_metadata={"input_tokens": 12, "output_tokens": 8},
            )
        )
        _patch_build_chat_model(monkeypatch, model)

        extractor = TodoExtractor(
            persistence=persistence,
            extraction_store=store,
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
        assert result.candidate_count == 0
        assert result.persisted_count == 0
        # Usage was still recorded — the LLM call did happen.
        assert len(recorder.calls) == 1
        assert recorder.calls[0].purpose == Purpose.TODO_EXTRACTION.value

    async def test_empty_transcript_does_not_call_model(self, monkeypatch) -> None:
        persistence = _FakePersistence(messages=[])
        store = InMemoryTodoExtractionStore()
        recorder = InMemoryUsageRecorder()

        # If the model is hit, the dataclass below raises (response is None).
        called = {"hit": False}

        class _PoisonModel:
            async def ainvoke(self, _messages: Any) -> Any:
                called["hit"] = True
                raise AssertionError("model should not be invoked")

        monkeypatch.setattr(
            todo_extractor_module,
            "build_chat_model",
            lambda *_a, **_k: _PoisonModel(),
        )

        extractor = TodoExtractor(
            persistence=persistence,
            extraction_store=store,
            usage_recorder=recorder,
            model_config=_model_config(),
        )
        result = await extractor.extract(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-003",
            conversation_id="conv-empty",
            trace_id="trace-003",
        )
        assert result.candidate_count == 0
        assert called["hit"] is False
        assert recorder.calls == []  # no LLM call → no usage row


class TestStoreTenantIsolation:
    """Cross-tenant guards on the in-memory store."""

    async def test_get_by_id_refuses_other_tenant(self) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-x",
            conversation_id="conv-x",
            proposed_text="ship it",
        )
        await store.insert_many([record])

        # Caller from the wrong tenant cannot read the row.
        result = await store.get_by_id(org_id="globex", extraction_id=record.id)
        assert result is None

        # Correct tenant sees it.
        own = await store.get_by_id(org_id="acme", extraction_id=record.id)
        assert own is not None
        assert own.proposed_text == "ship it"

    async def test_list_pending_does_not_leak_across_tenants(self) -> None:
        store = InMemoryTodoExtractionStore()
        acme = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="acme task",
        )
        globex = TodoExtractionRecord(
            org_id="globex",
            owner_user_id="sarah",
            run_id="run-b",
            conversation_id="conv-b",
            proposed_text="globex task",
        )
        await store.insert_many([acme, globex])
        # Same user_id across tenants — predicate must scope by org first.
        acme_listing = await store.list_pending(
            org_id="acme", owner_user_id="sarah", limit=10
        )
        assert len(acme_listing) == 1
        assert acme_listing[0].proposed_text == "acme task"

    async def test_update_state_transitions_pending(self) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-x",
            conversation_id="conv-x",
            proposed_text="archive Q3",
        )
        await store.insert_many([record])
        resolved_at = datetime(2026, 5, 18, 13, 0, tzinfo=timezone.utc)
        updated = await store.update_state(
            org_id="acme",
            extraction_id=record.id,
            state=TodoExtractionState.REJECTED,
            resolved_at=resolved_at,
        )
        assert updated is not None
        assert updated.state == TodoExtractionState.REJECTED
        assert updated.resolved_at == resolved_at

        # Now the pending listing excludes it.
        listing = await store.list_pending(
            org_id="acme", owner_user_id="sarah", limit=10
        )
        assert listing == ()


class TestRecordValidation:
    """Pydantic guards on :class:`TodoExtractionRecord`."""

    def test_rejects_bad_due_shape(self) -> None:
        # Stay within the 10-char ``max_length`` so the regex validator
        # is the one that trips, not the length cap.
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            TodoExtractionRecord(
                org_id="acme",
                owner_user_id="sarah",
                run_id="r",
                conversation_id="c",
                proposed_text="x",
                suggested_due="05/22/2026",
            )

    def test_rejects_confidence_over_one(self) -> None:
        with pytest.raises(ValueError):
            TodoExtractionRecord(
                org_id="acme",
                owner_user_id="sarah",
                run_id="r",
                conversation_id="c",
                proposed_text="x",
                confidence_score=1.5,
            )
