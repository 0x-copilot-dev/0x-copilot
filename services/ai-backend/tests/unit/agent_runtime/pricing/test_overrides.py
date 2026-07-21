"""Unit tests for ``PricingOverrideSource`` (P12 Step 2)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_runtime.pricing.overrides import (
    DEFAULT_PRICING_VERSION,
    PricingOverrideLoadError,
    PricingOverrideSource,
)


_INGEST_AT = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


class _YamlMixin:
    """Tiny helper to write override YAML payloads in a tmp_path."""

    @staticmethod
    def write(tmp_path: Path, body: str) -> Path:
        path = tmp_path / "pricing_overrides.yaml"
        path.write_text(body)
        return path


class TestLoadBasics(_YamlMixin):
    def test_load_returns_records_with_pricing_source_override(
        self, tmp_path: Path
    ) -> None:
        path = self.write(
            tmp_path,
            """
            overrides_version: "test-2026-05"
            overrides:
              - provider: anthropic
                model_name: claude-internal-1
                region: global
                input_per_1m_micro_usd: 12_000_000
                output_per_1m_micro_usd: 60_000_000
                cached_input_per_1m_micro_usd: 1_200_000
                context_window_tokens: 200_000
                reason: "Internal fine-tune; not in LiteLLM catalog"
            """,
        )
        records = PricingOverrideSource.load_all(
            overrides_path=path, effective_from=_INGEST_AT
        )
        assert len(records) == 1
        record = records[0]
        assert record.provider == "anthropic"
        assert record.model_name == "claude-internal-1"
        assert record.region == "global"
        assert record.input_per_1m_micro_usd == 12_000_000
        assert record.output_per_1m_micro_usd == 60_000_000
        assert record.cached_input_per_1m_micro_usd == 1_200_000
        assert record.context_window_tokens == 200_000
        assert record.pricing_source == "override"
        assert record.pricing_version == "test-2026-05"

    def test_missing_file_returns_empty_tuple(self, tmp_path: Path) -> None:
        absent = tmp_path / "does_not_exist.yaml"
        records = PricingOverrideSource.load_all(overrides_path=absent)
        assert records == ()

    def test_empty_overrides_list_returns_empty(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, "overrides_version: x\noverrides: []\n")
        records = PricingOverrideSource.load_all(overrides_path=path)
        assert records == ()

    def test_default_region_is_global(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-x
                input_per_1m_micro_usd: 1
                output_per_1m_micro_usd: 2
                reason: "test region default"
            """,
        )
        records = PricingOverrideSource.load_all(overrides_path=path)
        assert records[0].region == "global"

    def test_default_pricing_version_when_unset(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-x
                input_per_1m_micro_usd: 1
                output_per_1m_micro_usd: 2
                reason: "test default version"
            """,
        )
        records = PricingOverrideSource.load_all(overrides_path=path)
        assert records[0].pricing_version == DEFAULT_PRICING_VERSION


class TestEffectiveFrom(_YamlMixin):
    def test_effective_from_from_yaml_used_when_present(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-x
                effective_from: 2025-12-15T00:00:00Z
                input_per_1m_micro_usd: 1_000
                output_per_1m_micro_usd: 2_000
                reason: "back-dated override"
            """,
        )
        records = PricingOverrideSource.load_all(
            overrides_path=path, effective_from=_INGEST_AT
        )
        assert records[0].effective_from == datetime(2025, 12, 15, tzinfo=timezone.utc)

    def test_effective_from_falls_back_to_ingest_when_absent(
        self, tmp_path: Path
    ) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-x
                input_per_1m_micro_usd: 1
                output_per_1m_micro_usd: 2
                reason: "no explicit effective_from"
            """,
        )
        records = PricingOverrideSource.load_all(
            overrides_path=path, effective_from=_INGEST_AT
        )
        assert records[0].effective_from == _INGEST_AT


class TestReasonRequired(_YamlMixin):
    def test_missing_reason_rejected(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-x
                input_per_1m_micro_usd: 1
                output_per_1m_micro_usd: 2
            """,
        )
        with pytest.raises(PricingOverrideLoadError, match="reason"):
            PricingOverrideSource.load_all(overrides_path=path)

    def test_empty_reason_rejected(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-x
                input_per_1m_micro_usd: 1
                output_per_1m_micro_usd: 2
                reason: "   "
            """,
        )
        with pytest.raises(PricingOverrideLoadError, match="reason"):
            PricingOverrideSource.load_all(overrides_path=path)


class TestMalformed(_YamlMixin):
    def test_root_must_be_mapping(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, "- just\n- a list\n")
        with pytest.raises(PricingOverrideLoadError, match="root must be"):
            PricingOverrideSource.load_all(overrides_path=path)

    def test_overrides_must_be_list(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, "overrides: not-a-list\n")
        with pytest.raises(PricingOverrideLoadError, match="must be a list"):
            PricingOverrideSource.load_all(overrides_path=path)

    def test_entry_must_be_mapping(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - "not-a-mapping"
            """,
        )
        with pytest.raises(PricingOverrideLoadError, match="must be mappings"):
            PricingOverrideSource.load_all(overrides_path=path)

    def test_missing_provider_rejected(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - model_name: x
                input_per_1m_micro_usd: 1
                output_per_1m_micro_usd: 2
                reason: "test"
            """,
        )
        with pytest.raises(PricingOverrideLoadError, match="'provider'"):
            PricingOverrideSource.load_all(overrides_path=path)

    def test_missing_input_rate_rejected(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-x
                output_per_1m_micro_usd: 2
                reason: "test"
            """,
        )
        with pytest.raises(PricingOverrideLoadError, match="input_per_1m_micro_usd"):
            PricingOverrideSource.load_all(overrides_path=path)

    def test_invalid_yaml_rejected(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, "overrides: [\n  invalid yaml syntax")
        with pytest.raises(PricingOverrideLoadError, match="failed to parse"):
            PricingOverrideSource.load_all(overrides_path=path)


