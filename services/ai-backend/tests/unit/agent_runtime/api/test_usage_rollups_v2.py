"""PRD-A2 D7 — rollup totals equal the sum of seeded rows, in BOTH adapters.

Seeds the in-memory store and the file store (tmp root) with per-call + per-run
usage rows across purposes (including the new ``view_shaping``), then proves the
UsageQueryService rollups and the store's per-run / per-conversation queries add
up to the seeded fixture. This is the "both adapters" half of the DoD (the
postgres twin is ``tests/integration/persistence/test_usage_rollup_v2_pg.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agent_runtime.api.usage_service import UsageQueryService
from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
)
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore

_ORG = "org_a"
_USER = "user_1"
_CONV = "conv-1"
_WINDOW_START = datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc)
_WINDOW_END = datetime(2026, 5, 11, 23, 59, tzinfo=timezone.utc)
_TS = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)

# (purpose, input, output) per seeded call row. Two view_shaping rows on purpose
# so the per-purpose bucket sums more than one row.
_CALL_FIXTURE: tuple[tuple[str, int, int], ...] = (
    ("main", 100, 40),
    ("tool_planning", 50, 10),
    ("subagent_work", 30, 12),
    ("view_shaping", 120, 48),
    ("view_shaping", 200, 60),
    ("todo_extraction", 15, 5),
)

# (run_id, input, output) per seeded run row (all in _CONV).
_RUN_FIXTURE: tuple[tuple[str, int, int], ...] = (
    ("run-1", 180, 62),
    ("run-2", 130, 48),
)


def _call_row(
    *, purpose: str, input_tokens: int, output_tokens: int
) -> RuntimeModelCallUsageRecord:
    return RuntimeModelCallUsageRecord(
        id=uuid4().hex,
        org_id=_ORG,
        run_id="run-1",
        conversation_id=_CONV,
        trace_id="trace-1",
        user_id=_USER,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        created_at=_TS,
    )


def _run_row(
    *, run_id: str, input_tokens: int, output_tokens: int
) -> RuntimeRunUsageRecord:
    return RuntimeRunUsageRecord(
        id=run_id,
        org_id=_ORG,
        user_id=_USER,
        conversation_id=_CONV,
        run_id=run_id,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        started_at=_TS,
        completed_at=_TS,
        status="completed",
    )


class UsageRollupV2Mixin:
    """Seed a store + assert rollups equal the seeded fixture (adapter-agnostic)."""

    @staticmethod
    async def _seed(store: object) -> None:
        for purpose, in_tok, out_tok in _CALL_FIXTURE:
            await store.record_model_call_usage(
                _call_row(purpose=purpose, input_tokens=in_tok, output_tokens=out_tok)
            )
        for run_id, in_tok, out_tok in _RUN_FIXTURE:
            await store.record_run_usage(
                _run_row(run_id=run_id, input_tokens=in_tok, output_tokens=out_tok)
            )

    @classmethod
    async def _assert_rollups(cls, store: object) -> None:
        refreshed = datetime(2026, 5, 11, 12, 30, tzinfo=timezone.utc)

        # --- per-purpose rollup equals the sum of seeded call rows -----------
        call_rows = await store.query_model_call_usage_for_range(
            org_id=_ORG, start=_WINDOW_START, end=_WINDOW_END
        )
        assert len(call_rows) == len(_CALL_FIXTURE)
        purpose_rows = UsageQueryService.rollup_purpose_rows(
            call_rows, refreshed_at=refreshed
        )
        by_purpose = {row.purpose: row for row in purpose_rows}

        expected_in = sum(i for _, i, _ in _CALL_FIXTURE)
        expected_out = sum(o for _, _, o in _CALL_FIXTURE)
        assert sum(r.input_tokens for r in purpose_rows) == expected_in
        assert sum(r.output_tokens for r in purpose_rows) == expected_out

        # The new view_shaping bucket exists and sums BOTH seeded rows.
        assert "view_shaping" in by_purpose
        assert by_purpose["view_shaping"].input_tokens == 120 + 200
        assert by_purpose["view_shaping"].output_tokens == 48 + 60
        assert by_purpose["view_shaping"].call_count == 2

        # --- per-user rollup equals the sum of seeded run rows ---------------
        run_rows = await store.query_run_usage_for_range(
            org_id=_ORG, user_id=_USER, start=_WINDOW_START, end=_WINDOW_END
        )
        user_rows = UsageQueryService.rollup_user_rows(run_rows, refreshed_at=refreshed)
        assert sum(r.input_tokens for r in user_rows) == sum(
            i for _, i, _ in _RUN_FIXTURE
        )
        assert sum(r.output_tokens for r in user_rows) == sum(
            o for _, _, o in _RUN_FIXTURE
        )

        # --- per-run query equals that run's seeded row ----------------------
        run1 = await store.query_run_usage(org_id=_ORG, run_id="run-1")
        assert run1 is not None
        assert run1.input_tokens == 180
        assert run1.output_tokens == 62

        # --- per-conversation totals equal the sum of the conv's run rows ----
        conversations = await store.query_top_conversations(
            org_id=_ORG,
            user_id=_USER,
            start=_WINDOW_START,
            end=_WINDOW_END,
            limit=10,
        )
        by_conv = {row.conversation_id: row for row in conversations}
        assert _CONV in by_conv
        assert by_conv[_CONV].input_tokens == 180 + 130
        assert by_conv[_CONV].output_tokens == 62 + 48
        assert by_conv[_CONV].runs_count == 2


class TestUsageRollupV2(UsageRollupV2Mixin):
    async def test_rollup_totals_equal_sum_of_rows_in_memory(self) -> None:
        store = InMemoryRuntimeApiStore()
        await self._seed(store)
        await self._assert_rollups(store)

    async def test_rollup_totals_equal_sum_of_rows_file_store(
        self, tmp_path: Path
    ) -> None:
        store = FileRuntimeApiStore(tmp_path / "usage")
        await store.open()
        try:
            await self._seed(store)
            await self._assert_rollups(store)
        finally:
            await store.close()

    async def test_purpose_rollup_has_view_shaping_bucket_in_memory(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.record_model_call_usage(
            _call_row(purpose="view_shaping", input_tokens=42, output_tokens=7)
        )
        call_rows = await store.query_model_call_usage_for_range(
            org_id=_ORG, start=_WINDOW_START, end=_WINDOW_END
        )
        rows = UsageQueryService.rollup_purpose_rows(
            call_rows, refreshed_at=datetime(2026, 5, 11, 12, 30, tzinfo=timezone.utc)
        )
        assert [r.purpose for r in rows] == ["view_shaping"]
        assert rows[0].input_tokens == 42
