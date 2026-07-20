"""Unified model catalog — native models preserved, OpenRouter appended."""

from __future__ import annotations

from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.settings import RuntimeSettings


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
