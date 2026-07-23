"""Model catalog SSOT — the one builder the picker and workspace validation share.

``ModelCatalog.build`` is the single deduplication point. These tests lock in
the invariants it guarantees by construction — the runtime default is always
present exactly once and first, no id is ever double-listed, and only run-path
providers ever reach the picker — plus the LiteLLM-sourced metadata mapping the
frontend picker relies on. The metadata source is a curated product registry
enriched from ``litellm.model_cost`` (:mod:`agent_runtime.api.litellm_model_source`);
tests inject a deterministic ``model_cost`` map or a fake source so nothing
touches LiteLLM's real table except the couple of pinned-version assertions.
"""

from __future__ import annotations

from collections import Counter

from agent_runtime.api.litellm_model_source import (
    CatalogModelRecord,
    LitellmModelSource,
    ModelDisplayName,
    ProductModelRegistry,
)
from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.settings import RuntimeSettings


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load()


class _FakeSource:
    """A stand-in ``CatalogModelSource`` returning fixed records (no LiteLLM)."""

    def __init__(self, records: tuple[CatalogModelRecord, ...]) -> None:
        self._records = records

    def records(self) -> tuple[CatalogModelRecord, ...]:
        return self._records


class TestModelDisplayName:
    """Display name is derived from the id — LiteLLM carries none."""

    def test_derives_task_examples(self) -> None:
        assert ModelDisplayName.derive("claude-opus-4-8") == "Claude Opus 4.8"
        assert ModelDisplayName.derive("gpt-5.6") == "GPT-5.6"
        assert ModelDisplayName.derive("gemini-2.5-pro") == "Gemini 2.5 Pro"

    def test_uppercases_gpt_acronym_and_titlecases_words(self) -> None:
        # ``gpt`` is a known acronym; the version token that follows it joins
        # with a hyphen (vendor branding), other words join with spaces.
        assert ModelDisplayName.derive("gpt-5.4-mini") == "GPT-5.4 Mini"
        assert ModelDisplayName.derive("gpt-5") == "GPT-5"

    def test_normalises_underscores_and_collapses_trailing_version(self) -> None:
        # ``claude_opus`` == ``claude-opus``; a trailing run of bare integers
        # (``-4-7``) collapses into a dotted version.
        assert ModelDisplayName.derive("claude_opus-4-7") == "Claude Opus 4.7"
        assert ModelDisplayName.derive("claude-haiku-4-5") == "Claude Haiku 4.5"

    def test_single_trailing_integer_stays_spaced(self) -> None:
        assert ModelDisplayName.derive("claude-sonnet-5") == "Claude Sonnet 5"
        assert ModelDisplayName.derive("gemini-3-flash") == "Gemini 3 Flash"

    def test_catalog_delegates_to_deriver(self) -> None:
        assert ModelCatalog.display_name("gpt-5.6") == "GPT-5.6"


