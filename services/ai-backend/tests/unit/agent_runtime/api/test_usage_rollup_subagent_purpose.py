"""Tests for the subagent + purpose rollup builders (Sub-PRD 01d)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


from agent_runtime.api.usage_service import UsageQueryService
from agent_runtime.observability.attribution import Purpose
from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
)


def _call(
    *,
    org_id: str = "org_1",
    run_id: str = "run_1",
    subagent_id: str | None = None,
    purpose: str = "main",
    model_provider: str = "openai",
    model_name: str = "gpt-5.4-mini",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cached_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    audio_input_tokens: int = 0,
    audio_output_tokens: int = 0,
    cost_micro_usd: int | None = None,
    created_at: datetime | None = None,
) -> RuntimeModelCallUsageRecord:
    return RuntimeModelCallUsageRecord(
        id=uuid4().hex,
        org_id=org_id,
        run_id=run_id,
        conversation_id="conv_1",
        trace_id="trace_1",
        subagent_id=subagent_id,
        purpose=purpose,
        model_provider=model_provider,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        reasoning_tokens=reasoning_tokens,
        audio_input_tokens=audio_input_tokens,
        audio_output_tokens=audio_output_tokens,
        total_tokens=(
            input_tokens
            + output_tokens
            + reasoning_tokens
            + audio_input_tokens
            + audio_output_tokens
        ),
        cost_micro_usd=cost_micro_usd,
        created_at=created_at or datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),
    )


def _refreshed_at() -> datetime:
    return datetime(2026, 5, 11, 12, 30, tzinfo=timezone.utc)


class TestSubagentRollup:
    def test_groups_by_org_day_subagent_model(self) -> None:
        rows = UsageQueryService.rollup_subagent_rows(
            [
                _call(subagent_id="researcher", input_tokens=100, output_tokens=20),
                _call(subagent_id="researcher", input_tokens=50, output_tokens=10),
                _call(subagent_id="writer", input_tokens=30, output_tokens=8),
            ],
            refreshed_at=_refreshed_at(),
        )
        by_slug = {row.subagent_slug: row for row in rows}
        assert by_slug["researcher"].input_tokens == 150
        assert by_slug["researcher"].output_tokens == 30
        assert by_slug["researcher"].call_count == 2
        assert by_slug["writer"].call_count == 1

    def test_none_subagent_collapses_to_empty_slug(self) -> None:
        rows = UsageQueryService.rollup_subagent_rows(
            [_call(subagent_id=None, input_tokens=42)],
            refreshed_at=_refreshed_at(),
        )
        assert len(rows) == 1
        assert rows[0].subagent_slug == ""

    def test_sums_every_token_kind(self) -> None:
        rows = UsageQueryService.rollup_subagent_rows(
            [
                _call(
                    subagent_id="researcher",
                    input_tokens=100,
                    cached_input_tokens=40,
                    cache_creation_input_tokens=20,
                    reasoning_tokens=80,
                    audio_input_tokens=5,
                    audio_output_tokens=3,
                    output_tokens=10,
                ),
            ],
            refreshed_at=_refreshed_at(),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.cached_input_tokens == 40
        assert row.cache_creation_input_tokens == 20
        assert row.reasoning_tokens == 80
        assert row.audio_input_tokens == 5
        assert row.audio_output_tokens == 3

    def test_different_models_dont_merge(self) -> None:
        rows = UsageQueryService.rollup_subagent_rows(
            [
                _call(subagent_id="researcher", model_name="gpt-5.4"),
                _call(subagent_id="researcher", model_name="claude-3.7"),
            ],
            refreshed_at=_refreshed_at(),
        )
        assert len(rows) == 2

    def test_different_days_dont_merge(self) -> None:
        day_a = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
        day_b = day_a + timedelta(days=1)
        rows = UsageQueryService.rollup_subagent_rows(
            [
                _call(subagent_id="researcher", created_at=day_a),
                _call(subagent_id="researcher", created_at=day_b),
            ],
            refreshed_at=_refreshed_at(),
        )
        assert len(rows) == 2

    def test_cost_sums_when_present(self) -> None:
        rows = UsageQueryService.rollup_subagent_rows(
            [
                _call(subagent_id="researcher", cost_micro_usd=1000),
                _call(subagent_id="researcher", cost_micro_usd=500),
            ],
            refreshed_at=_refreshed_at(),
        )
        assert rows[0].cost_micro_usd == 1500

    def test_cost_none_when_all_inputs_none(self) -> None:
        rows = UsageQueryService.rollup_subagent_rows(
            [
                _call(subagent_id="researcher", cost_micro_usd=None),
                _call(subagent_id="researcher", cost_micro_usd=None),
            ],
            refreshed_at=_refreshed_at(),
        )
        assert rows[0].cost_micro_usd is None


class TestPurposeRollup:
    def test_groups_by_purpose(self) -> None:
        rows = UsageQueryService.rollup_purpose_rows(
            [
                _call(purpose=Purpose.MAIN.value, input_tokens=100),
                _call(purpose=Purpose.MAIN.value, input_tokens=50),
                _call(purpose=Purpose.TOOL_INTERPRETATION.value, input_tokens=200),
                _call(purpose=Purpose.SUBAGENT_WORK.value, input_tokens=30),
            ],
            refreshed_at=_refreshed_at(),
        )
        by_purpose = {row.purpose: row for row in rows}
        assert by_purpose[Purpose.MAIN.value].input_tokens == 150
        assert by_purpose[Purpose.MAIN.value].call_count == 2
        assert by_purpose[Purpose.TOOL_INTERPRETATION.value].input_tokens == 200
        assert by_purpose[Purpose.SUBAGENT_WORK.value].input_tokens == 30

    def test_all_purpose_values_supported(self) -> None:
        rows = UsageQueryService.rollup_purpose_rows(
            [_call(purpose=p.value) for p in Purpose],
            refreshed_at=_refreshed_at(),
        )
        purposes = {row.purpose for row in rows}
        assert purposes == {p.value for p in Purpose}

    def test_default_purpose_main(self) -> None:
        # Records that pre-date 01b have purpose='main' as column default;
        # rollup must honor that bucket.
        rows = UsageQueryService.rollup_purpose_rows(
            [_call(purpose="main")],
            refreshed_at=_refreshed_at(),
        )
        assert rows[0].purpose == "main"


class TestConnectorRollupExtendedKey:
    def test_connector_rollup_includes_model_name_in_key(self) -> None:
        # Sub-PRD 01d: connector rollup key now includes model_name so
        # the same connector with different models lands in separate rows.
        rows = UsageQueryService.rollup_connector_rows(
            [
                _call(model_name="gpt-5.4"),
                _call(model_name="claude-3.7"),
            ],
            run_user_lookup={"run_1": "user_1"},
            refreshed_at=_refreshed_at(),
        )
        assert len(rows) == 2
        models = {row.model_name for row in rows}
        assert models == {"gpt-5.4", "claude-3.7"}