class TestByKey(_YamlMixin):
    def test_by_key_indexes_records_by_triple(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            overrides:
              - provider: anthropic
                model_name: claude-x
                input_per_1m_micro_usd: 1
                output_per_1m_micro_usd: 2
                reason: "first"
              - provider: openai
                model_name: gpt-x
                region: us
                input_per_1m_micro_usd: 3
                output_per_1m_micro_usd: 4
                reason: "second"
            """,
        )
        records = PricingOverrideSource.load_all(overrides_path=path)
        index = PricingOverrideSource.by_key(records)
        assert set(index.keys()) == {
            ("anthropic", "claude-x", "global"),
            ("openai", "gpt-x", "us"),
        }


class TestRealOverrideFile:
    """The shipped override backstop must load clean.

    Post-litellm-cutover the file holds a single entry: ``gemini-3-flash``,
    the only product model LiteLLM 1.93.0 does not price. The stale migration
    entries were dropped (LiteLLM carries their real provider prices).
    """

    def test_backstop_override_file_loads(self) -> None:
        records = PricingOverrideSource.load_all()
        keys = {(r.provider, r.model_name, r.region) for r in records}
        # gemini-3-flash is the sole remaining override; the canonical slug is
        # ``gemini`` (not ``google``).
        assert ("gemini", "gemini-3-flash", "global") in keys
        # The dropped stale entries must be gone.
        assert ("anthropic", "claude-opus-4-7", "global") not in keys
        assert ("openai", "gpt-5", "global") not in keys

    def test_every_override_has_a_reason(self) -> None:
        # The reason field is the audit trail. If any entry is missing it
        # the loader raises — so a successful load transitively asserts the
        # precondition. Each record routes through PricingOverrideSource, so
        # ``pricing_source`` is "override" (never silently empty).
        records = PricingOverrideSource.load_all()
        assert len(records) > 0
        assert all(r.pricing_source == "override" for r in records)
