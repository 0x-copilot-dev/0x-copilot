"""Pins the base-extraction contract shared by the dict-backed runtime stores.

``MaterializedViewStoreBase`` writes the in-memory-view + domain policy for
context occupancy exactly once; the two backends differ only in two hooks:
``_state_guard`` (locking) and ``_persist_context_occupancy`` (durability). The
cross-backend behaviour is already pinned by
``test_context_occupancy_stores.py``. These tests pin the two things that file
alone owns and that a future "simplification" of the hooks could silently drop:

* the durability *economy* — a redelivered append writes no second ledger line;
* the *lock fence* — an append and a read both wait on the shared state lock,
  which is the guarantee the file store held directly before the extraction and
  now inherits through the guard hook. A mis-wire back to the in-memory no-op
  guard would pass every single-threaded test but fail these.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone

from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from runtime_adapters._materialized_store import MaterializedViewStoreBase
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore

_CREATED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
_ORG_A = "org-a"
_RUN_A = "run-a"
_OCCUPANCY_TABLE = "context_occupancy"


class ContextOccupancyStoreMixin:
    """Builds occupancy rows that differ only where a test needs them to."""

    def occupancy(
        self,
        *,
        model_call_id: str,
        attempt_ordinal: int = 1,
        org_id: str = _ORG_A,
        run_id: str = _RUN_A,
        graph_scope: RuntimeContextGraphScope = RuntimeContextGraphScope.ROOT,
        created_at: datetime = _CREATED_AT,
        record_id: str | None = None,
    ) -> RuntimeContextOccupancyRecord:
        return RuntimeContextOccupancyRecord.from_measurement(
            org_id=org_id,
            run_id=run_id,
            conversation_id="conversation-a",
            model_call_id=model_call_id,
            attempt_ordinal=attempt_ordinal,
            graph_scope=graph_scope,
            provider="anthropic",
            model_family="claude-opus-5",
            context_window_tokens=200_000,
            estimated_input_tokens=1_200,
            provider_input_tokens=1_180,
            segments=(),
            record_id=record_id,
            created_at=created_at,
        )


class TestBackendDifferenceHooks(ContextOccupancyStoreMixin):
    """The two hooks are the whole difference between the backends."""

    def test_in_memory_inherits_the_no_op_guard_and_sidecar(self) -> None:
        store = InMemoryRuntimeApiStore()
        # The in-memory store takes no lock: its guard is the base's no-op.
        assert isinstance(store._state_guard(), nullcontext)
        assert not hasattr(store, "_state_lock")
        # And it persists nothing: every durability sidecar is the base's no-op,
        # not an override.
        assert (
            type(store)._persist_context_occupancy
            is MaterializedViewStoreBase._persist_context_occupancy
        )
        assert (
            type(store)._persist_usage_attribution_edge
            is MaterializedViewStoreBase._persist_usage_attribution_edge
        )

    def test_file_guard_is_the_shared_state_lock(self, tmp_path) -> None:
        store = FileRuntimeApiStore(tmp_path / "guard-identity")
        # The file store serializes back-office state on exactly its own lock —
        # the object the occupancy methods held directly before the extraction.
        assert store._state_guard() is store._state_lock
        assert isinstance(store._state_lock, asyncio.Lock)

    def test_file_overrides_every_durability_sidecar(self, tmp_path) -> None:
        store = FileRuntimeApiStore(tmp_path / "sidecar-override")
        assert (
            type(store)._persist_context_occupancy
            is not MaterializedViewStoreBase._persist_context_occupancy
        )
        assert (
            type(store)._persist_usage_attribution_edge
            is not MaterializedViewStoreBase._persist_usage_attribution_edge
        )


class TestFileDurabilityEconomy(ContextOccupancyStoreMixin):
    """The JSONL sidecar writes one line per attempt, never per redelivery."""

    def _ledger_lines(self, store: FileRuntimeApiStore) -> list[str]:
        path = store.layout.state_path(_OCCUPANCY_TABLE)
        if not path.exists():
            return []
        return [line for line in path.read_text().splitlines() if line.strip()]

    async def test_redelivered_append_writes_no_second_ledger_line(
        self, tmp_path
    ) -> None:
        store = FileRuntimeApiStore(tmp_path / "durability-economy")
        await store.open()

        assert await store.append_context_occupancy(
            self.occupancy(model_call_id="call-a", record_id="row-1")
        )
        assert len(self._ledger_lines(store)) == 1

        # A redelivery of the same measured attempt under a fresh transport id is
        # a no-op: it must not append a second put line (reload folds by natural
        # key, but the append-only path should not have bloated in the first
        # place — the sidecar runs only for a genuinely new row).
        assert not await store.append_context_occupancy(
            self.occupancy(model_call_id="call-a", record_id="row-2")
        )
        assert len(self._ledger_lines(store)) == 1

        # A distinct attempt is a distinct row and a distinct line.
        assert await store.append_context_occupancy(
            self.occupancy(model_call_id="call-a", attempt_ordinal=2)
        )
        assert len(self._ledger_lines(store)) == 2
        await store.close()


class TestFileLockSemantics(ContextOccupancyStoreMixin):
    """Append and read both wait on the shared state lock after the extraction.

    Each assertion holds under *any* scheduling: while the test holds
    ``_state_lock`` the fenced task provably cannot pass the guard, so it can
    neither complete nor mutate/observe state. The ``sleep(0)`` turns exist only
    to give a mis-wired (no-op guard) implementation every chance to run ahead
    and fail the test.
    """

    async def test_append_waits_on_the_state_lock(self, tmp_path) -> None:
        store = FileRuntimeApiStore(tmp_path / "append-fence")
        await store.open()

        async with store._state_lock:
            task = asyncio.create_task(
                store.append_context_occupancy(self.occupancy(model_call_id="fenced"))
            )
            for _ in range(5):
                await asyncio.sleep(0)
            assert not task.done()
            assert store.context_occupancy == {}

        assert await task is True
        assert set(store.context_occupancy) == {("fenced", 1)}
        await store.close()

    async def test_list_waits_on_the_state_lock(self, tmp_path) -> None:
        store = FileRuntimeApiStore(tmp_path / "list-fence")
        await store.open()
        await store.append_context_occupancy(self.occupancy(model_call_id="seeded"))

        async with store._state_lock:
            task = asyncio.create_task(
                store.list_context_occupancy(org_id=_ORG_A, run_id=_RUN_A)
            )
            for _ in range(5):
                await asyncio.sleep(0)
            assert not task.done()

        rows = await task
        assert [row.model_call_id for row in rows] == ["seeded"]
        await store.close()
