"""Unit tests for /subagents/ filesystem projection over runtime_events."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_runtime.context.memory.subagent_trace import (
    SubagentArtifactsBackend,
    SubagentTraceProjector,
)
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


def _envelope(
    *,
    sequence_no: int,
    event_type: RuntimeApiEventType,
    source: StreamEventSource = StreamEventSource.TOOL,
    payload: dict[str, object] | None = None,
    parent_task_id: str | None = None,
    visibility: RuntimeEventVisibility = RuntimeEventVisibility.USER,
    redaction_state: RuntimeEventRedactionState = RuntimeEventRedactionState.REDACTED,
    run_id: str = "run_123",
    conversation_id: str = "conv_123",
) -> RuntimeEventEnvelope:
    activity_kind = (
        RuntimeActivityKind.SUBAGENT
        if source is StreamEventSource.SUBAGENT
        else RuntimeActivityKind.TOOL
    )
    return RuntimeEventEnvelope(
        run_id=run_id,
        conversation_id=conversation_id,
        source=source,
        event_type=event_type,
        trace_id="trace_123",
        sequence_no=sequence_no,
        created_at=datetime(2026, 5, 3, 12, 0, sequence_no, tzinfo=timezone.utc),
        parent_task_id=parent_task_id,
        activity_kind=activity_kind,
        visibility=visibility,
        redaction_state=redaction_state,
        payload=payload or {},
    )


def _research_subagent_events() -> list[RuntimeEventEnvelope]:
    """Synthetic event sequence mirroring the user's actual research run.

    Subagent dispatched with a research objective, runs 3 web_searches, then
    completes successfully. This is the canonical fixture for the
    'what queries did subagent 2 do?' use case.
    """

    task_id = "call_research_subagent"
    events: list[RuntimeEventEnvelope] = [
        _envelope(
            sequence_no=1,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            source=StreamEventSource.SUBAGENT,
            payload={
                "task_id": task_id,
                "subagent_name": "general-purpose",
                "summary": (
                    "Research the phrase 'langchain deep agents' and "
                    "summarize what 'deep agents' means."
                ),
                "status": "queued",
            },
        ),
    ]
    queries = [
        '"langchain" "deep agents"',
        "LangChain deep agents meaning",
        "LangChain deep agent workflow planning reflection",
    ]
    seq = 2
    for i, query in enumerate(queries):
        events.append(
            _envelope(
                sequence_no=seq,
                event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
                payload={
                    "tool_name": "web_search",
                    "call_id": f"call_websearch_{i}",
                    "args": {"query": query},
                    "status": "started",
                },
                parent_task_id=task_id,
            )
        )
        events.append(
            _envelope(
                sequence_no=seq + 1,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload={
                    "tool_name": "web_search",
                    "call_id": f"call_websearch_{i}",
                    "output": [
                        {"snippet": f"result {i}.1", "url": "https://example.com/a"},
                        {"snippet": f"result {i}.2", "url": "https://example.com/b"},
                    ],
                    "status": "completed",
                },
                parent_task_id=task_id,
            )
        )
        seq += 2
    events.append(
        _envelope(
            sequence_no=seq,
            event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
            source=StreamEventSource.SUBAGENT,
            payload={
                "task_id": task_id,
                "subagent_name": "general-purpose",
                "summary": (
                    "Deep Agents is an open-source agent harness with "
                    "planning, filesystem, and subagent primitives."
                ),
                "status": "completed",
            },
        )
    )
    return events


def test_list_task_ids_returns_started_events_in_order() -> None:
    events = [
        _envelope(
            sequence_no=1,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            source=StreamEventSource.SUBAGENT,
            payload={"task_id": "call_a", "subagent_name": "general-purpose"},
        ),
        _envelope(
            sequence_no=2,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            source=StreamEventSource.SUBAGENT,
            payload={"task_id": "call_b", "subagent_name": "researcher"},
        ),
    ]
    pairs = SubagentTraceProjector.list_task_ids_with_names(events)
    assert pairs == (("call_a", "general-purpose"), ("call_b", "researcher"))


def test_tool_calls_json_preserves_args_verbatim() -> None:
    """The 'what queries did subagent 2 do?' use case requires args verbatim."""

    events = _research_subagent_events()
    rendered = SubagentTraceProjector.project_tool_calls(
        "call_research_subagent", events
    )
    parsed = json.loads(rendered)
    assert len(parsed) == 3
    queries = [entry["args"]["query"] for entry in parsed]
    assert queries == [
        '"langchain" "deep agents"',
        "LangChain deep agents meaning",
        "LangChain deep agent workflow planning reflection",
    ]
    for entry in parsed:
        assert entry["tool_name"] == "web_search"
        assert entry["status"] == "completed"


def test_summary_md_carries_objective_and_result() -> None:
    events = _research_subagent_events()
    rendered = SubagentTraceProjector.project_summary("call_research_subagent", events)
    assert "## Status\ncompleted" in rendered
    assert "Research the phrase 'langchain deep agents'" in rendered
    assert "Deep Agents is an open-source agent harness" in rendered
    assert "## Run\nrun_123" in rendered


def test_summary_md_status_running_when_not_completed() -> None:
    """Partial work preservation: subagent_started without subagent_completed
    still produces a usable summary so on-call/the supervisor know what
    happened before the run was cancelled or timed out."""

    events = _research_subagent_events()
    # Drop the SUBAGENT_COMPLETED tail to simulate cancel/timeout.
    truncated = [
        e for e in events if e.event_type is not RuntimeApiEventType.SUBAGENT_COMPLETED
    ]
    rendered = SubagentTraceProjector.project_summary(
        "call_research_subagent", truncated
    )
    assert "## Status\nrunning" in rendered
    # Tool calls already in the event log should still be available — the
    # supervisor can still read tool_calls.json to see partial work.
    tool_calls = SubagentTraceProjector.project_tool_calls(
        "call_research_subagent", truncated
    )
    assert len(json.loads(tool_calls)) == 3


def test_conversation_md_interleaves_tool_calls_and_results() -> None:
    events = _research_subagent_events()
    rendered = SubagentTraceProjector.project_conversation(
        "call_research_subagent", events
    )
    # Args are rendered as JSON inside `> tool_call: ...` lines, so the inner
    # quotes are escaped — assert on the JSON-encoded form. tool_calls.json
    # provides the unescaped values for the verbatim-query use case.
    assert "tool_call: web_search" in rendered
    assert "tool_result:" in rendered
    assert r"\"langchain\" \"deep agents\"" in rendered
    assert "LangChain deep agents meaning" in rendered


def test_visible_events_drops_internal_and_offloaded() -> None:
    events = [
        _envelope(
            sequence_no=1,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            source=StreamEventSource.SUBAGENT,
            payload={"task_id": "call_a", "subagent_name": "general-purpose"},
            visibility=RuntimeEventVisibility.USER,
        ),
        _envelope(
            sequence_no=2,
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
            payload={"tool_name": "write_todos", "call_id": "x", "args": {}},
            parent_task_id="call_a",
            visibility=RuntimeEventVisibility.INTERNAL,
        ),
        _envelope(
            sequence_no=3,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={"tool_name": "read_file", "call_id": "y", "output": "foo"},
            parent_task_id="call_a",
            visibility=RuntimeEventVisibility.USER,
            redaction_state=RuntimeEventRedactionState.OFFLOADED,
        ),
        _envelope(
            sequence_no=4,
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
            payload={
                "tool_name": "web_search",
                "call_id": "z",
                "args": {"query": "kept"},
            },
            parent_task_id="call_a",
        ),
    ]
    visible = SubagentTraceProjector.visible_events(events)
    kept_calls = [(e.event_type, (e.payload or {}).get("tool_name")) for e in visible]
    assert (
        RuntimeApiEventType.TOOL_CALL_STARTED,
        "web_search",
    ) in kept_calls
    assert (
        RuntimeApiEventType.TOOL_CALL_STARTED,
        "write_todos",
    ) not in kept_calls
    assert (
        RuntimeApiEventType.TOOL_RESULT,
        "read_file",
    ) not in kept_calls


class _FakeEventStore:
    def __init__(self, events: list[RuntimeEventEnvelope]) -> None:
        self._events = events

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> list[RuntimeEventEnvelope]:
        return [e for e in self._events if e.run_id == run_id]


class _FakePersistence:
    def __init__(self, message_run_ids: list[str]) -> None:
        self._run_ids = message_run_ids

    async def list_messages(self, *, org_id: str, conversation_id: str, limit: int):
        from runtime_api.schemas import MessageRecord, MessageRole

        return [
            MessageRecord(
                message_id=f"msg_{i}",
                conversation_id=conversation_id,
                org_id=org_id,
                run_id=run_id,
                role=MessageRole.USER,
                content_text="x",
            )
            for i, run_id in enumerate(self._run_ids)
        ]


@pytest.mark.asyncio
async def test_backend_als_root_lists_subagents_newest_first() -> None:
    """Paths are returned relative to the `/subagents/` route so deepagents'
    `CompositeBackend` can prepend the prefix once on its own. Returning
    fully-qualified `/subagents/...` paths from this backend would result in
    double-prefixed paths like `/subagents/subagents/...` after the composite
    remap."""

    events = _research_subagent_events()
    backend = SubagentArtifactsBackend(
        event_store=_FakeEventStore(events),
        persistence=_FakePersistence(["run_123"]),
        org_id="org_123",
        conversation_id="conv_123",
        current_run_id="run_123",
    )
    result = await backend.als("/subagents/")
    assert result.error is None
    paths = [entry["path"] for entry in (result.entries or [])]
    assert paths == ["/call_research_subagent/"]


@pytest.mark.asyncio
async def test_backend_als_subagent_dir_lists_four_files() -> None:
    events = _research_subagent_events()
    backend = SubagentArtifactsBackend(
        event_store=_FakeEventStore(events),
        persistence=_FakePersistence(["run_123"]),
        org_id="org_123",
        conversation_id="conv_123",
        current_run_id="run_123",
    )
    result = await backend.als("/subagents/call_research_subagent/")
    assert result.error is None
    paths = [entry["path"] for entry in (result.entries or [])]
    assert sorted(paths) == [
        "/call_research_subagent/conversation.md",
        "/call_research_subagent/events.jsonl",
        "/call_research_subagent/summary.md",
        "/call_research_subagent/tool_calls.json",
    ]


@pytest.mark.asyncio
async def test_backend_aread_tool_calls_json_includes_verbatim_queries() -> None:
    events = _research_subagent_events()
    backend = SubagentArtifactsBackend(
        event_store=_FakeEventStore(events),
        persistence=_FakePersistence(["run_123"]),
        org_id="org_123",
        conversation_id="conv_123",
        current_run_id="run_123",
    )
    result = await backend.aread("/subagents/call_research_subagent/tool_calls.json")
    assert result.error is None
    body = (result.file_data or {}).get("content")
    assert body is not None
    parsed = json.loads(body)
    assert any('"langchain" "deep agents"' == c["args"]["query"] for c in parsed)


@pytest.mark.asyncio
async def test_backend_awrite_rejects_subagents_path() -> None:
    backend = SubagentArtifactsBackend(
        event_store=_FakeEventStore([]),
        persistence=_FakePersistence([]),
        org_id="org_123",
        conversation_id="conv_123",
        current_run_id="run_123",
    )
    result = await backend.awrite(
        "/subagents/x/notes.md", "supervisor cannot write here"
    )
    assert result.error is not None
    assert "read-only" in result.error.lower()


@pytest.mark.asyncio
async def test_backend_aread_unknown_file_returns_error() -> None:
    events = _research_subagent_events()
    backend = SubagentArtifactsBackend(
        event_store=_FakeEventStore(events),
        persistence=_FakePersistence(["run_123"]),
        org_id="org_123",
        conversation_id="conv_123",
        current_run_id="run_123",
    )
    result = await backend.aread("/subagents/call_research_subagent/notes.md")
    assert result.error is not None


@pytest.mark.asyncio
async def test_backend_handles_paths_stripped_by_composite_backend() -> None:
    """deepagents' `CompositeBackend` strips the matched route prefix before
    delegating to the per-route backend. So when the supervisor's `ls
    /subagents/` reaches us we receive `/`, and `read /subagents/<id>/x`
    arrives as `/<id>/x`. Both shapes must work."""

    events = _research_subagent_events()
    backend = SubagentArtifactsBackend(
        event_store=_FakeEventStore(events),
        persistence=_FakePersistence(["run_123"]),
        org_id="org_123",
        conversation_id="conv_123",
        current_run_id="run_123",
    )
    # Stripped root (`/subagents/` → `/`). Entries are relative for the
    # composite remap to prepend the prefix.
    result = await backend.als("/")
    assert result.error is None
    paths = [entry["path"] for entry in (result.entries or [])]
    assert "/call_research_subagent/" in paths

    # Stripped task dir
    result = await backend.als("/call_research_subagent/")
    assert result.error is None
    paths = [entry["path"] for entry in (result.entries or [])]
    assert "/call_research_subagent/tool_calls.json" in paths

    # Stripped file path
    read_result = await backend.aread("/call_research_subagent/tool_calls.json")
    assert read_result.error is None
    assert (read_result.file_data or {}).get("content") is not None


@pytest.mark.asyncio
async def test_backend_collects_events_across_prior_runs_in_conversation() -> None:
    """Cross-turn use case: Turn 2's run can read Turn 1's subagent traces."""

    turn1 = _research_subagent_events()
    # Place turn1 events on run_turn1
    turn1 = [e.model_copy(update={"run_id": "run_turn1"}) for e in turn1]
    backend = SubagentArtifactsBackend(
        event_store=_FakeEventStore(turn1),
        persistence=_FakePersistence(["run_turn1"]),
        org_id="org_123",
        conversation_id="conv_123",
        current_run_id="run_turn2",  # different run, same conversation
    )
    result = await backend.als("/subagents/")
    paths = [entry["path"] for entry in (result.entries or [])]
    assert "/call_research_subagent/" in paths