class TestLitellmModelSource:
    """The curated registry, enriched from an injected ``model_cost`` map."""

    def test_enriches_registry_entry_from_litellm_row(self) -> None:
        source = LitellmModelSource(
            model_cost={
                "claude-opus-4-8": {
                    "input_cost_per_token": 5e-06,
                    "output_cost_per_token": 2.5e-05,
                    "max_input_tokens": 1_000_000,
                    "max_output_tokens": 128_000,
                    "supports_reasoning": True,
                    "supports_function_calling": True,
                    "supports_vision": True,
                }
            }
        )
        record = {r.model_id: r for r in source.records()}["claude-opus-4-8"]
        assert record.provider == "anthropic"
        assert record.display_name == "Claude Opus 4.8"
        assert record.context_window == 1_000_000
        assert record.max_output_tokens == 128_000
        # USD/token -> USD/Mtok, no float drift.
        assert record.input_cost_per_mtok == 5.0
        assert record.output_cost_per_mtok == 25.0
        assert record.supports_reasoning is True
        assert record.supports_tools is True
        assert record.supports_attachments is True

    def test_context_window_falls_back_to_max_tokens(self) -> None:
        source = LitellmModelSource(
            model_cost={
                "gpt-5": {
                    "input_cost_per_token": 1.25e-06,
                    "output_cost_per_token": 1e-05,
                    "max_tokens": 272_000,
                }
            }
        )
        record = {r.model_id: r for r in source.records()}["gpt-5"]
        assert record.context_window == 272_000

    def test_pdf_input_counts_as_attachment_support(self) -> None:
        source = LitellmModelSource(
            model_cost={
                "gpt-5.4-mini": {
                    "input_cost_per_token": 7.5e-07,
                    "output_cost_per_token": 4.5e-06,
                    "supports_pdf_input": True,
                }
            }
        )
        record = {r.model_id: r for r in source.records()}["gpt-5.4-mini"]
        assert record.supports_attachments is True

    def test_gemini_3_flash_supplement_carries_it_when_litellm_lacks_it(self) -> None:
        # Empty map: every native id falls through the LiteLLM lookup.
        # gemini-3-flash must still be present — carried by the reviewed
        # supplement, never silently dropped.
        source = LitellmModelSource(model_cost={})
        record = {r.model_id: r for r in source.records()}["gemini-3-flash"]
        assert record.provider == "gemini"
        assert record.context_window == 1_048_576
        assert record.input_cost_per_mtok == 0.30
        assert record.output_cost_per_mtok == 2.50
        assert record.supports_reasoning is True
        assert record.supports_tools is True

    def test_unknown_model_yields_bare_record_never_dropped(self) -> None:
        # No LiteLLM row and no supplement -> a metadata-less record, so a new
        # registry entry is visible in the picker rather than vanishing.
        source = LitellmModelSource(model_cost={})
        record = {r.model_id: r for r in source.records()}["claude-opus-4-8"]
        assert record.display_name == "Claude Opus 4.8"
        assert record.context_window is None
        assert record.input_cost_per_mtok is None

    def test_covers_every_registry_entry(self) -> None:
        ids = {r.model_id for r in LitellmModelSource(model_cost={}).records()}
        for model_ids in ProductModelRegistry.NATIVE.values():
            assert set(model_ids) <= ids
        for slug, _name in ProductModelRegistry.OPENROUTER:
            assert slug in ids

    def test_records_ordered_provider_then_id(self) -> None:
        records = LitellmModelSource(model_cost={}).records()
        keys = [(r.provider, r.model_id) for r in records]
        assert keys == sorted(keys)


class TestModelCatalogBuild:
    """``build`` invariants: default-first, id-unique, run-path-only providers."""

    def test_default_present_exactly_once_and_first(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource(model_cost={}))
        settings = _settings()
        items = ModelCatalog.build(settings)
        ids = [item.id for item in items]
        assert items[0].id == settings.default_model.model_name
        assert items[0].provider == settings.default_model.provider
        assert ids.count(settings.default_model.model_name) == 1

    def test_no_duplicate_ids(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource(model_cost={}))
        items = ModelCatalog.build(_settings())
        duplicates = {
            model_id: n
            for model_id, n in Counter(item.id for item in items).items()
            if n > 1
        }
        assert duplicates == {}

    def test_supports_provider_filters_out_of_allowlist_records(self) -> None:
        # groq/xai are outside the run path's ``ModelConfigResolver`` allowlist;
        # a source emitting them must never leak into the picker.
        records = (
            CatalogModelRecord(
                provider="groq",
                model_id="llama-3.3-70b-versatile",
                display_name="Llama 3.3 70B",
            ),
            CatalogModelRecord(
                provider="xai", model_id="grok-4.5", display_name="Grok 4.5"
            ),
            CatalogModelRecord(
                provider="anthropic",
                model_id="claude-opus-4-8",
                display_name="Claude Opus 4.8",
            ),
        )
        ModelCatalog.configure_source(_FakeSource(records))
        items = ModelCatalog.build(_settings())
        providers = {item.provider for item in items}
        assert "groq" not in providers
        assert "xai" not in providers
        assert any(item.id == "claude-opus-4-8" for item in items)

    def test_gemini_3_flash_reaches_the_catalog(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource(model_cost={}))
        items = ModelCatalog.build(_settings())
        assert any(item.id == "gemini-3-flash" for item in items)

    def test_default_present_even_with_empty_source(self) -> None:
        ModelCatalog.configure_source(_FakeSource(()))
        settings = _settings()
        items = ModelCatalog.build(settings)
        assert len(items) == 1
        assert items[0].id == settings.default_model.model_name

    def test_richer_source_record_supersedes_default_placeholder(self) -> None:
        settings = _settings()
        default_id = settings.default_model.model_name
        records = (
            CatalogModelRecord(
                provider=settings.default_model.provider,
                model_id=default_id,
                display_name="Default Live",
                context_window=400_000,
                input_cost_per_mtok=0.25,
            ),
        )
        ModelCatalog.configure_source(_FakeSource(records))
        items = ModelCatalog.build(settings)
        matching = [item for item in items if item.id == default_id]
        assert len(matching) == 1, "default must not be double-listed"
        assert items[0] is matching[0], "default stays first after the merge"
        assert matching[0].context_window == 400_000
        assert matching[0].input_cost_per_mtok == 0.25


