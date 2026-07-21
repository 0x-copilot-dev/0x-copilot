"""Bounded-growth compaction of the file store's raw queue op-log.

The queue (``state/queue.jsonl``) is not a ``StateLedger`` — it is a raw op-log
(an ``enqueue`` plus a ``status``/``attempts`` op per claim, then a terminal
status). Completed / dead-lettered commands are never pruned, so both boot
replay and *every* ``claim_next`` scan grow O(history). Boot compaction folds it
to only the live (non-terminal) commands. These tests drive the queue via raw
ops (the on-disk contract `_load_queue_from_disk` reconstructs) and pin: the
bloat→compact→live-preserved fold (order, status, attempts, available_at),
that `claim_next` still works after compaction, the kill switch and threshold,
and that a rewrite failure never bricks `open()`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_runtime.persistence.records.common import OutboxStatus
from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore

_PAST = "2020-01-01T00:00:00+00:00"  # available_at in the past → claimable


def _lines(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8")) if path.exists() else 0


def _payload(cid: str) -> dict:
    return {
        "command_id": cid,
        "command_type": "run_requested",
        "org_id": "org_1",
        "run_id": f"run_{cid}",
        "approval_id": None,
    }


class QueueBloatMixin:
    @staticmethod
    def _qpath(root: Path) -> Path:
        FileStoreLayout(root).ensure_scaffold()
        return FileStoreLayout(root).state_path("queue")

    @classmethod
    def _enqueue(cls, path: Path, cid: str) -> None:
        JsonlIo.append_line(
            path,
            {
                "op": "enqueue",
                "command_id": cid,
                "payload": _payload(cid),
                "available_at": _PAST,
            },
        )

    @classmethod
    def _status(cls, path: Path, cid: str, status: str) -> None:
        JsonlIo.append_line(
            path,
            {
                "op": "status",
                "command_id": cid,
                "status": status,
                "available_at": _PAST,
            },
        )

    @classmethod
    def _attempts(cls, path: Path, cid: str, attempts: int) -> None:
        JsonlIo.append_line(
            path, {"op": "attempts", "command_id": cid, "attempts": attempts}
        )

    @classmethod
    def _seed_bloated_queue(cls, path: Path, *, completed: int = 20) -> None:
        # `completed` terminal commands (2 lines each) + 2 pending + 1 retry(+attempts).
        for i in range(completed):
            cid = f"done_{i}"
            cls._enqueue(path, cid)
            cls._status(path, cid, OutboxStatus.COMPLETED.value)
        cls._enqueue(path, "pend_a")
        cls._enqueue(path, "pend_b")
        cls._enqueue(path, "retry_c")
        cls._status(path, "retry_c", OutboxStatus.RETRY.value)
        cls._attempts(path, "retry_c", 2)


class TestQueueCompaction(QueueBloatMixin):
    async def test_bloated_queue_folds_to_live_commands(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        qpath = self._qpath(root)
        self._seed_bloated_queue(qpath)
        before = _lines(qpath)  # 20*2 + 2 + 3 = 45

        store = FileRuntimeApiStore(root)
        await store.open()  # compaction runs on the way up
        try:
            # In-memory: terminals dropped, live subset preserved IN ORDER.
            assert store._queue_order == ["pend_a", "pend_b", "retry_c"]
            assert store._queue_statuses["pend_a"] is OutboxStatus.PENDING
            assert store._queue_statuses["retry_c"] is OutboxStatus.RETRY
            assert store._queue_attempts["retry_c"] == 2
            assert "done_0" not in store._queue_statuses
            # On disk: 2 pending (1 line) + retry (enqueue+status+attempts = 3) = 5.
            assert _lines(qpath) == 5
            assert _lines(qpath) < before
        finally:
            await store.close()

        # Durable across a fresh reopen at the same root.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        try:
            assert reopened._queue_order == ["pend_a", "pend_b", "retry_c"]
            assert reopened._queue_statuses["retry_c"] is OutboxStatus.RETRY
            assert reopened._queue_attempts["retry_c"] == 2
        finally:
            await reopened.close()

    async def test_claim_next_still_works_after_compaction(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        qpath = self._qpath(root)
        self._seed_bloated_queue(qpath)

        store = FileRuntimeApiStore(root)
        await store.open()
        try:
            claim = await store.claim_next(
                worker_id="w1",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            assert claim is not None
            assert claim.command_id == "pend_a"  # first live command, junk skipped
            assert claim.run_id == "run_pend_a"
        finally:
            await store.close()

    async def test_kill_switch_leaves_queue_intact(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        qpath = self._qpath(root)
        self._seed_bloated_queue(qpath)
        before = _lines(qpath)

        store = FileRuntimeApiStore(root, compaction_enabled=False)
        await store.open()
        try:
            assert _lines(qpath) == before  # untouched
            assert "done_0" in store._queue_statuses  # terminals retained
            assert store._queue_order.count("pend_a") == 1
        finally:
            await store.close()

    async def test_small_queue_below_threshold_untouched(self, tmp_path) -> None:
        root = tmp_path / "store"
        qpath = self._qpath(root)
        for cid in ("a", "b", "c"):
            self._enqueue(qpath, cid)  # 3 lines, real threshold (256) → no-op
        before = _lines(qpath)
        store = FileRuntimeApiStore(root)
        await store.open()
        try:
            assert _lines(qpath) == before
        finally:
            await store.close()

    async def test_rewrite_failure_never_bricks_open(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        qpath = self._qpath(root)
        self._seed_bloated_queue(qpath)
        before = _lines(qpath)

        def _boom(self, _live):
            raise OSError("disk full")

        monkeypatch.setattr(FileRuntimeApiStore, "_rewrite_queue_ledger", _boom)

        store = FileRuntimeApiStore(root)
        await store.open()  # must NOT raise
        try:
            assert _lines(qpath) == before  # un-compacted log preserved + valid
            assert store._queue_order.count("pend_a") == 1  # live intact
            assert "done_0" in store._queue_statuses  # not pruned (compaction failed)
        finally:
            await store.close()
