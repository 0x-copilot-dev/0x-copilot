"""Hermetic end-to-end: exhausting a tool budget must not kill the run.

The bug this pins: a run that made more than the per-run allowance of tool
calls died with a fatal ``RUN INTERRUPTED / the tool reported an error``
instead of letting the model finalize. ``BudgetExceeded`` was raised out of
the guard, escaped through ``astream_runtime``, and terminated the run —
discarding every tool result the run had already gathered. The rejection
message told the model to "finalize", but the model never got to read it.

These drive the **real** worker, the **real** Deep Agents graph, and the
**real** streaming executor, with only the concrete chat model faked
(``RUNTIME_FAKE_MODEL``). The fake is scripted to keep calling a tool well
past the configured cap, which is exactly the shape of the research prompts
that reproduced the failure.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from agent_runtime.persistence.records import (
    DefaultToolBudget,
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_worker import dependencies as worker_dependencies
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

from tests.unit.runtime_worker.test_fake_model_run_stream import FakeModelRunMixin


class _WebSearchArgs(BaseModel):
    """Mirrors the shipped web_search argument surface."""

    query: str
    display_title: str | None = None
    display_summary: str | None = None


_EXECUTIONS: list[str] = []
"""Every time the search leaf actually ran. This — not the invocation
ledger, which records *attempted* calls including refused ones — is the
measure of spend the budget exists to bound."""


class _OfflineWebSearchTool(BaseTool):
    """Stands in for the DuckDuckGo leaf; everything above it stays real.

    Only the network call is replaced. The tool is still composed through
    the real ``WebSearchToolRegistry`` → ``CitationCapturingRegistry`` →
    ``ToolBudgetGuardedRegistry`` → ``ToolErrorPolicyRegistry`` stack, so
    the budget guard and error policy under test are the shipped ones.
    """

    name: str = "web_search"
    description: str = "Search the web for information."
    args_schema: type[BaseModel] = _WebSearchArgs

    _RESULT = "[{'title': 'Result', 'link': 'https://example.test', 'snippet': 'x'}]"

    def _run(self, *args: object, **kwargs: object) -> str:
        _EXECUTIONS.append(self.name)
        return self._RESULT

    async def _arun(self, *args: object, **kwargs: object) -> str:
        _EXECUTIONS.append(self.name)
        return self._RESULT


class ToolBudgetRunMixin(FakeModelRunMixin):
    """Enqueue a run whose scripted model overruns a deliberately tiny cap."""

    CAP = 2
    """Small enough that the overrun happens within a few graph steps."""

    TOOL_NAME = "web_search"
    """The budget guard wraps runtime-registered tools; Deep Agents
    built-ins (``ls``, ``read_file``, ``task`` …) are injected by the
    graph builder and never pass through the guarded registry, so they
    cannot exercise this path."""

    TOOL_ARGS = {"query": "what shipped this week"}

    @staticmethod
    def _set_cap(store: InMemoryRuntimeApiStore, cap: int) -> None:
        """Replace the seeded global budget with a tight wildcard cap."""

        store.tool_budgets[DefaultToolBudget.ID] = ToolBudgetRecord(
            id=DefaultToolBudget.ID,
            org_id=None,
            tool_name=DefaultToolBudget.TOOL_NAME,
            max_calls_per_run=cap,
            enforcement=ToolBudgetEnforcement.HARD,
        )

    @classmethod
    async def _run_overrunning_budget(
        cls,
        monkeypatch: pytest.MonkeyPatch,
        *,
        tool_calls: int,
        cap: int | None = None,
    ) -> tuple[InMemoryRuntimeApiStore, str]:
        """Execute one real run whose model attempts ``tool_calls`` dispatches."""

        _EXECUTIONS.clear()
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_CALLS", str(tool_calls))
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_NAME", cls.TOOL_NAME)
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_ARGS", json.dumps(cls.TOOL_ARGS))
        monkeypatch.setattr(
            worker_dependencies.WebSearchToolRegistry,
            "_web_search_tool",
            classmethod(lambda _cls: _OfflineWebSearchTool()),
        )

        store = InMemoryRuntimeApiStore()
        cls._set_cap(store, cls.CAP if cap is None else cap)
        settings = cls._settings()
        run_id = await cls._enqueue_run(store, settings)

        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        processed = await worker.run_until_idle()
        assert processed == 1
        return store, run_id

    @staticmethod
    def _event_names(store: InMemoryRuntimeApiStore, run_id: str) -> list[str]:
        return [event.event_type for event in store.events_by_run[run_id]]


class TestRunSurvivesToolBudgetExhaustion(ToolBudgetRunMixin):
    async def test_run_reaches_final_response_after_budget_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression: overrunning the cap still finalizes the run.

        Before the fix this run ended in ``run_failed`` with
        ``error_code=external_service_error`` and no assistant answer.
        """

        # Two calls fit under the cap; the remaining three are refused.
        store, run_id = await self._run_overrunning_budget(monkeypatch, tool_calls=5)
        names = self._event_names(store, run_id)

        assert "run_failed" not in names, names
        assert "final_response" in names, names
        assert "run_completed" in names, names
        assert names.index("final_response") < names.index("run_completed"), names

        # The user gets a real answer, not a dead run.
        assistant = [m for m in store.messages.values() if m.role == "assistant"]
        assert assistant, "no assistant message persisted"
        assert (assistant[0].content_text or "").strip()

    async def test_budget_is_still_enforced_while_the_run_survives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Surviving must not mean the cap leaked — spend is still bounded.

        A fix that simply admitted the over-cap calls would also produce a
        ``final_response``, so the run finishing is only half the contract.
        """

        await self._run_overrunning_budget(monkeypatch, tool_calls=5)

        assert len(_EXECUTIONS) == self.CAP, (
            f"{len(_EXECUTIONS)} {self.TOOL_NAME} executions against a cap of "
            f"{self.CAP} — the budget stopped being enforced"
        )

    async def test_model_is_told_to_finalize(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal must reach the model as a tool result it can act on."""

        store, run_id = await self._run_overrunning_budget(monkeypatch, tool_calls=5)

        payloads = [
            str(event.payload)
            for event in store.events_by_run[run_id]
            if event.event_type == "tool_result"
        ]
        refusals = [body for body in payloads if "ToolBudgetRejected" in body]
        assert refusals, (
            "no budget refusal was surfaced as a tool result; "
            f"tool_result payloads: {payloads}"
        )
        assert any("finalize" in body for body in refusals), refusals

    async def test_relentless_looping_still_terminates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A model that never stops calling tools must still end, not spin.

        The surfaced-rejection allowance is the backstop; this pins that it
        actually fires rather than looping until some unrelated limit trips.
        """

        store, run_id = await self._run_overrunning_budget(
            monkeypatch, tool_calls=100, cap=1
        )
        names = self._event_names(store, run_id)
        # Either outcome is acceptable — what matters is that the run reached
        # a terminal state instead of spinning on free refusals.
        assert "run_completed" in names or "run_failed" in names, names
