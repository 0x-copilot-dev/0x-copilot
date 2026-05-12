"""C8 sweeper-loop tests with a fake PersistencePort."""

from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.observability.retention_metrics import RetentionMetrics
from agent_runtime.persistence.records.retention import (
    RetentionDeletionEvidenceRecord,
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
    RetentionSweepOutcome,
)
from runtime_worker.jobs.retention_sweeper import RetentionSweeperLoop


class _FakePersistence:
    """Minimal stub of ``PersistencePort`` for sweeper tests."""

    def __init__(
        self,
        *,
        orgs: tuple[str, ...] = ("org_a", "org_b"),
        policies: dict[str, tuple[RetentionPolicyRecord, ...]] | None = None,
        sweep_outcome: RetentionSweepOutcome | None = None,
    ) -> None:
        self._orgs = orgs
        self._policies = policies or {}
        self._sweep_outcome = sweep_outcome
        self.sweep_calls: list[dict[str, Any]] = []
        self.evidence_rows: list[RetentionDeletionEvidenceRecord] = []

    async def list_retention_orgs(self) -> tuple[str, ...]:
        return self._orgs

    async def list_retention_policies(
        self, *, org_id: str
    ) -> tuple[RetentionPolicyRecord, ...]:
        return self._policies.get(org_id, ())

    async def sweep_retention_kind(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        dry_run: bool = False,
    ) -> RetentionSweepOutcome:
        self.sweep_calls.append(
            {
                "org_id": org_id,
                "kind": kind,
                "ttl_seconds": ttl_seconds,
                "dry_run": dry_run,
            }
        )
        if self._sweep_outcome is not None:
            return self._sweep_outcome.model_copy(
                update={"org_id": org_id, "kind": kind}
            )
        return RetentionSweepOutcome(org_id=org_id, kind=kind, tombstoned=1)

    async def insert_retention_deletion_evidence(
        self, record: RetentionDeletionEvidenceRecord
    ) -> None:
        self.evidence_rows.append(record)


