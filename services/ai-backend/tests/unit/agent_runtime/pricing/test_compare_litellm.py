"""Unit tests for the ``compare_litellm`` parity CLI (P12 Step 1)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.compare_litellm import compare, main


_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(
    *,
    provider: str = "anthropic",
    model_name: str = "claude-sonnet-4-6",
    input_per_1m: int = 3_000_000,
    output_per_1m: int = 15_000_000,
    cached: int | None = 300_000,
    context: int | None = 1_000_000,
    pricing_source: str = "yaml-seed",
) -> ModelPricingRecord:
    return ModelPricingRecord(
        provider=provider,
        model_name=model_name,
        region="global",
        effective_from=_EFFECTIVE_FROM,
        input_per_1m_micro_usd=input_per_1m,
        output_per_1m_micro_usd=output_per_1m,
        cached_input_per_1m_micro_usd=cached,
        context_window_tokens=context,
        pricing_source=pricing_source,
        pricing_version="test.v1",
    )


class TestMatch:
    def test_exact_match_reports_match(self) -> None:
        seeds = (_record(),)
        litellm_rows = (_record(pricing_source="litellm"),)
        rows = compare(seeds=seeds, litellm_records=litellm_rows)
        assert len(rows) == 1
        assert rows[0].status == "match"
        assert rows[0].differences == {}

    def test_within_tolerance_reports_match(self) -> None:
        seeds = (_record(input_per_1m=3_000_000),)
        # 0.05% drift — within default 0.1% tolerance
        litellm_rows = (_record(input_per_1m=3_001_500, pricing_source="litellm"),)
        rows = compare(seeds=seeds, litellm_records=litellm_rows)
        assert rows[0].status == "match"


class TestDivergent:
    def test_outside_tolerance_reports_divergent(self) -> None:
        seeds = (_record(input_per_1m=15_000_000),)
        # LiteLLM has $5/1M vs our $15/1M — the real-world claude-opus-4-7 case
        litellm_rows = (_record(input_per_1m=5_000_000, pricing_source="litellm"),)
        rows = compare(seeds=seeds, litellm_records=litellm_rows)
        assert rows[0].status == "divergent"
        assert rows[0].differences["input_per_1m_micro_usd"]["seed"] == 15_000_000
        assert rows[0].differences["input_per_1m_micro_usd"]["litellm"] == 5_000_000

    def test_context_window_must_match_exactly(self) -> None:
        seeds = (_record(context=1_000_000),)
        litellm_rows = (_record(context=999_999, pricing_source="litellm"),)
        rows = compare(seeds=seeds, litellm_records=litellm_rows)
        assert rows[0].status == "divergent"
        assert "context_window_tokens" in rows[0].differences

    def test_cached_none_vs_int_is_divergent(self) -> None:
        seeds = (_record(cached=300_000),)
        litellm_rows = (_record(cached=None, pricing_source="litellm"),)
        rows = compare(seeds=seeds, litellm_records=litellm_rows)
        assert rows[0].status == "divergent"
        assert "cached_input_per_1m_micro_usd" in rows[0].differences

    def test_tolerance_argument_widens_match_window(self) -> None:
        seeds = (_record(input_per_1m=3_000_000),)
        # 3.3% drift — outside default 0.1%, inside 5%
        litellm_rows = (_record(input_per_1m=3_100_000, pricing_source="litellm"),)
        default = compare(seeds=seeds, litellm_records=litellm_rows)
        widened = compare(
            seeds=seeds, litellm_records=litellm_rows, tolerance=Decimal("0.05")
        )
        assert default[0].status == "divergent"
        assert widened[0].status == "match"


class TestMissing:
    def test_seed_with_no_litellm_row_reports_missing(self) -> None:
        seeds = (_record(model_name="claude-internal-finetune"),)
        rows = compare(seeds=seeds, litellm_records=())
        assert len(rows) == 1
        assert rows[0].status == "missing_in_litellm"
        assert rows[0].litellm_present is False

    def test_litellm_only_rows_are_not_reported(self) -> None:
        # The comparison is keyed on seeds; LiteLLM-only models don't appear.
        # That's intentional: the report's question is "is our hand-curated
        # source aligned with upstream", not "what's new upstream".
        seeds = ()
        litellm_rows = (_record(pricing_source="litellm"),)
        rows = compare(seeds=seeds, litellm_records=litellm_rows)
        assert rows == ()


class TestCLIExitCodes:
    def test_main_exits_zero_when_every_seed_matches(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.PricingSeedLoader.load_all",
            classmethod(lambda cls: (_record(),)),
        )
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.LiteLLMPricingSource.load_all",
            classmethod(lambda cls, **kw: (_record(pricing_source="litellm"),)),
        )
        rc = main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "match" in out

    def test_main_exits_one_on_divergence(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.PricingSeedLoader.load_all",
            classmethod(lambda cls: (_record(input_per_1m=15_000_000),)),
        )
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.LiteLLMPricingSource.load_all",
            classmethod(
                lambda cls, **kw: (
                    _record(input_per_1m=5_000_000, pricing_source="litellm"),
                )
            ),
        )
        rc = main([])
        out = capsys.readouterr().out
        assert rc == 1
        assert "divergent" in out

    def test_main_emits_json(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.PricingSeedLoader.load_all",
            classmethod(lambda cls: (_record(),)),
        )
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.LiteLLMPricingSource.load_all",
            classmethod(lambda cls, **kw: (_record(pricing_source="litellm"),)),
        )
        rc = main(["--json"])
        out = capsys.readouterr().out
        import json

        payload = json.loads(out)
        assert rc == 0
        assert isinstance(payload, list)
        assert payload[0]["status"] == "match"

    def test_main_tolerance_flag_widens_window(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.PricingSeedLoader.load_all",
            classmethod(lambda cls: (_record(input_per_1m=3_000_000),)),
        )
        monkeypatch.setattr(
            "agent_runtime.pricing.compare_litellm.LiteLLMPricingSource.load_all",
            classmethod(
                lambda cls, **kw: (
                    _record(input_per_1m=3_050_000, pricing_source="litellm"),
                )
            ),
        )
        rc_strict = main([])
        rc_lenient = main(["--tolerance", "0.05"])
        assert rc_strict == 1
        assert rc_lenient == 0
