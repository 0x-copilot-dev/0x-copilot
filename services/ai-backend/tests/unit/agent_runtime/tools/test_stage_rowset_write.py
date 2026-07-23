"""Tests for the ``stage_rowset_write`` builtin tool (PRD-D3).

The propose seam for bulk row-sets: validate untrusted input, delegate to
``WriteStager.stage_rowset``, and return a summary — WITHOUT interrupting the
graph (staging is non-blocking). A typed domain rejection becomes a safe
tool-result error dict; nothing is staged and the run continues.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    StageRowsetWriteInput,
    StageRowsetWriteTool,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.staging import WriteStager
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_bulk"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run() -> RunRecord:
    return RunRecord(
        run_id=_RUN,
        conversation_id="conv_bulk",
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_1",
        trace_id="trace_1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_1",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


def _tool() -> tuple[StageRowsetWriteTool, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    run = _run()
    store.runs[_RUN] = run
    store.events_by_run.setdefault(_RUN, [])
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    stager = WriteStager(
        draft_store=None,  # type: ignore[arg-type]
        ledger=RuntimeStageLedger(event_producer=producer),
    )
    tool = StageRowsetWriteTool(stager=stager, run=run, org_id=_ORG, run_id=_RUN)
    return tool, store


_ROWS = [
    {
        "row_key": "iss-1",
        "title": "Acme renewal",
        "target_args": {"id": "iss-1", "priority": 2},
        "changes": [{"field": "priority", "old": 1, "new": 2}],
    },
    {
        "row_key": "iss-2",
        "title": "Beta onboarding",
        "target_args": {"id": "iss-2", "priority": 3},
        "changes": [{"field": "priority", "old": 1, "new": 3}],
    },
]


class TestToolInput:
    def test_valid_input_parses(self) -> None:
        parsed = StageRowsetWriteInput.model_validate(
            {
                "target_connector": "linear",
                "target_op": "update_issue",
                "title": "Reprioritize",
                "rows": _ROWS,
                "agent_holds": [{"row_key": "iss-2", "reason": "recent reply"}],
            }
        )
        assert len(parsed.rows) == 2
        assert parsed.rows[0].target_args == {"id": "iss-1", "priority": 2}


class TestToolInvoke:
    async def test_stages_and_returns_summary_without_interrupt(self) -> None:
        tool, store = _tool()
        result = await tool.ainvoke(
            {
                "target_connector": "linear",
                "target_op": "update_issue",
                "title": "Reprioritize",
                "rows": _ROWS,
                "agent_holds": [{"row_key": "iss-2", "reason": "recent reply"}],
            }
        )
        assert result["ok"] is True
        assert result["rows_staged"] == 2
        assert result["rows_pre_held"] == 1
        assert result["status"] == "staged"
        assert result["stage_id"]
        # Staged three v2 events (surface.created / write.staged / revision.added);
        # NO interrupt / approval event was emitted (non-blocking staging).
        types = [
            getattr(getattr(e, "event_type", None), "value", None)
            for e in store.events_by_run[_RUN]
        ]
        assert types == ["surface.created", "write.staged", "revision.added"]

    async def test_malformed_input_is_safe_error_not_raised(self) -> None:
        tool, store = _tool()
        result = await tool.ainvoke({"target_connector": "linear"})  # missing fields
        assert result["ok"] is False
        assert "message" in result
        assert store.events_by_run[_RUN] == []  # nothing staged

    async def test_over_cap_rowset_is_safe_error(self) -> None:
        tool, store = _tool()
        big = [
            {"row_key": f"r{i}", "title": f"R{i}", "target_args": {}}
            for i in range(201)
        ]
        result = await tool.ainvoke(
            {
                "target_connector": "linear",
                "target_op": "update_issue",
                "title": "Too big",
                "rows": big,
            }
        )
        assert result["ok"] is False
        assert store.events_by_run[_RUN] == []  # typed domain rejection, no event
