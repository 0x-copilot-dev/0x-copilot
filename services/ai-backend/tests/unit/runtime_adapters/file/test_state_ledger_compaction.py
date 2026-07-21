"""Bounded-growth compaction of the append-with-fold state ledgers.

The file-native store is the desktop DEFAULT and is long-lived. Its back-office
"state" ledgers (usage, approvals, budgets, …) are append-only: every re-put and
tombstone stays on disk forever and is replayed on every ``open()`` — a
compounding O(history) boot cost. Compaction folds a bloated ledger back to its
live set at boot, reusing the proven crash-safe ``StateLedger.rewrite`` (temp →
fsync → ``os.replace``). These tests pin: the primitive, the threshold policy,
that a bloated log is compacted while its folded state is preserved, that the
kill switch and threshold hold it back, that the audit log is never folded
(compliance), and that a torn compaction ``.tmp`` can never corrupt committed
state.
"""

from __future__ import annotations

from pathlib import Path

from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas.workspace_defaults import WorkspaceDefaultsRecord

_WS = "workspace_defaults"


def _lines(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8")) if path.exists() else 0


class TestStateLedgerPrimitive:
    def test_line_count_tracks_appends_and_drops_on_rewrite(self, tmp_path) -> None:
        ledger = StateLedger(tmp_path / "t.jsonl")
        for i in range(300):
            ledger.append_put({"id": "x", "v": i})  # same key: 300 lines, 1 live
        assert ledger.line_count == 300

        ledger.rewrite([{"id": "x", "v": 299}])
        assert ledger.line_count == 1

        # A fresh ledger reads the compacted file and reports the new size.
        reloaded = StateLedger(tmp_path / "t.jsonl")
        assert reloaded.load_ops() == [("put", {"id": "x", "v": 299})]
        assert reloaded.line_count == 1


class TestShouldCompactThreshold:
    def test_ratio_and_floor(self) -> None:
        f = FileRuntimeApiStore._should_compact
        assert f(300, 1) is True  # bloated: 300 ≥ 256 and 300 ≥ 2×1
        assert f(255, 1) is False  # below the line floor
        assert f(300, 200) is False  # 300 < 2×200 — not bloated enough
        assert f(300, 100) is True  # 300 ≥ 256 and 300 ≥ 2×100
        assert f(0, 0) is False

    def test_production_defaults_are_conservative(self) -> None:
        assert FileRuntimeApiStore._COMPACT_MIN_LINES >= 64
        assert FileRuntimeApiStore._COMPACT_RATIO >= 2


class BootCompactionMixin:
    @staticmethod
    async def _bloat_workspace_defaults(
        store: FileRuntimeApiStore, *, org_id: str, times: int
    ) -> None:
        for _ in range(times):
            await store.upsert_workspace_defaults(
                record=WorkspaceDefaultsRecord(org_id=org_id)
            )


class TestBootCompaction(BootCompactionMixin):
    async def test_bloated_fold_ledger_compacts_and_preserves_state(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        path = FileStoreLayout(root).state_path(_WS)

        store = FileRuntimeApiStore(root)
        await store.open()
        await self._bloat_workspace_defaults(store, org_id="org_x", times=20)
        await store.close()
        assert _lines(path) == 20  # 20 re-puts, 1 live row

        # Reopen: compaction folds the log to its live set on the way up.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        try:
            assert _lines(path) == 1  # folded to the single live row
            assert list(reopened.workspace_defaults.keys()) == ["org_x"]
        finally:
            await reopened.close()

        # Stable: a second reopen is at/below threshold and leaves it alone.
        again = FileRuntimeApiStore(root)
        await again.open()
        try:
            assert _lines(path) == 1
        finally:
            await again.close()

    async def test_kill_switch_leaves_log_intact(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        path = FileStoreLayout(root).state_path(_WS)

        store = FileRuntimeApiStore(root)
        await store.open()
        await self._bloat_workspace_defaults(store, org_id="org_x", times=20)
        await store.close()

        disabled = FileRuntimeApiStore(root, compaction_enabled=False)
        await disabled.open()
        try:
            assert _lines(path) == 20  # untouched with compaction off
            assert list(disabled.workspace_defaults.keys()) == ["org_x"]
        finally:
            await disabled.close()

    async def test_small_store_below_real_threshold_untouched(self, tmp_path) -> None:
        # Real (unpatched) threshold: a handful of rows must never be rewritten.
        root = tmp_path / "store"
        path = FileStoreLayout(root).state_path(_WS)
        store = FileRuntimeApiStore(root)
        await store.open()
        for i in range(5):
            await store.upsert_workspace_defaults(
                record=WorkspaceDefaultsRecord(org_id=f"org_{i}")
            )
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        try:
            assert _lines(path) == 5
        finally:
            await reopened.close()

    async def test_audit_log_is_never_compacted(self, tmp_path, monkeypatch) -> None:
        # The audit log is append-only immutable evidence — excluded from the
        # compaction set even when bloated far past the (lowered) threshold.
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 4)
        root = tmp_path / "store"
        FileStoreLayout(root).ensure_scaffold()
        audit_path = FileStoreLayout(root).state_path("audit_log")
        audit = StateLedger(audit_path)
        for i in range(30):
            audit.append_put(
                {"event_type": "test", "record": {"org_id": "o", "seq": i}}
            )
        assert _lines(audit_path) == 30

        store = FileRuntimeApiStore(root)
        await store.open()
        try:
            assert _lines(audit_path) == 30  # untouched: append-only evidence
        finally:
            await store.close()

    async def test_torn_compaction_tmp_cannot_corrupt_state(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        path = FileStoreLayout(root).state_path(_WS)

        store = FileRuntimeApiStore(root)
        await store.open()
        await self._bloat_workspace_defaults(store, org_id="org_x", times=20)
        await store.close()

        # Simulate a crash mid-compaction: a torn temp beside the committed log.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text('{"op":"put","record":{"org_id":"GARB', encoding="utf-8")

        reopened = FileRuntimeApiStore(root)
        await reopened.open()  # ignores the torn .tmp; reads only the committed log
        try:
            # Live state is exactly the committed row — never the torn temp.
            assert list(reopened.workspace_defaults.keys()) == ["org_x"]
            assert _lines(path) == 1  # clean re-compaction replaced the temp
        finally:
            await reopened.close()

    async def test_compaction_failure_never_bricks_open(
        self, tmp_path, monkeypatch
    ) -> None:
        # Compaction is best-effort maintenance: a transient rewrite failure
        # (disk full, IO error) must NOT fail store open(). The store opens with
        # the un-compacted — still valid — log, and the next boot retries.
        monkeypatch.setattr(FileRuntimeApiStore, "_COMPACT_MIN_LINES", 8)
        root = tmp_path / "store"
        path = FileStoreLayout(root).state_path(_WS)

        store = FileRuntimeApiStore(root)
        await store.open()
        await self._bloat_workspace_defaults(store, org_id="org_x", times=20)
        await store.close()

        def _boom(self, _records):
            raise OSError("disk full")

        monkeypatch.setattr(StateLedger, "rewrite", _boom)

        reopened = FileRuntimeApiStore(root)
        await reopened.open()  # must NOT raise despite the failing rewrite
        try:
            assert list(reopened.workspace_defaults.keys()) == ["org_x"]  # intact
            assert _lines(path) == 20  # un-compacted log preserved + readable
        finally:
            await reopened.close()
