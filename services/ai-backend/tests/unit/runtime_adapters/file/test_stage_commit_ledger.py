"""Durability + atomicity tests for the file-native stage-commit ledger (PRD-D2).

The desktop worker's at-most-once guarantee rests on this: a claim written BEFORE
a connector send survives a restart (a new adapter over the same root sees it), and
concurrent claims resolve to exactly one winner.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.capabilities.surfaces.commit import ConnectorCommitResult
from runtime_adapters.file.stage_commit_ledger import FileStageCommitLedger

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestClaimAtomicity:
    async def test_claim_is_once_then_lost(self, tmp_path) -> None:
        ledger = FileStageCommitLedger(root=tmp_path)
        assert await ledger.claim(commit_key="s:1:5") is True
        assert await ledger.claim(commit_key="s:1:5") is False

    async def test_concurrent_claims_yield_one_winner(self, tmp_path) -> None:
        ledger = FileStageCommitLedger(root=tmp_path)
        results = await asyncio.gather(
            *(ledger.claim(commit_key="k") for _ in range(12))
        )
        assert sum(1 for r in results if r is True) == 1

    async def test_complete_and_load_roundtrip(self, tmp_path) -> None:
        ledger = FileStageCommitLedger(root=tmp_path)
        await ledger.claim(commit_key="k")
        await ledger.complete(
            commit_key="k",
            result=ConnectorCommitResult(status="sent", external_ref="ext-9"),
        )
        entry = await ledger.load(commit_key="k")
        assert entry is not None
        assert entry.committed is True
        assert entry.result is not None and entry.result.external_ref == "ext-9"


class TestDurabilityAcrossRestart:
    async def test_claim_survives_a_fresh_adapter_over_the_same_root(
        self, tmp_path
    ) -> None:
        first = FileStageCommitLedger(root=tmp_path)
        assert await first.claim(commit_key="s:2:7") is True

        # Simulate a worker restart: a brand-new adapter over the SAME root.
        second = FileStageCommitLedger(root=tmp_path)
        # The claim is durable — the restarted worker cannot re-claim (no resend).
        assert await second.claim(commit_key="s:2:7") is False
        entry = await second.load(commit_key="s:2:7")
        assert entry is not None and entry.committed is False

    async def test_committed_result_survives_restart(self, tmp_path) -> None:
        first = FileStageCommitLedger(root=tmp_path)
        await first.claim(commit_key="k")
        await first.complete(
            commit_key="k",
            result=ConnectorCommitResult(status="sent", external_ref="ext-durable"),
        )
        second = FileStageCommitLedger(root=tmp_path)
        entry = await second.load(commit_key="k")
        assert entry is not None and entry.committed is True
        assert entry.result is not None and entry.result.external_ref == "ext-durable"


class TestMissingKey:
    async def test_load_unknown_key_is_none(self, tmp_path) -> None:
        ledger = FileStageCommitLedger(root=tmp_path)
        assert await ledger.load(commit_key="nope") is None