class _SpyMetrics(RetentionMetrics):
    """RetentionMetrics subclass that captures calls for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.swept_rows_calls: list[dict[str, Any]] = []
        self.duration_calls: list[dict[str, Any]] = []

    def record_swept_rows(
        self, *, kind: str, action: str, count: int, dry_run: bool
    ) -> None:
        self.swept_rows_calls.append(
            {"kind": kind, "action": action, "count": count, "dry_run": dry_run}
        )

    def record_sweep_duration(self, *, kind: str, elapsed_seconds: float) -> None:
        self.duration_calls.append({"kind": kind, "elapsed_seconds": elapsed_seconds})


class TestRetentionSweeperLoop:
    @pytest.mark.asyncio
    async def test_iterates_orgs_and_kinds(self) -> None:
        persistence = _FakePersistence(orgs=("org_a", "org_b"))
        loop = RetentionSweeperLoop(persistence=persistence)
        outcomes = await loop.sweep_once()
        # Each org × every kind that has a non-None TTL (defaults give
        # MESSAGES + EVENTS); CONTEXT_PAYLOADS is always invoked since
        # its retention is column-driven not TTL-driven.
        org_kinds = {(call["org_id"], call["kind"]) for call in persistence.sweep_calls}
        assert ("org_a", RetentionKind.MESSAGES) in org_kinds
        assert ("org_b", RetentionKind.MESSAGES) in org_kinds
        assert ("org_a", RetentionKind.EVENTS) in org_kinds
        assert ("org_a", RetentionKind.CONTEXT_PAYLOADS) in org_kinds
        # Defaults skip checkpoints/memory_items (TTL=None and not the
        # column-driven kind).
        assert ("org_a", RetentionKind.CHECKPOINTS) not in org_kinds
        assert ("org_a", RetentionKind.MEMORY_ITEMS) not in org_kinds
        assert len(outcomes) >= 6  # 2 orgs × 3 kinds

    @pytest.mark.asyncio
    async def test_per_org_policy_overrides_default(self) -> None:
        # org_a policy sets MESSAGES TTL to 1h; resolver passes it to sweep_kind.
        org_a_policy = RetentionPolicyRecord(
            org_id="org_a",
            scope=RetentionScope.ORG,
            resource_id=None,
            kind=RetentionKind.MESSAGES,
            ttl_seconds=3600,
        )
        persistence = _FakePersistence(
            orgs=("org_a",),
            policies={"org_a": (org_a_policy,)},
        )
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        messages_calls = [
            call
            for call in persistence.sweep_calls
            if call["kind"] is RetentionKind.MESSAGES
        ]
        assert len(messages_calls) == 1
        assert messages_calls[0]["ttl_seconds"] == 3600

    @pytest.mark.asyncio
    async def test_dry_run_propagates_to_adapter(self) -> None:
        persistence = _FakePersistence(orgs=("org_a",))
        loop = RetentionSweeperLoop(persistence=persistence, dry_run=True)
        await loop.sweep_once()
        assert all(call["dry_run"] for call in persistence.sweep_calls)

    @pytest.mark.asyncio
    async def test_sweep_kind_failure_doesnt_break_loop(self) -> None:
        class _BoomPersistence(_FakePersistence):
            async def sweep_retention_kind(self, **kwargs):  # type: ignore[no-untyped-def]
                if kwargs["kind"] is RetentionKind.MESSAGES:
                    raise RuntimeError("KMS unavailable")
                return await super().sweep_retention_kind(**kwargs)

        persistence = _BoomPersistence(orgs=("org_a",))
        loop = RetentionSweeperLoop(persistence=persistence)
        # Must not raise; the loop logs and proceeds.
        outcomes = await loop.sweep_once()
        # EVENTS + CONTEXT_PAYLOADS still ran.
        kinds = {outcome.kind for outcome in outcomes}
        assert RetentionKind.EVENTS in kinds
        assert RetentionKind.CONTEXT_PAYLOADS in kinds


class TestRetentionSweeperPhase1Evidence:
    """Phase 1 acceptance criteria: evidence rows + OTel metrics."""

    @pytest.mark.asyncio
    async def test_evidence_row_inserted_on_non_empty_tombstone(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.MESSAGES,
                tombstoned=5,
            ),
        )
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        assert len(persistence.evidence_rows) > 0
        row = next(
            r for r in persistence.evidence_rows if r.kind is RetentionKind.MESSAGES
        )
        assert row.org_id == "org_a"
        assert row.tombstoned == 5
        assert row.dry_run is False

    @pytest.mark.asyncio
    async def test_evidence_row_inserted_on_non_empty_delete(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.CONTEXT_PAYLOADS,
                deleted=3,
            ),
        )
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        row = next(
            (
                r
                for r in persistence.evidence_rows
                if r.kind is RetentionKind.CONTEXT_PAYLOADS
            ),
            None,
        )
        assert row is not None
        assert row.deleted == 3

    @pytest.mark.asyncio
    async def test_evidence_row_not_inserted_on_zero_outcome(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.MESSAGES,
                tombstoned=0,
                deleted=0,
                skipped_legal_hold=0,
            ),
        )
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        assert len(persistence.evidence_rows) == 0

    @pytest.mark.asyncio
    async def test_dry_run_writes_evidence_row_with_dry_run_flag(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.MESSAGES,
                tombstoned=2,
            ),
        )
        loop = RetentionSweeperLoop(persistence=persistence, dry_run=True)
        await loop.sweep_once()
        messages_rows = [
            r for r in persistence.evidence_rows if r.kind is RetentionKind.MESSAGES
        ]
        assert len(messages_rows) > 0
        assert all(r.dry_run is True for r in messages_rows)

    @pytest.mark.asyncio
    async def test_evidence_row_for_legal_hold_skip(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.MESSAGES,
                tombstoned=0,
                deleted=0,
                skipped_legal_hold=4,
            ),
        )
        loop = RetentionSweeperLoop(persistence=persistence)
        await loop.sweep_once()
        rows = [
            r for r in persistence.evidence_rows if r.kind is RetentionKind.MESSAGES
        ]
        assert len(rows) > 0
        assert rows[0].skipped_legal_hold == 4

    @pytest.mark.asyncio
    async def test_otel_counter_emitted_per_tombstone_outcome(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.MESSAGES,
                tombstoned=7,
            ),
        )
        spy = _SpyMetrics()
        loop = RetentionSweeperLoop(persistence=persistence, metrics=spy)
        await loop.sweep_once()
        tombstone_calls = [
            c for c in spy.swept_rows_calls if c["action"] == "tombstone"
        ]
        assert any(c["kind"] == "messages" and c["count"] == 7 for c in tombstone_calls)

    @pytest.mark.asyncio
    async def test_otel_counter_emitted_per_delete_outcome(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.CONTEXT_PAYLOADS,
                deleted=10,
            ),
        )
        spy = _SpyMetrics()
        loop = RetentionSweeperLoop(persistence=persistence, metrics=spy)
        await loop.sweep_once()
        delete_calls = [c for c in spy.swept_rows_calls if c["action"] == "delete"]
        assert any(
            c["kind"] == "context_payloads" and c["count"] == 10 for c in delete_calls
        )

    @pytest.mark.asyncio
    async def test_otel_duration_emitted_per_sweep_call(self) -> None:
        persistence = _FakePersistence(orgs=("org_a",))
        spy = _SpyMetrics()
        loop = RetentionSweeperLoop(persistence=persistence, metrics=spy)
        await loop.sweep_once()
        swept_kinds = {c["kind"] for c in spy.duration_calls}
        assert "messages" in swept_kinds
        assert "events" in swept_kinds
        assert "context_payloads" in swept_kinds

    @pytest.mark.asyncio
    async def test_no_counter_emitted_on_zero_outcome(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a",),
            sweep_outcome=RetentionSweepOutcome(
                org_id="org_a",
                kind=RetentionKind.MESSAGES,
                tombstoned=0,
                deleted=0,
                skipped_legal_hold=0,
            ),
        )
        spy = _SpyMetrics()
        loop = RetentionSweeperLoop(persistence=persistence, metrics=spy)
        await loop.sweep_once()
        assert len(spy.swept_rows_calls) == 0

    @pytest.mark.asyncio
    async def test_evidence_insert_failure_does_not_crash_loop(self) -> None:
        class _BoomEvidence(_FakePersistence):
            async def insert_retention_deletion_evidence(self, record):  # type: ignore[override]
                raise RuntimeError("DB unavailable")

        persistence = _BoomEvidence(orgs=("org_a",))
        loop = RetentionSweeperLoop(persistence=persistence)
        outcomes = await loop.sweep_once()
        assert len(outcomes) > 0
