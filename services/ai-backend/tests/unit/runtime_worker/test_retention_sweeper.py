"""C8 sweeper-loop tests with a fake AsyncPersistencePort."""

from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
    RetentionSweepOutcome,
)
from runtime_worker.jobs.retention_sweeper import RetentionSweeperLoop


class _FakePersistence:
    """Minimal stub of ``AsyncPersistencePort`` for sweeper tests."""

    def __init__(
        self,
        *,
        orgs: tuple[str, ...] = ("org_a", "org_b"),
        policies: dict[str, tuple[RetentionPolicyRecord, ...]] | None = None,
    ) -> None:
        self._orgs = orgs
        self._policies = policies or {}
        self.sweep_calls: list[dict[str, Any]] = []

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
        return RetentionSweepOutcome(org_id=org_id, kind=kind, tombstoned=1)


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
