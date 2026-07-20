"""ModelsDevCatalogSource — tier fallback, TTL/backoff, parse hygiene.

Every test is offline (httpx.MockTransport / tmp_path files) and
deterministic (FakeClock). The invariants under test:

* records() never raises and never blocks on the network;
* tier order is live -> fresh-enough cache -> vendored snapshot -> empty,
  and a STALE cache still beats the snapshot (recency of real data wins
  over freshness of shipped data);
* only the six supported providers survive parsing, ``google`` maps to
  our ``gemini``, malformed rows are skipped without poisoning siblings;
* failed refreshes back off (RETRY_INTERVAL) and successes hold for the
  cache TTL.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.api.models_dev_source import (
    CatalogTier,
    ModelsDevCatalogSource,
)

from tests.unit.agent_runtime.api.models_dev_fixtures import (
    FakeClock,
    ModelsDevFixtureMixin,
)


class TestOfflineTiers(ModelsDevFixtureMixin):
    def test_snapshot_tier_parses_supported_providers_only(
        self, tmp_path: Path
    ) -> None:
        source = self.source_with_snapshot(tmp_path)
        records = source.records()
        assert source.current_tier() is CatalogTier.SNAPSHOT
        assert len(records) == self.EXPECTED_RECORD_COUNT
        providers = {record.provider for record in records}
        # google maps to our runtime name; unsupported providers vanish.
        assert "gemini" in providers
        assert "google" not in providers
        assert "mistral" not in providers
        # The malformed openai row is skipped without losing its siblings.
        openai_ids = {r.model_id for r in records if r.provider == "openai"}
        assert openai_ids == {"gpt-test-pro", "gpt-test-mini"}

    def test_metadata_fields_are_mapped(self, tmp_path: Path) -> None:
        source = self.source_with_snapshot(tmp_path)
        by_id = {record.model_id: record for record in source.records()}
        pro = by_id["gpt-test-pro"]
        assert pro.display_name == "GPT Test Pro"
        assert pro.context_window == 400_000
        assert pro.max_output_tokens == 128_000
        assert pro.input_cost_per_mtok == 1.25
        assert pro.output_cost_per_mtok == 10.0
        assert pro.supports_reasoning is True
        assert pro.supports_tools is True
        assert pro.supports_attachments is True
        assert pro.release_date == "2026-01-02"
        assert "image" in pro.input_modalities

    def test_stale_cache_still_beats_snapshot(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        self.write_cache(cache_dir, self.payload_with_single("cached-model"))
        clock = FakeClock()
        # Way past any TTL: the cache is ancient but still real fetched data.
        clock.advance(365 * 24 * 60 * 60)
        source = ModelsDevCatalogSource(
            cache_dir=cache_dir,
            snapshot_path=self.write_snapshot(tmp_path),
            clock=clock,
            auto_refresh=False,
        )
        ids = {record.model_id for record in source.records()}
        assert source.current_tier() is CatalogTier.CACHE
        assert ids == {"cached-model"}

    def test_missing_everything_serves_empty_not_error(self, tmp_path: Path) -> None:
        source = ModelsDevCatalogSource(
            cache_dir=tmp_path / "nope",
            snapshot_path=Path(self.MISSING_PATH),
            auto_refresh=False,
        )
        assert source.records() == ()
        assert source.current_tier() is CatalogTier.EMPTY


class TestRefreshLifecycle(ModelsDevFixtureMixin):
    def test_successful_refresh_serves_live_and_writes_cache(
        self, tmp_path: Path
    ) -> None:
        cache_dir = tmp_path / "cache"
        source = ModelsDevCatalogSource(
            cache_dir=cache_dir,
            snapshot_path=self.write_snapshot(tmp_path),
            http_client=self.client_returning(self.payload_with_single("live-model")),
            clock=FakeClock(),
            auto_refresh=False,
        )
        assert source.refresh_now() is True
        assert source.current_tier() is CatalogTier.LIVE
        assert {r.model_id for r in source.records()} == {"live-model"}
        assert (cache_dir / ModelsDevCatalogSource.CACHE_FILENAME).exists()

    def test_failed_refresh_keeps_snapshot_and_backs_off(self, tmp_path: Path) -> None:
        clock = FakeClock()
        source = ModelsDevCatalogSource(
            snapshot_path=self.write_snapshot(tmp_path),
            http_client=self.client_http_error(500),
            clock=clock,
            auto_refresh=False,
        )
        assert source.records()  # loads snapshot
        assert source.refresh_now() is False
        assert source.current_tier() is CatalogTier.SNAPSHOT
        # Backoff: no immediate retry, then due again after the interval.
        assert source.refresh_due() is False
        clock.advance(ModelsDevCatalogSource.RETRY_INTERVAL_SECONDS + 1)
        assert source.refresh_due() is True

    def test_broken_json_fetch_never_raises(self, tmp_path: Path) -> None:
        source = ModelsDevCatalogSource(
            snapshot_path=self.write_snapshot(tmp_path),
            http_client=self.client_broken_json(),
            clock=FakeClock(),
            auto_refresh=False,
        )
        assert source.refresh_now() is False
        assert source.current_tier() in (CatalogTier.SNAPSHOT, CatalogTier.EMPTY)

    def test_network_error_never_raises(self, tmp_path: Path) -> None:
        source = ModelsDevCatalogSource(
            snapshot_path=self.write_snapshot(tmp_path),
            http_client=self.client_network_error(),
            clock=FakeClock(),
            auto_refresh=False,
        )
        assert source.refresh_now() is False

    def test_ttl_governs_refresh_due_after_success(self, tmp_path: Path) -> None:
        clock = FakeClock()
        source = ModelsDevCatalogSource(
            snapshot_path=self.write_snapshot(tmp_path),
            http_client=self.client_returning(self.payload_with_single("live-model")),
            clock=clock,
            auto_refresh=False,
        )
        assert source.refresh_now() is True
        assert source.refresh_due() is False
        clock.advance(ModelsDevCatalogSource.CACHE_TTL_SECONDS + 1)
        assert source.refresh_due() is True