class TestModelCatalogByokConfigured:
    """``configured`` reflects the caller's BYOK keys, not just deployment env keys.

    This is the M1 fix: the picker's "your key" badge is computed from the same
    credential sources the run-create gate accepts (env key OR the caller's stored
    BYOK key), so a user who has added an OpenAI key in Settings sees their models
    as selectable — and the badge can never disagree with what a run actually does.
    """

    def test_caller_byok_key_flips_only_that_provider_to_configured(
        self, monkeypatch
    ) -> None:
        # No deployment env keys → native providers are unconfigured by default,
        # so the flip we assert is attributable purely to the BYOK argument.
        for var in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY",
        ):
            monkeypatch.setenv(var, "")
        ModelCatalog.configure_source(LitellmModelSource(model_cost={}))
        settings = _settings()

        without = {i.id: i for i in ModelCatalog.build(settings)}
        anthropic_ids = [
            i for i, item in without.items() if item.provider == "anthropic"
        ]
        assert anthropic_ids, "registry must ship anthropic models"
        assert all(without[i].configured is False for i in anthropic_ids)

        with_key = {
            i.id: i
            for i in ModelCatalog.build(
                settings, user_key_providers=frozenset({"anthropic"})
            )
        }
        # The caller's anthropic BYOK key makes exactly the anthropic models usable…
        assert all(with_key[i].configured is True for i in anthropic_ids)
        # …and leaves providers the caller has no key for untouched.
        openai_ids = [i for i, item in with_key.items() if item.provider == "openai"]
        assert openai_ids, "default model is openai — must be present"
        assert all(with_key[i].configured is False for i in openai_ids)


class TestModelCatalogRealLitellm:
    """A couple of assertions against the real (pinned) LiteLLM table."""

    def test_native_product_models_present_with_metadata(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource())
        items = {item.id: item for item in ModelCatalog.build(_settings())}
        assert {"claude-opus-4-8", "gpt-5.6", "gemini-2.5-pro"} <= set(items)
        opus = items["claude-opus-4-8"]
        assert opus.name == "Claude Opus 4.8"
        assert opus.input_cost_per_mtok == 5.0
        assert opus.context_window == 1_000_000

    def test_openrouter_present_selectable_and_reasoning_off(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource())
        items = ModelCatalog.build(_settings())
        openrouter = [item for item in items if item.provider == "openrouter"]
        assert openrouter, "registry must supply openrouter discovery models"
        # BYOK availability is per-user and unknown here, so always selectable.
        assert all(item.configured for item in openrouter)
        # Reasoning passthrough for OpenAI-compat gateways is a follow-up.
        assert all(not item.supports_reasoning for item in openrouter)
        assert all(item.id == item.model_name for item in openrouter)
