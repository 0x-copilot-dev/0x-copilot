"""Model catalog SSOT — the one builder the picker and workspace validation share.

``ModelCatalog.build`` is the single deduplication point. These tests lock
in the two invariants it guarantees by construction — the runtime default is
always present exactly once and first, and no id is ever double-listed — plus
the metadata mapping the frontend picker relies on.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.api.models_dev_source import ModelsDevCatalogSource
from agent_runtime.settings import RuntimeSettings
from tests.unit.agent_runtime.api.models_dev_fixtures import ModelsDevFixtureMixin


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load()


class TestModelCatalog:
    def test_openrouter_models_present_and_selectable(self) -> None:
        # OpenRouter entries now come from the models.dev source (vendored
        # snapshot in unit tests) instead of a hardcoded constant.
        items = ModelCatalog.build(_settings())
        openrouter = [item for item in items if item.provider == "openrouter"]
        assert openrouter, "snapshot must supply openrouter models"
        # BYOK availability is per-user and unknown here, so they are always
        # selectable (configured=True) and stream.
        assert all(item.configured for item in openrouter)
        assert all(item.supports_streaming for item in openrouter)
        # id and model_name are the OpenRouter vendor/model slug verbatim.
        assert all(item.id == item.model_name for item in openrouter)
        assert all("/" in item.id for item in openrouter)

    def test_native_curated_models_preserved(self) -> None:
        items = ModelCatalog.build(_settings())
        ids = {item.id for item in items}
        assert {"gpt-5.4-mini", "claude-opus-4-7", "gemini-2.5-pro"} <= ids

    def test_openrouter_reasoning_off_for_round_one(self) -> None:
        items = ModelCatalog.build(_settings())
        openrouter = [item for item in items if item.provider == "openrouter"]
        assert all(not item.supports_reasoning for item in openrouter)

    def test_display_name_uppercases_gpt(self) -> None:
        assert ModelCatalog.display_name("gpt-5.4-mini") == "GPT 5.4 Mini"
        assert ModelCatalog.display_name("claude_opus-4-7") == "Claude Opus 4 7"


class TestCatalogSsotInvariants(ModelsDevFixtureMixin):
    """``build`` guarantees id-uniqueness and a present, leading default."""

    def test_no_duplicate_ids(self) -> None:
        # The frontend keys picker rows by ``id``; a duplicate id would be a
        # duplicate React key. ``build`` must return an id-unique tuple.
        items = ModelCatalog.build(_settings())
        counts = Counter(item.id for item in items)
        duplicates = {model_id: n for model_id, n in counts.items() if n > 1}
        assert duplicates == {}, duplicates

    def test_default_model_present_exactly_once(self) -> None:
        settings = _settings()
        items = ModelCatalog.build(settings)
        ids = [item.id for item in items]
        assert ids.count(settings.default_model.model_name) == 1

    def test_default_model_is_first(self) -> None:
        settings = _settings()
        items = ModelCatalog.build(settings)
        assert items[0].id == settings.default_model.model_name
        assert items[0].provider == settings.default_model.provider

    def test_default_present_and_first_even_with_empty_source(self) -> None:
        # Offline boot with no cache/snapshot: the source yields nothing, yet
        # the settings-derived default still anchors a usable catalog.
        ModelCatalog.configure_source(
            ModelsDevCatalogSource(snapshot_path=self.MISSING_PATH, auto_refresh=False)
        )
        settings = self.settings_with()
        items = ModelCatalog.build(settings)
        assert len(items) == 1
        assert items[0].id == settings.default_model.model_name
        assert items[0].provider == settings.default_model.provider

    def test_default_not_double_listed_when_source_ships_it(
        self, tmp_path: Path
    ) -> None:
        # The source carries the exact default (openai/gpt-5.4-mini) with rich
        # metadata. The catalog must list it once, first, and carry the source's
        # richer fields — not a second, minimal placeholder row.
        settings = self.settings_with()
        provider = settings.default_model.provider
        model_name = settings.default_model.model_name
        payload = {
            provider: {
                "id": provider,
                "models": {
                    model_name: {
                        "id": model_name,
                        "name": "GPT 5.4 Mini (live)",
                        "release_date": "2026-01-01",
                        "limit": {"context": 400_000, "output": 128_000},
                        "cost": {"input": 0.25, "output": 2.0},
                    },
                    "gpt-other": {
                        "id": "gpt-other",
                        "name": "GPT Other",
                        "release_date": "2026-02-01",
                        "limit": {"context": 128_000},
                    },
                },
            }
        }
        ModelCatalog.configure_source(self.source_with_snapshot(tmp_path, payload))
        items = ModelCatalog.build(settings)

        matching = [item for item in items if item.id == model_name]
        assert len(matching) == 1, "default must not be double-listed"
        default_item = matching[0]
        assert items[0] is default_item, "default stays first after the merge"
        # Richer live record wins over the minimal default placeholder.
        assert default_item.context_window == 400_000
        assert default_item.input_cost_per_mtok == 0.25
        # And the sibling source model is still present.
        assert any(item.id == "gpt-other" for item in items)

    def test_build_is_idempotent_under_repeated_dedup(self) -> None:
        # Re-running the same id-dedup over ``build``'s output is a no-op, i.e.
        # the tuple is already collapsed — the picker route can consume it raw.
        items = ModelCatalog.build(_settings())
        rededuped = tuple({item.id: item for item in items}.values())
        assert [item.id for item in rededuped] == [item.id for item in items]
