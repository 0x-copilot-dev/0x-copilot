"""C8 Phase 2 — RetentionBackfillJob tests."""

from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
)
from runtime_worker.jobs.retention_backfill import RetentionBackfillJob


class _FakePersistence:
    """Minimal stub that tracks backfill_retention_until calls."""

    def __init__(
        self,
        *,
        orgs: tuple[str, ...] = ("org_a",),
        policies: dict[str, tuple[RetentionPolicyRecord, ...]] | None = None,
        backfill_return_sequence: dict[str, list[int]] | None = None,
    ) -> None:
        self._orgs = orgs
        self._policies = policies or {}
        # Per-key sequence of return values; key = "<org>:<kind>".
        # Default: return 0 on first call (nothing to backfill).
        self._backfill_return_sequence = backfill_return_sequence or {}
        self._backfill_call_counts: dict[str, int] = {}
        self.backfill_calls: list[dict[str, Any]] = []

    async def list_retention_orgs(self) -> tuple[str, ...]:
        return self._orgs

    async def list_retention_policies(
        self, *, org_id: str
    ) -> tuple[RetentionPolicyRecord, ...]:
        return self._policies.get(org_id, ())

    async def backfill_retention_until(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        chunk_size: int,
    ) -> int:
        self.backfill_calls.append(
            {
                "org_id": org_id,
                "kind": kind,
                "ttl_seconds": ttl_seconds,
                "chunk_size": chunk_size,
            }
        )
        key = f"{org_id}:{kind.value}"
        call_no = self._backfill_call_counts.get(key, 0)
        self._backfill_call_counts[key] = call_no + 1
        seq = self._backfill_return_sequence.get(key, [0])
        return seq[call_no] if call_no < len(seq) else 0


def _policy(
    org_id: str,
    kind: RetentionKind,
    ttl_seconds: int,
    scope: RetentionScope = RetentionScope.ORG,
) -> RetentionPolicyRecord:
    return RetentionPolicyRecord(
        org_id=org_id,
        scope=scope,
        resource_id=None,
        kind=kind,
        ttl_seconds=ttl_seconds,
    )


class TestRetentionBackfillJob:
    @pytest.mark.asyncio
    async def test_calls_backfill_for_kinds_with_policy(self) -> None:
        persistence = _FakePersistence(
            policies={
                "org_a": (
                    _policy("org_a", RetentionKind.MESSAGES, 86400),
                    _policy("org_a", RetentionKind.EVENTS, 86400),
                )
            }
        )
        job = RetentionBackfillJob(persistence=persistence, chunk_size=500)
        await job.run()
        kinds_called = {c["kind"] for c in persistence.backfill_calls}
        assert RetentionKind.MESSAGES in kinds_called
        assert RetentionKind.EVENTS in kinds_called

    @pytest.mark.asyncio
    async def test_skips_kind_with_no_policy_and_no_default(self) -> None:
        # MEMORY_ITEMS has no deployment default — no policy → no backfill.
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 3600),)}
        )
        job = RetentionBackfillJob(persistence=persistence, chunk_size=100)
        await job.run()
        kinds_called = {c["kind"] for c in persistence.backfill_calls}
        assert RetentionKind.MEMORY_ITEMS not in kinds_called
        assert RetentionKind.CONTEXT_PAYLOADS not in kinds_called
        assert RetentionKind.CHECKPOINTS not in kinds_called

    @pytest.mark.asyncio
    async def test_loops_until_zero_rows(self) -> None:
        # Adapter returns 100, 100, 0 for MESSAGES → job makes 3 calls.
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)},
            backfill_return_sequence={"org_a:messages": [100, 100, 0]},
        )
        job = RetentionBackfillJob(persistence=persistence, chunk_size=100)
        totals = await job.run()
        messages_calls = [
            c for c in persistence.backfill_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert len(messages_calls) == 3
        assert totals.get("org_a:messages") == 200

    @pytest.mark.asyncio
    async def test_returns_zero_when_nothing_to_backfill(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)},
        )
        job = RetentionBackfillJob(persistence=persistence, chunk_size=100)
        totals = await job.run()
        assert totals.get("org_a:messages") == 0

    @pytest.mark.asyncio
    async def test_propagates_ttl_from_policy(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 7200),)}
        )
        job = RetentionBackfillJob(persistence=persistence, chunk_size=100)
        await job.run()
        messages_calls = [
            c for c in persistence.backfill_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert messages_calls[0]["ttl_seconds"] == 7200

    @pytest.mark.asyncio
    async def test_propagates_chunk_size(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)}
        )
        job = RetentionBackfillJob(persistence=persistence, chunk_size=42)
        await job.run()
        assert all(c["chunk_size"] == 42 for c in persistence.backfill_calls)

    @pytest.mark.asyncio
    async def test_iterates_multiple_orgs(self) -> None:
        persistence = _FakePersistence(
            orgs=("org_a", "org_b"),
            policies={
                "org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),),
                "org_b": (_policy("org_b", RetentionKind.MESSAGES, 3600),),
            },
        )
        job = RetentionBackfillJob(persistence=persistence, chunk_size=100)
        await job.run()
        orgs_called = {c["org_id"] for c in persistence.backfill_calls}
        assert "org_a" in orgs_called
        assert "org_b" in orgs_called

    @pytest.mark.asyncio
    async def test_uses_deployment_default_when_no_policy(self) -> None:
        # No explicit MESSAGES policy → deployment default is 365 days → backfill fires.
        persistence = _FakePersistence(policies={})
        job = RetentionBackfillJob(persistence=persistence, chunk_size=100)
        await job.run()
        messages_calls = [
            c for c in persistence.backfill_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        # Deployment default = 365 days = 31_536_000 seconds
        assert len(messages_calls) == 1
        assert messages_calls[0]["ttl_seconds"] == 31_536_000
