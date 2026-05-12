"""C8 Phase 4 — chunked retention_until sweep tests.

Covers:
  1. Phase 4 (use_retention_until=True): sweeper passes chunk_size > 0 to
     the adapter; loop terminates when adapter returns 0 rows.
  2. Phase 4 loop accumulates counts across chunks correctly.
  3. Phase 4 dry-run: issues one chunk only (does not loop).
  4. Phase 4 skips CHECKPOINTS when no policy and no default.
  5. Phase 4 still resolves ttl_seconds for CHECKPOINTS.
  6. Legacy (use_retention_until=False): single-pass, chunk_size=0, resolver
     used for all kinds.
  7. Legacy: kinds with no ttl and no default are skipped.
  8. Feature-flag default is True (opt-in to Phase 4).
  9. Env-var RETENTION_SWEEP_USE_RETENTION_UNTIL=false enables legacy path.
 10. chunk_size is read from RETENTION_SWEEP_CHUNK env var.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
    RetentionSweepOutcome,
)
from runtime_worker.jobs.retention_sweeper import (
    RetentionSweeperLoop,
    RetentionSweeperLoopEnv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


class _FakePersistence:
    """Stub that records sweep calls and drives a configurable sequence."""

    def __init__(
        self,
        *,
        orgs: tuple[str, ...] = ("org_a",),
        policies: dict[str, tuple[RetentionPolicyRecord, ...]] | None = None,
        # Maps "<org>:<kind>" → list of (tombstoned, deleted) per call.
        sweep_sequence: dict[str, list[tuple[int, int]]] | None = None,
    ) -> None:
        self._orgs = orgs
        self._policies = policies or {}
        self._sweep_sequence = sweep_sequence or {}
        self._sweep_call_counts: dict[str, int] = {}
        self.sweep_calls: list[dict[str, Any]] = []
        self.evidence_calls: list[Any] = []

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
        n = self._sweep_call_counts.get(key, 0)
        self._sweep_call_counts[key] = n + 1
        seq = self._sweep_sequence.get(key, [(0, 0)])
        tombstoned, deleted = seq[n] if n < len(seq) else (0, 0)
        return RetentionSweepOutcome(
            org_id=org_id,
            kind=kind,
            tombstoned=tombstoned,
            deleted=deleted,
        )

    async def insert_retention_deletion_evidence(self, record) -> None:
        self.evidence_calls.append(record)


# ---------------------------------------------------------------------------
# Phase 4 — use_retention_until=True (default)
# ---------------------------------------------------------------------------


class TestPhase4ChunkedSweep:
    @pytest.mark.asyncio
    async def test_passes_chunk_size_to_adapter(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)}
        )
        loop = RetentionSweeperLoop(
            persistence=persistence,
            use_retention_until=True,
            chunk_size=42,
        )
        await loop.sweep_once()
        messages_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert all(c["chunk_size"] == 42 for c in messages_calls)

    @pytest.mark.asyncio
    async def test_loop_terminates_when_adapter_returns_zero(self) -> None:
        # Sequence: 100, 100, 0 → 3 calls total
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)},
            sweep_sequence={"org_a:messages": [(100, 0), (100, 0), (0, 0)]},
        )
        loop = RetentionSweeperLoop(
            persistence=persistence, use_retention_until=True, chunk_size=100
        )
        outcomes = await loop.sweep_once()
        messages_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert len(messages_calls) == 3
        # Returned outcome accumulates all chunks
        msg_outcome = next(o for o in outcomes if o.kind is RetentionKind.MESSAGES)
        assert msg_outcome.tombstoned == 200

    @pytest.mark.asyncio
    async def test_dry_run_calls_adapter_exactly_once(self) -> None:
        # Even with non-zero rows, dry-run must stop after one chunk.
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)},
            sweep_sequence={"org_a:messages": [(50, 0), (50, 0), (0, 0)]},
        )
        loop = RetentionSweeperLoop(
            persistence=persistence,
            use_retention_until=True,
            chunk_size=50,
            dry_run=True,
        )
        await loop.sweep_once()
        messages_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert len(messages_calls) == 1

    @pytest.mark.asyncio
    async def test_checkpoints_uses_resolved_ttl(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.CHECKPOINTS, 7200),)}
        )
        loop = RetentionSweeperLoop(
            persistence=persistence, use_retention_until=True, chunk_size=100
        )
        await loop.sweep_once()
        cp_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.CHECKPOINTS
        ]
        assert len(cp_calls) >= 1
        assert cp_calls[0]["ttl_seconds"] == 7200

    @pytest.mark.asyncio
    async def test_checkpoints_skipped_when_no_policy_and_no_default(self) -> None:
        # CHECKPOINTS has no deployment default → skip when no policy set.
        persistence = _FakePersistence(policies={})
        loop = RetentionSweeperLoop(
            persistence=persistence, use_retention_until=True, chunk_size=100
        )
        await loop.sweep_once()
        cp_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.CHECKPOINTS
        ]
        assert len(cp_calls) == 0

    @pytest.mark.asyncio
    async def test_non_checkpoint_kinds_pass_zero_ttl(self) -> None:
        # MESSAGES/EVENTS/MEMORY_ITEMS don't need ttl in Phase 4.
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)}
        )
        loop = RetentionSweeperLoop(
            persistence=persistence, use_retention_until=True, chunk_size=100
        )
        await loop.sweep_once()
        messages_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert messages_calls[0]["ttl_seconds"] == 0

    @pytest.mark.asyncio
    async def test_context_payloads_always_swept(self) -> None:
        # CONTEXT_PAYLOADS sweeps even with no policy (column-driven).
        persistence = _FakePersistence(policies={})
        loop = RetentionSweeperLoop(
            persistence=persistence, use_retention_until=True, chunk_size=100
        )
        await loop.sweep_once()
        cp_calls = [
            c
            for c in persistence.sweep_calls
            if c["kind"] is RetentionKind.CONTEXT_PAYLOADS
        ]
        assert len(cp_calls) >= 1

    @pytest.mark.asyncio
    async def test_evidence_written_for_non_zero_total(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)},
            sweep_sequence={"org_a:messages": [(10, 0), (0, 0)]},
        )
        loop = RetentionSweeperLoop(
            persistence=persistence, use_retention_until=True, chunk_size=100
        )
        await loop.sweep_once()
        evidence = [
            e for e in persistence.evidence_calls if e.kind is RetentionKind.MESSAGES
        ]
        assert len(evidence) == 1
        assert evidence[0].tombstoned == 10

    @pytest.mark.asyncio
    async def test_no_evidence_when_zero_rows(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)},
        )
        loop = RetentionSweeperLoop(
            persistence=persistence, use_retention_until=True, chunk_size=100
        )
        await loop.sweep_once()
        evidence = [
            e for e in persistence.evidence_calls if e.kind is RetentionKind.MESSAGES
        ]
        assert len(evidence) == 0


# ---------------------------------------------------------------------------
# Legacy path — use_retention_until=False
# ---------------------------------------------------------------------------


class TestLegacySweep:
    @pytest.mark.asyncio
    async def test_passes_chunk_size_zero(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)}
        )
        loop = RetentionSweeperLoop(persistence=persistence, use_retention_until=False)
        await loop.sweep_once()
        messages_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert all(c["chunk_size"] == 0 for c in messages_calls)

    @pytest.mark.asyncio
    async def test_single_pass_only(self) -> None:
        # Legacy path calls sweep once per kind, never loops.
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 86400),)},
            sweep_sequence={"org_a:messages": [(100, 0), (100, 0)]},
        )
        loop = RetentionSweeperLoop(persistence=persistence, use_retention_until=False)
        await loop.sweep_once()
        messages_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert len(messages_calls) == 1

    @pytest.mark.asyncio
    async def test_skips_kind_with_no_ttl(self) -> None:
        # No MEMORY_ITEMS policy and no default → legacy path skips it.
        persistence = _FakePersistence(policies={})
        loop = RetentionSweeperLoop(persistence=persistence, use_retention_until=False)
        await loop.sweep_once()
        memory_calls = [
            c
            for c in persistence.sweep_calls
            if c["kind"] is RetentionKind.MEMORY_ITEMS
        ]
        assert len(memory_calls) == 0

    @pytest.mark.asyncio
    async def test_uses_resolved_ttl(self) -> None:
        persistence = _FakePersistence(
            policies={"org_a": (_policy("org_a", RetentionKind.MESSAGES, 3600),)}
        )
        loop = RetentionSweeperLoop(persistence=persistence, use_retention_until=False)
        await loop.sweep_once()
        messages_calls = [
            c for c in persistence.sweep_calls if c["kind"] is RetentionKind.MESSAGES
        ]
        assert messages_calls[0]["ttl_seconds"] == 3600


# ---------------------------------------------------------------------------
# Feature-flag and env-var behaviour
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    def test_default_is_phase4(self) -> None:
        # Default: use_retention_until=True (Phase 4 on by default).
        loop = RetentionSweeperLoop(persistence=_FakePersistence(), chunk_size=100)
        assert loop._use_retention_until is True

    def test_env_false_enables_legacy(self, monkeypatch) -> None:
        monkeypatch.setenv(RetentionSweeperLoopEnv.USE_RETENTION_UNTIL, "false")
        loop = RetentionSweeperLoop(persistence=_FakePersistence())
        assert loop._use_retention_until is False

    def test_chunk_size_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv(RetentionSweeperLoopEnv.CHUNK_SIZE, "250")
        loop = RetentionSweeperLoop(persistence=_FakePersistence())
        assert loop._chunk_size == 250

    def test_explicit_chunk_size_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv(RetentionSweeperLoopEnv.CHUNK_SIZE, "250")
        loop = RetentionSweeperLoop(persistence=_FakePersistence(), chunk_size=99)
        assert loop._chunk_size == 99
