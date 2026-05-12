"""C8 Phase 5 — grace-period hard-delete tests.

Covers:
  1. Grace=0 (default): TOMBSTONED kinds are skipped entirely (no adapter call).
  2. Grace>0: sweeper calls adapter with ttl_seconds = grace_days * 86400.
  3. Grace>0 + non-zero rows: outcome accumulated and evidence written.
  4. Dry-run with grace>0: one adapter call only.
  5. All three tombstoned kinds (MESSAGES_TOMBSTONED, EVENTS_TOMBSTONED,
     MEMORY_ITEMS_TOMBSTONED) follow the same grace-period gate.
  6. Grace env vars are read correctly (RETENTION_TOMBSTONE_GRACE_DAYS_*).
  7. Explicit constructor params override env vars.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionSweepOutcome,
)
from runtime_worker.jobs.retention_sweeper import (
    RetentionSweeperLoop,
    RetentionSweeperLoopEnv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePersistence:
    def __init__(
        self,
        *,
        orgs: tuple[str, ...] = ("org_a",),
        sweep_sequence: dict[str, list[tuple[int, int]]] | None = None,
    ) -> None:
        self._orgs = orgs
        self._sweep_sequence = sweep_sequence or {}
        self._call_counts: dict[str, int] = {}
        self.sweep_calls: list[dict[str, Any]] = []
        self.evidence_calls: list[Any] = []

    async def list_retention_orgs(self) -> tuple[str, ...]:
        return self._orgs

    async def list_retention_policies(
        self, *, org_id: str
    ) -> tuple[RetentionPolicyRecord, ...]:
        return ()

    async def sweep_retention_kind(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        dry_run: bool = False,
        chunk_size: int = 0,
    ) -> RetentionSweepOutcome:
        call = {
            "org_id": org_id,
            "kind": kind,
            "ttl_seconds": ttl_seconds,
            "dry_run": dry_run,
            "chunk_size": chunk_size,
        }
        self.sweep_calls.append(call)
        key = f"{org_id}:{kind.value}"
        n = self._call_counts.get(key, 0)
        self._call_counts[key] = n + 1
        seq = self._sweep_sequence.get(key, [(0, 0)])
        tombstoned, deleted = seq[n] if n < len(seq) else (0, 0)
        return RetentionSweepOutcome(
            org_id=org_id, kind=kind, tombstoned=tombstoned, deleted=deleted
        )

    async def insert_retention_deletion_evidence(self, record: Any) -> None:
        self.evidence_calls.append(record)


def _tombstoned_calls(
    persistence: _FakePersistence, kind: RetentionKind
) -> list[dict[str, Any]]:
    return [c for c in persistence.sweep_calls if c["kind"] is kind]


# ---------------------------------------------------------------------------
# Grace = 0 (default): all TOMBSTONED kinds skipped
# ---------------------------------------------------------------------------


class TestGraceZeroSkips:
    @pytest.mark.asyncio
    async def test_messages_tombstoned_skipped_by_default(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        assert _tombstoned_calls(persistence, RetentionKind.MESSAGES_TOMBSTONED) == []

    @pytest.mark.asyncio
    async def test_events_tombstoned_skipped_by_default(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        assert _tombstoned_calls(persistence, RetentionKind.EVENTS_TOMBSTONED) == []

    @pytest.mark.asyncio
    async def test_memory_items_tombstoned_skipped_by_default(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        assert (
            _tombstoned_calls(persistence, RetentionKind.MEMORY_ITEMS_TOMBSTONED) == []
        )

    @pytest.mark.asyncio
    async def test_explicit_grace_zero_skips(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(
            persistence=persistence,
            grace_days_messages=0,
            grace_days_events=0,
            grace_days_memory_items=0,
        )
        await loop.sweep_once()
        tombstoned_kinds = {
            RetentionKind.MESSAGES_TOMBSTONED,
            RetentionKind.EVENTS_TOMBSTONED,
            RetentionKind.MEMORY_ITEMS_TOMBSTONED,
        }
        for call in persistence.sweep_calls:
            assert call["kind"] not in tombstoned_kinds


# ---------------------------------------------------------------------------
# Grace > 0: adapter called with correct ttl_seconds
# ---------------------------------------------------------------------------


class TestGracePositiveCallsAdapter:
    @pytest.mark.asyncio
    async def test_messages_tombstoned_ttl_seconds(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(persistence=persistence, grace_days_messages=30)
        await loop.sweep_once()
        calls = _tombstoned_calls(persistence, RetentionKind.MESSAGES_TOMBSTONED)
        assert len(calls) >= 1
        assert calls[0]["ttl_seconds"] == 30 * 86400

    @pytest.mark.asyncio
    async def test_events_tombstoned_ttl_seconds(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(persistence=persistence, grace_days_events=7)
        await loop.sweep_once()
        calls = _tombstoned_calls(persistence, RetentionKind.EVENTS_TOMBSTONED)
        assert len(calls) >= 1
        assert calls[0]["ttl_seconds"] == 7 * 86400

    @pytest.mark.asyncio
    async def test_memory_items_tombstoned_ttl_seconds(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(persistence=persistence, grace_days_memory_items=14)
        await loop.sweep_once()
        calls = _tombstoned_calls(persistence, RetentionKind.MEMORY_ITEMS_TOMBSTONED)
        assert len(calls) >= 1
        assert calls[0]["ttl_seconds"] == 14 * 86400

    @pytest.mark.asyncio
    async def test_chunk_size_passed_to_adapter(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(
            persistence=persistence, grace_days_messages=30, chunk_size=200
        )
        await loop.sweep_once()
        calls = _tombstoned_calls(persistence, RetentionKind.MESSAGES_TOMBSTONED)
        assert all(c["chunk_size"] == 200 for c in calls)


# ---------------------------------------------------------------------------
# Loop terminates + accumulates correctly
# ---------------------------------------------------------------------------


class TestGracePeriodLoop:
    @pytest.mark.asyncio
    async def test_loop_terminates_when_adapter_returns_zero(self) -> None:
        # Sequence: 50 deleted, 50 deleted, 0 → 3 calls total.
        persistence = _FakePersistence(
            sweep_sequence={"org_a:messages_tombstoned": [(0, 50), (0, 50), (0, 0)]}
        )
        loop = RetentionSweeperLoop(
            persistence=persistence, grace_days_messages=30, chunk_size=50
        )
        outcomes = await loop.sweep_once()
        calls = _tombstoned_calls(persistence, RetentionKind.MESSAGES_TOMBSTONED)
        assert len(calls) == 3
        msg_outcome = next(
            o for o in outcomes if o.kind is RetentionKind.MESSAGES_TOMBSTONED
        )
        assert msg_outcome.deleted == 100

    @pytest.mark.asyncio
    async def test_evidence_written_for_non_zero_hard_delete(self) -> None:
        persistence = _FakePersistence(
            sweep_sequence={"org_a:messages_tombstoned": [(0, 10), (0, 0)]}
        )
        loop = RetentionSweeperLoop(persistence=persistence, grace_days_messages=30)
        await loop.sweep_once()
        evidence = [
            e
            for e in persistence.evidence_calls
            if e.kind is RetentionKind.MESSAGES_TOMBSTONED
        ]
        assert len(evidence) == 1
        assert evidence[0].deleted == 10

    @pytest.mark.asyncio
    async def test_no_evidence_when_zero_rows(self) -> None:
        persistence = _FakePersistence()
        loop = RetentionSweeperLoop(persistence=persistence, grace_days_messages=30)
        await loop.sweep_once()
        evidence = [
            e
            for e in persistence.evidence_calls
            if e.kind is RetentionKind.MESSAGES_TOMBSTONED
        ]
        assert len(evidence) == 0


# ---------------------------------------------------------------------------
# Dry-run: one adapter call only
# ---------------------------------------------------------------------------


class TestGraceDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_calls_adapter_exactly_once(self) -> None:
        persistence = _FakePersistence(
            sweep_sequence={"org_a:messages_tombstoned": [(0, 50), (0, 50), (0, 0)]}
        )
        loop = RetentionSweeperLoop(
            persistence=persistence,
            grace_days_messages=30,
            chunk_size=50,
            dry_run=True,
        )
        await loop.sweep_once()
        calls = _tombstoned_calls(persistence, RetentionKind.MESSAGES_TOMBSTONED)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_dry_run_evidence_flagged(self) -> None:
        persistence = _FakePersistence(
            sweep_sequence={"org_a:messages_tombstoned": [(0, 5), (0, 0)]}
        )
        loop = RetentionSweeperLoop(
            persistence=persistence, grace_days_messages=30, dry_run=True
        )
        await loop.sweep_once()
        evidence = [
            e
            for e in persistence.evidence_calls
            if e.kind is RetentionKind.MESSAGES_TOMBSTONED
        ]
        assert len(evidence) > 0
        assert all(e.dry_run is True for e in evidence)


# ---------------------------------------------------------------------------
# Env-var configuration
# ---------------------------------------------------------------------------


class TestGraceEnvVars:
    def test_grace_days_messages_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv(RetentionSweeperLoopEnv.GRACE_DAYS_MESSAGES, "30")
        loop = RetentionSweeperLoop(persistence=_FakePersistence())
        from agent_runtime.persistence.records.retention import RetentionKind

        assert loop._grace_days[RetentionKind.MESSAGES_TOMBSTONED] == 30

    def test_grace_days_events_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv(RetentionSweeperLoopEnv.GRACE_DAYS_EVENTS, "14")
        loop = RetentionSweeperLoop(persistence=_FakePersistence())
        assert loop._grace_days[RetentionKind.EVENTS_TOMBSTONED] == 14

    def test_grace_days_memory_items_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv(RetentionSweeperLoopEnv.GRACE_DAYS_MEMORY_ITEMS, "7")
        loop = RetentionSweeperLoop(persistence=_FakePersistence())
        assert loop._grace_days[RetentionKind.MEMORY_ITEMS_TOMBSTONED] == 7

    def test_explicit_param_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv(RetentionSweeperLoopEnv.GRACE_DAYS_MESSAGES, "30")
        loop = RetentionSweeperLoop(
            persistence=_FakePersistence(), grace_days_messages=45
        )
        assert loop._grace_days[RetentionKind.MESSAGES_TOMBSTONED] == 45

    def test_default_grace_is_zero(self) -> None:
        loop = RetentionSweeperLoop(persistence=_FakePersistence())
        assert loop._grace_days[RetentionKind.MESSAGES_TOMBSTONED] == 0
        assert loop._grace_days[RetentionKind.EVENTS_TOMBSTONED] == 0
        assert loop._grace_days[RetentionKind.MEMORY_ITEMS_TOMBSTONED] == 0
