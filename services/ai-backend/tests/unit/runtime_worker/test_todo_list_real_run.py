"""Hermetic end-to-end: a real run's ``write_todos`` reaches the stream as a checklist.

The unit tests around ``_append_todo_list_event`` drive the seam directly with a
hand-built tool-call chunk. That proves the projection, and proves nothing about
whether the seam FIRES — a shape mismatch between the fixture and what the real
graph actually streams would leave the panel permanently empty with every one of
those tests green.

So this drives the **real** worker, the **real** Deep Agents graph (whose stack
includes the real ``TodoListMiddleware`` and its real ``write_todos`` tool), and
the **real** streaming executor, with only the chat model faked
(``RUNTIME_FAKE_MODEL``) and scripted to call ``write_todos``. The assertions are
made against the events that actually landed in the store.
"""

from __future__ import annotations

import json

from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

from tests.unit.runtime_worker.test_fake_model_run_stream import FakeModelRunMixin

_TODOS = [
    {"content": "Pull the Q3 pipeline export", "status": "in_progress"},
    {"content": "Reconcile opportunity ids", "status": "pending"},
    {"content": "Flag accounts that moved more than 20%", "status": "pending"},
]


class TestTodoListReachesTheStreamFromARealRun(FakeModelRunMixin):
    """The panel's data must survive the whole production path, not just the seam."""

    @staticmethod
    def _script_write_todos(monkeypatch, todos: list[dict[str, str]]) -> None:
        """Point the deterministic fake at ``write_todos`` with a real argument list."""
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_CALLS", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_NAME", "write_todos")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_ARGS", json.dumps({"todos": todos}))

    @staticmethod
    async def _drive(store: InMemoryRuntimeApiStore, settings) -> str:
        """Enqueue one run and let the real worker execute it to completion."""
        run_id = await FakeModelRunMixin._enqueue_run(store, settings)
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        assert await worker.run_until_idle() == 1
        return run_id

    async def test_write_todos_emits_a_checklist_snapshot(self, monkeypatch) -> None:
        self._script_write_todos(monkeypatch, _TODOS)
        store = InMemoryRuntimeApiStore()
        settings = self._settings()

        run_id = await self._drive(store, settings)

        events = store.events_by_run[run_id]
        names = [event.event_type for event in events]
        assert "run_failed" not in names, names

        snapshots = [e for e in events if e.event_type == "todo_list_updated"]
        assert snapshots, f"no todo_list_updated event was emitted: {names}"
        payload = snapshots[0].payload
        # The list survived the provider stream, the graph, and the projection
        # with its structure intact — the flattening bug produced one run-on
        # string here, which is what shipped to the tool card.
        assert payload["todos"] == _TODOS
        assert payload["generation"] == 1
        assert payload["list_id"].endswith(":todos:1")

    async def test_the_raw_write_todos_frames_stay_internal(self, monkeypatch) -> None:
        # The checklist event is the ONLY public rendering of this tool. If the
        # frames were visible too, a run would show the panel and the card it
        # replaced, side by side.
        self._script_write_todos(monkeypatch, _TODOS)
        store = InMemoryRuntimeApiStore()
        settings = self._settings()

        run_id = await self._drive(store, settings)

        frames = [
            event
            for event in store.events_by_run[run_id]
            if isinstance(event.payload, dict)
            and event.payload.get("tool_name") == "write_todos"
        ]
        assert frames, "the run never actually called write_todos"
        for frame in frames:
            assert frame.payload.get("visibility") == "internal", frame.event_type

    async def test_the_checklist_is_a_public_event_the_client_can_read(
        self, monkeypatch
    ) -> None:
        # Its counterpart: the snapshot itself must NOT inherit the tool's
        # internal visibility, or the client projector drops it silently and the
        # panel never appears.
        self._script_write_todos(monkeypatch, _TODOS)
        store = InMemoryRuntimeApiStore()
        settings = self._settings()

        run_id = await self._drive(store, settings)

        [snapshot] = [
            event
            for event in store.events_by_run[run_id]
            if event.event_type == "todo_list_updated"
        ]
        assert snapshot.visibility != "internal"
        assert snapshot.activity_kind == "event"
