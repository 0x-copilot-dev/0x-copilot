"""Model catalog SSOT — the one builder the picker and workspace validation share.

``ModelCatalog.build`` is the single deduplication point. These tests lock in
the invariants it guarantees by construction — the runtime default is always
present exactly once and first, no id is ever double-listed, and only run-path
providers ever reach the picker — plus the LiteLLM-sourced metadata mapping the
frontend picker relies on. The source discovers eligible rows directly from
``litellm.model_cost`` (:mod:`agent_runtime.api.litellm_model_source`); tests
inject a deterministic table or a fake source so nothing touches LiteLLM's real
table except the pinned-version assertions.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from agent_runtime.api.litellm_model_source import (
    CatalogModelRecord,
    CompositeModelSource,
    LitellmModelSource,
    ModelDisplayName,
)
from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.settings import RuntimeSettings


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load()


def _model_cost() -> dict[str, dict[str, object]]:
    """Small representative LiteLLM table for hermetic catalog tests."""

    return {
        "claude-opus-4-8": {
            "litellm_provider": "anthropic",
            "mode": "chat",
            "supports_function_calling": True,
            "input_cost_per_token": 5e-06,
            "output_cost_per_token": 2.5e-05,
            "max_input_tokens": 1_000_000,
            "max_output_tokens": 128_000,
            "supports_reasoning": True,
            "supports_vision": True,
        },
        "gpt-5.6": {
            "litellm_provider": "openai",
            "mode": "chat",
            "supports_function_calling": True,
            "max_input_tokens": 1_050_000,
            "supports_reasoning": True,
        },
        "gemini/gemini-2.5-pro": {
            "litellm_provider": "gemini",
            "mode": "chat",
            "supports_function_calling": True,
            "max_input_tokens": 1_048_576,
            "supports_pdf_input": True,
        },
        "openrouter/anthropic/claude-sonnet-4.6": {
            "litellm_provider": "openrouter",
            "mode": "chat",
            "supports_function_calling": True,
            "supports_reasoning": True,
        },
    }


class _FakeSource:
    """A stand-in ``CatalogModelSource`` returning fixed records (no LiteLLM)."""

    def __init__(self, records: tuple[CatalogModelRecord, ...]) -> None:
        self._records = records

    def records(self) -> tuple[CatalogModelRecord, ...]:
        return self._records


class _ExplodingSource:
    """A source whose upstream is having a bad day."""

    def records(self) -> tuple[CatalogModelRecord, ...]:
        raise RuntimeError("upstream on fire")


class TestCompositeModelSource:
    """Union, not fallback — each source owns a catalog the others lack."""

    @staticmethod
    def _record(provider: str, model_id: str) -> CatalogModelRecord:
        return CatalogModelRecord(
            provider=provider, model_id=model_id, display_name=model_id
        )

    def test_unions_every_source_in_order(self) -> None:
        first = _FakeSource((self._record("openai", "gpt-5.6"),))
        second = _FakeSource((self._record("virtuals", "moonshotai-kimi-k3"),))

        combined = CompositeModelSource(first, second).records()

        # Order preserved: ModelCatalog de-dupes keeping the FIRST occurrence's
        # position, so the richer source must be able to win by going first.
        assert [r.model_id for r in combined] == ["gpt-5.6", "moonshotai-kimi-k3"]

    def test_a_failing_source_does_not_empty_the_catalog(self) -> None:
        """One upstream down must not take the whole picker with it."""

        healthy = _FakeSource((self._record("openai", "gpt-5.6"),))

        combined = CompositeModelSource(_ExplodingSource(), healthy).records()

        assert [r.model_id for r in combined] == ["gpt-5.6"]

    def test_no_sources_is_empty_not_an_error(self) -> None:
        assert CompositeModelSource().records() == ()


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

    def test_drops_a_trailing_release_date_stamp(self) -> None:
        # A vendor snapshot id carries a YYYYMMDD tail. Without a rule for it,
        # ``_collapse_trailing_version`` swallowed the date into the version and
        # the picker read "Claude Haiku 4.5.20251001".
        assert ModelDisplayName.derive("claude-haiku-4-5-20251001") == (
            "Claude Haiku 4.5"
        )
        assert ModelDisplayName.derive("claude-opus-4-5-20251101") == "Claude Opus 4.5"
        assert ModelDisplayName.derive("claude-3-haiku-20240307") == "Claude 3 Haiku"

    def test_does_not_mistake_a_version_for_a_date(self) -> None:
        # The rule is deliberately strict — 8 digits AND a real 20xx date — so
        # an 8-digit build number survives. ``12345678`` has month 56.
        assert ModelDisplayName.derive("some-model-12345678") == ("Some Model 12345678")

    def test_strips_a_provider_namespace_prefix(self) -> None:
        # The ``/`` survived the ``-`` split, so it both leaked the namespace
        # and blocked the title-case pass — hence the lower-case "claude".
        assert ModelDisplayName.derive("anthropic/claude-haiku-4.5") == (
            "Claude Haiku 4.5"
        )
        assert ModelDisplayName.derive("meta-llama/llama-3.3-70b") == ("Llama 3.3 70b")

    def test_degrades_rather_than_rendering_an_empty_label(self) -> None:
        # A pathological id must not collapse to "" — a nameless row is worse
        # than an ugly one.
        assert ModelDisplayName.derive("anthropic/") == "Anthropic/"
        assert ModelDisplayName.derive("20251001") == "20251001"


class TestLitellmModelSource:
    """Generic discovery and enrichment from an injected ``model_cost`` map."""

    def test_discovers_and_enriches_upstream_row(self) -> None:
        source = LitellmModelSource(model_cost=_model_cost())
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
                    "litellm_provider": "openai",
                    "mode": "chat",
                    "supports_function_calling": True,
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
                    "litellm_provider": "openai",
                    "mode": "chat",
                    "supports_function_calling": True,
                    "input_cost_per_token": 7.5e-07,
                    "output_cost_per_token": 4.5e-06,
                    "supports_pdf_input": True,
                }
            }
        )
        record = {r.model_id: r for r in source.records()}["gpt-5.4-mini"]
        assert record.supports_attachments is True

    def test_normalizes_provider_prefixes_and_deduplicates_aliases(self) -> None:
        table = _model_cost()
        table["gemini-2.5-pro"] = {
            "litellm_provider": "gemini",
            "mode": "chat",
            "supports_function_calling": True,
            "max_input_tokens": 123,
        }
        records = LitellmModelSource(model_cost=table).records()
        gemini = [
            record
            for record in records
            if record.provider == "gemini" and record.model_id == "gemini-2.5-pro"
        ]
        assert len(gemini) == 1
        # The provider-qualified row is canonical when a bare alias also exists.
        assert gemini[0].context_window == 1_048_576

    def test_openrouter_keeps_vendor_slug_and_derives_generic_name(self) -> None:
        records = LitellmModelSource(model_cost=_model_cost()).records()
        record = {(item.provider, item.model_id): item for item in records}[
            ("openrouter", "anthropic/claude-sonnet-4.6")
        ]
        assert record.display_name == "Claude Sonnet 4.6 (OpenRouter)"

    def test_filters_non_chat_non_tool_deprecated_finetune_and_mirrors(self) -> None:
        common = {
            "litellm_provider": "openai",
            "mode": "chat",
            "supports_function_calling": True,
        }
        table = {
            "gpt-good": common,
            "gpt-embedding": {**common, "mode": "embedding"},
            "gpt-no-tools": {**common, "supports_function_calling": False},
            "gpt-old": {**common, "deprecation_date": "2025-01-01"},
            "ft:gpt-4o": common,
            "azure/gpt-mirror": common,
            "bedrock-claude": {
                **common,
                "litellm_provider": "bedrock",
            },
        }
        ids = {
            record.model_id for record in LitellmModelSource(model_cost=table).records()
        }
        assert ids == {"gpt-good"}

    def test_empty_upstream_table_returns_no_records(self) -> None:
        assert LitellmModelSource(model_cost={}).records() == ()

    def test_records_ordered_provider_then_id(self) -> None:
        records = LitellmModelSource(model_cost=_model_cost()).records()
        keys = [(r.provider, r.model_id) for r in records]
        assert keys == sorted(keys)


class TestModelCatalogBuild:
    """``build`` invariants: default-first, id-unique, run-path-only providers."""

    def test_default_present_exactly_once_and_first(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource(model_cost=_model_cost()))
        settings = _settings()
        items = ModelCatalog.build(settings)
        ids = [item.id for item in items]
        assert items[0].id == settings.default_model.model_name
        assert items[0].provider == settings.default_model.provider
        assert ids.count(settings.default_model.model_name) == 1

    def test_no_duplicate_ids(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource(model_cost=_model_cost()))
        items = ModelCatalog.build(_settings())
        duplicates = {
            model_id: n
            for model_id, n in Counter(item.id for item in items).items()
            if n > 1
        }
        assert duplicates == {}

    def test_one_model_family_is_one_row(self) -> None:
        # A vendor catalog carries the same model plain and date-stamped. Once
        # the deriver stopped rendering the stamp both derive the SAME label, so
        # without a collapse the picker shows a column of identical rows — which
        # is how it came to offer five Haiku rows for one family.
        records = (
            CatalogModelRecord(
                provider="anthropic",
                model_id="claude-haiku-4-5",
                display_name="Claude Haiku 4.5",
            ),
            CatalogModelRecord(
                provider="anthropic",
                model_id="claude-haiku-4-5-20251001",
                display_name="Claude Haiku 4.5",
            ),
        )
        ModelCatalog.configure_source(_FakeSource(records))
        items = ModelCatalog.build(_settings())

        haiku = [item for item in items if "haiku" in item.id]
        assert [item.id for item in haiku] == ["claude-haiku-4-5"], (
            "the plain id is the canonical one; the dated alias is the duplicate"
        )

    def test_a_model_without_a_plain_twin_is_never_dropped(self) -> None:
        # The narrow rule's whole point. An earlier by-display-name collapse
        # removed ``claude-opus-4-8`` from a real-table build; a dated id is now
        # only ever dropped when the identical id WITHOUT the stamp is present.
        records = (
            CatalogModelRecord(
                provider="anthropic",
                model_id="claude-3-haiku-20240307",
                display_name="Claude 3 Haiku",
            ),
        )
        ModelCatalog.configure_source(_FakeSource(records))
        items = ModelCatalog.build(_settings())
        assert any(item.id == "claude-3-haiku-20240307" for item in items)

    def test_a_dated_twin_of_another_provider_is_kept(self) -> None:
        # The plain id must belong to the SAME provider to count as the twin.
        records = (
            CatalogModelRecord(
                provider="anthropic",
                model_id="claude-haiku-4-5",
                display_name="Claude Haiku 4.5",
            ),
            CatalogModelRecord(
                provider="openrouter",
                model_id="claude-haiku-4-5-20251001",
                display_name="Claude Haiku 4.5 (OpenRouter)",
            ),
        )
        ModelCatalog.configure_source(_FakeSource(records))
        ids = {item.id for item in ModelCatalog.build(_settings())}
        assert "claude-haiku-4-5-20251001" in ids

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

    def test_upstream_gemini_row_reaches_the_catalog(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource(model_cost=_model_cost()))
        items = ModelCatalog.build(_settings())
        assert any(item.id == "gemini-2.5-pro" for item in items)

    def test_every_item_is_chat_kind(self) -> None:
        # The source catalog is chat-only; the ``kind`` default flows through so
        # a chat picker filtering ``kind == "chat"`` never drops a real model, and
        # a non-chat model could never masquerade as a selectable chat model.
        ModelCatalog.configure_source(LitellmModelSource(model_cost=_model_cost()))
        items = ModelCatalog.build(_settings())
        assert items, "catalog must be non-empty"
        assert all(item.kind == "chat" for item in items)

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
        ModelCatalog.configure_source(LitellmModelSource(model_cost=_model_cost()))
        settings = _settings()

        without = {i.id: i for i in ModelCatalog.build(settings)}
        anthropic_ids = [
            i for i, item in without.items() if item.provider == "anthropic"
        ]
        assert anthropic_ids, "upstream table fixture must include anthropic models"
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

    def test_openrouter_selectability_follows_the_caller_key(self) -> None:
        ModelCatalog.configure_source(LitellmModelSource())
        # Hermetic: RuntimeSettings.load() would pick up a developer's real
        # OPENROUTER_API_KEY and mask the no-key case this pins.
        keyless = RuntimeSettings.load(env_file=Path("/nonexistent/.env"), environ={})
        without = [
            item
            for item in ModelCatalog.build(keyless)
            if item.provider == "openrouter"
        ]
        assert without, "LiteLLM must supply openrouter discovery models"
        # No env key and no BYOK key -> not selectable. OpenRouter used to be
        # exempt here, which made the picker advertise "your key" to a user who
        # had none while the run-create gate rejected the very same model.
        assert all(item.configured is False for item in without)

        with_key = [
            item
            for item in ModelCatalog.build(
                keyless, user_key_providers=frozenset({"openrouter"})
            )
            if item.provider == "openrouter"
        ]
        assert with_key and all(item.configured for item in with_key)
        # Reasoning passthrough for OpenAI-compat gateways is a follow-up.
        assert all(not item.supports_reasoning for item in without)
        assert all(item.id == item.model_name for item in without)
