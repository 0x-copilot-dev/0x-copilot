"""LiteLLM-library-sourced model catalog metadata (the picker's model set).

Replaces the retired models.dev source. The *set* of models is a curated
product registry; their *metadata* (context window, capability flags, per-Mtok
cost) is read live from the installed ``litellm`` package's bundled
``model_cost`` table — the same offline table slice 1 uses for pricing. This
mirrors the pricing seam: LiteLLM is the source of truth for the values, and a
small reviewed supplement backstops the models LiteLLM lacks (today
``gemini-3-flash``), so a product model is never silently dropped.

**Why a curated registry, not ``litellm.models_by_provider`` enumeration.**
``litellm.model_cost`` is a flat pricing table with a provider taxonomy built
for billing, not for a picker: the same Gemini model appears under both
``gemini`` and ``vertex_ai-language-models`` keys, hundreds of azure/bedrock/
fireworks mirrors reuse the same ids, and image/embedding/TTS rows sit beside
chat rows. Enumerating it would surface duplicates and non-chat noise. The
product supports a focused model set, so that set is declared here and enriched
from LiteLLM — exactly as the pre-models.dev catalog did, only now the numbers
come from LiteLLM instead of being hardcoded.

Display names are derived from the model id (:class:`ModelDisplayName`) because
LiteLLM carries no display name; catalog ordering is provider-then-id because
LiteLLM carries no ``release_date`` (only ``deprecation_date``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Final, Protocol, runtime_checkable

from agent_runtime.execution.contracts import RuntimeContract


_LOGGER = logging.getLogger("agent_runtime.api.litellm_model_source")

# LiteLLM returns cost as USD per single token; the picker shows USD per 1M
# tokens. Convert with Decimal so 5e-6 * 1e6 lands on exactly 5.0 (no binary
# float drift that would render as "5.000000000000001" in the picker).
_USD_PER_TOKEN_TO_PER_MILLION: Final[Decimal] = Decimal(1_000_000)


class CatalogModelRecord(RuntimeContract):
    """Normalized, trusted metadata for one catalog model.

    No ``release_date`` — LiteLLM does not carry one, and the catalog no longer
    orders or curates on release date (see :class:`ModelEnablementResolver`).
    """

    provider: str
    model_id: str
    display_name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None
    supports_reasoning: bool = False
    supports_tools: bool = False
    supports_attachments: bool = False


class ModelDisplayName:
    """Derive a human-readable label from a slug-style model id.

    LiteLLM has no display name, so the picker label is computed from the id:

    * ``claude-opus-4-8`` -> ``"Claude Opus 4.8"``
    * ``gpt-5.6``         -> ``"GPT-5.6"``
    * ``gemini-2.5-pro``  -> ``"Gemini 2.5 Pro"``

    Rules: split on ``-`` (``_`` normalised to ``-`` first); collapse a trailing
    run of bare-integer parts into one dotted version (``…-4-8`` -> ``4.8``);
    upper-case known acronyms (``gpt``); title-case plain words; keep numeric /
    dotted-numeric parts verbatim as version tokens. A version token that
    immediately follows an acronym joins with a hyphen (``GPT-5.6``, matching the
    vendor's own branding); every other boundary is a space.
    """

    # Slug fragments that render fully upper-cased rather than title-cased.
    KNOWN_ACRONYMS: Final[frozenset[str]] = frozenset({"gpt"})

    class _Kind:
        ACRONYM = "acronym"
        VERSION = "version"
        WORD = "word"

    @classmethod
    def derive(cls, model_id: str) -> str:
        parts = [part for part in model_id.replace("_", "-").split("-") if part]
        if not parts:
            return model_id
        parts = cls._collapse_trailing_version(parts)
        tokens = [cls._classify(part) for part in parts]
        return cls._join(tokens)

    @classmethod
    def _collapse_trailing_version(cls, parts: list[str]) -> list[str]:
        """Join a trailing run of >=2 bare integers into one dotted version."""

        cut = len(parts)
        while cut > 0 and parts[cut - 1].isdigit():
            cut -= 1
        if len(parts) - cut >= 2:
            return parts[:cut] + [".".join(parts[cut:])]
        return parts

    @classmethod
    def _classify(cls, part: str) -> tuple[str, str]:
        low = part.lower()
        if low in cls.KNOWN_ACRONYMS:
            return part.upper(), cls._Kind.ACRONYM
        if cls._is_version(part):
            return part, cls._Kind.VERSION
        return part[:1].upper() + part[1:], cls._Kind.WORD

    @staticmethod
    def _is_version(part: str) -> bool:
        return part.replace(".", "", 1).isdigit() and any(c.isdigit() for c in part)

    @classmethod
    def _join(cls, tokens: list[tuple[str, str]]) -> str:
        rendered = tokens[0][0]
        for index in range(1, len(tokens)):
            text, kind = tokens[index]
            previous_kind = tokens[index - 1][1]
            separator = (
                "-"
                if kind == cls._Kind.VERSION and previous_kind == cls._Kind.ACRONYM
                else " "
            )
            rendered = f"{rendered}{separator}{text}"
        return rendered


class ProductModelRegistry:
    """The curated set of product-supported models, grouped by runtime slug.

    Runtime slugs only — every provider here passes the run path's
    ``ModelConfigResolver.supports_provider`` allowlist, so nothing the registry
    lists can be rejected the moment a run starts.
    """

    # Native direct-provider product models. Intra-provider order is the intended
    # display order (most capable first); ``LitellmModelSource`` re-sorts to a
    # deterministic provider-then-id order, so this is documentation, not a
    # load-bearing sequence.
    NATIVE: Mapping[str, tuple[str, ...]] = {
        "anthropic": (
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-sonnet-5",
            "claude-haiku-4-5",
        ),
        "openai": ("gpt-5.6", "gpt-5.4-mini", "gpt-5"),
        "gemini": ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-3-flash"),
    }

    # Curated OpenRouter discovery set: ``(vendor/model slug, display name)``.
    # OpenRouter availability is per-user BYOK (invisible at this settings-only
    # layer) so these are always selectable; the composer's custom-model field
    # covers the long tail beyond this convenience list, so it stays short.
    # Display names are explicit — deriving them from a ``vendor/model`` slug
    # reads poorly, and OpenRouter mirrors already have canonical names.
    OPENROUTER: tuple[tuple[str, str], ...] = (
        ("openai/gpt-4o", "GPT-4o (OpenRouter)"),
        ("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6 (OpenRouter)"),
        ("google/gemini-2.5-pro", "Gemini 2.5 Pro (OpenRouter)"),
        ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B (OpenRouter)"),
        ("deepseek/deepseek-chat", "DeepSeek Chat (OpenRouter)"),
        ("mistralai/mistral-large", "Mistral Large (OpenRouter)"),
    )

    OPENROUTER_PROVIDER: Final[str] = "openrouter"


class _SupplementEntry(RuntimeContract):
    """Reviewed static metadata for a product model LiteLLM does not carry."""

    context_window: int | None = None
    max_output_tokens: int | None = None
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None
    supports_reasoning: bool = False
    supports_tools: bool = False
    supports_attachments: bool = False


@runtime_checkable
class CatalogModelSource(Protocol):
    """The seam :class:`~agent_runtime.api.model_catalog.ModelCatalog` builds on.

    A thin protocol so tests can inject a fake source (e.g. to exercise the
    ``supports_provider`` filter with an out-of-allowlist provider record)
    without constructing a full :class:`LitellmModelSource`.
    """

    def records(self) -> tuple[CatalogModelRecord, ...]: ...


class LitellmModelSource:
    """Assemble catalog records for the curated registry, enriched from LiteLLM."""

    MODEL_MISSING_LOG_EVENT: Final[str] = "catalog.litellm_model_missing"

    class _Fields:
        """Stable LiteLLM ``model_cost`` field names — pinned so a rename fails here."""

        INPUT_COST_PER_TOKEN = "input_cost_per_token"
        OUTPUT_COST_PER_TOKEN = "output_cost_per_token"
        MAX_INPUT_TOKENS = "max_input_tokens"
        MAX_TOKENS = "max_tokens"
        MAX_OUTPUT_TOKENS = "max_output_tokens"
        SUPPORTS_REASONING = "supports_reasoning"
        SUPPORTS_FUNCTION_CALLING = "supports_function_calling"
        SUPPORTS_VISION = "supports_vision"
        SUPPORTS_PDF_INPUT = "supports_pdf_input"

    # Canonical run-path slug -> LiteLLM key prefix, used to build the
    # ``provider/model`` candidate key form (bare id is tried first). Mirrors
    # ``LitellmRateSource._LITELLM_PREFIX`` so metadata and pricing resolve the
    # same rows.
    _LITELLM_PREFIX: Final[Mapping[str, str]] = {
        "anthropic": "anthropic",
        "openai": "openai",
        "gemini": "gemini",
        "openrouter": "openrouter",
        "ollama": "ollama",
    }

    # Reviewed metadata backstop for product models LiteLLM lacks — the catalog
    # analogue of ``config/pricing_overrides.yaml``. gemini-3-flash: absent from
    # LiteLLM 1.93.0 ``model_cost``; mirror gemini-2.5-flash's published limits
    # and prices ($0.30 in / $2.50 out per 1M, ~1M ctx) as the defensible proxy.
    # Remove once LiteLLM ships the model.
    _SUPPLEMENT: Mapping[tuple[str, str], _SupplementEntry] = {
        ("gemini", "gemini-3-flash"): _SupplementEntry(
            context_window=1_048_576,
            max_output_tokens=65_535,
            input_cost_per_mtok=0.30,
            output_cost_per_mtok=2.50,
            supports_reasoning=True,
            supports_tools=True,
            supports_attachments=True,
        ),
    }

    def __init__(
        self,
        *,
        model_cost: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        # Injected in tests to stay hermetic/deterministic; ``None`` resolves
        # from the installed ``litellm`` package on first use.
        self._model_cost = model_cost

    def records(self) -> tuple[CatalogModelRecord, ...]:
        """Return the curated catalog, provider-then-id ordered, never raising."""

        records: list[CatalogModelRecord] = []
        for provider, model_ids in ProductModelRegistry.NATIVE.items():
            for model_id in model_ids:
                records.append(
                    self._record(
                        provider=provider,
                        model_id=model_id,
                        display_name=ModelDisplayName.derive(model_id),
                    )
                )
        for slug, display_name in ProductModelRegistry.OPENROUTER:
            records.append(
                self._record(
                    provider=ProductModelRegistry.OPENROUTER_PROVIDER,
                    model_id=slug,
                    display_name=display_name,
                )
            )
        return self._sorted(records)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record(
        self, *, provider: str, model_id: str, display_name: str
    ) -> CatalogModelRecord:
        """Enrich one registry entry from LiteLLM, else the supplement, else bare."""

        row = self._litellm_row(provider=provider, model_id=model_id)
        if row is not None:
            return self._from_litellm(
                provider=provider,
                model_id=model_id,
                display_name=display_name,
                row=row,
            )
        supplement = self._SUPPLEMENT.get((provider, model_id))
        if supplement is not None:
            return self._from_supplement(
                provider=provider,
                model_id=model_id,
                display_name=display_name,
                supplement=supplement,
            )
        # Never drop a product model: emit a metadata-less record and log the
        # miss so a new registry entry with no LiteLLM row is visible in logs
        # rather than silently priced/sized as unknown.
        _LOGGER.info(
            self.MODEL_MISSING_LOG_EVENT,
            extra={"metadata": {"provider": provider, "model_id": model_id}},
        )
        return CatalogModelRecord(
            provider=provider, model_id=model_id, display_name=display_name
        )

    def _from_litellm(
        self,
        *,
        provider: str,
        model_id: str,
        display_name: str,
        row: Mapping[str, object],
    ) -> CatalogModelRecord:
        context_window = self._int_field(
            row, self._Fields.MAX_INPUT_TOKENS
        ) or self._int_field(row, self._Fields.MAX_TOKENS)
        return CatalogModelRecord(
            provider=provider,
            model_id=model_id,
            display_name=display_name,
            context_window=context_window,
            max_output_tokens=self._int_field(row, self._Fields.MAX_OUTPUT_TOKENS),
            input_cost_per_mtok=self._per_mtok(
                self._float_field(row, self._Fields.INPUT_COST_PER_TOKEN)
            ),
            output_cost_per_mtok=self._per_mtok(
                self._float_field(row, self._Fields.OUTPUT_COST_PER_TOKEN)
            ),
            supports_reasoning=self._bool_field(row, self._Fields.SUPPORTS_REASONING),
            supports_tools=self._bool_field(
                row, self._Fields.SUPPORTS_FUNCTION_CALLING
            ),
            supports_attachments=(
                self._bool_field(row, self._Fields.SUPPORTS_VISION)
                or self._bool_field(row, self._Fields.SUPPORTS_PDF_INPUT)
            ),
        )

    @staticmethod
    def _from_supplement(
        *,
        provider: str,
        model_id: str,
        display_name: str,
        supplement: _SupplementEntry,
    ) -> CatalogModelRecord:
        return CatalogModelRecord(
            provider=provider,
            model_id=model_id,
            display_name=display_name,
            context_window=supplement.context_window,
            max_output_tokens=supplement.max_output_tokens,
            input_cost_per_mtok=supplement.input_cost_per_mtok,
            output_cost_per_mtok=supplement.output_cost_per_mtok,
            supports_reasoning=supplement.supports_reasoning,
            supports_tools=supplement.supports_tools,
            supports_attachments=supplement.supports_attachments,
        )

    def _litellm_row(
        self, *, provider: str, model_id: str
    ) -> Mapping[str, object] | None:
        """First matching ``model_cost`` row across candidate key forms."""

        table = self._model_cost_table()
        for key in self._candidate_keys(provider=provider, model_id=model_id):
            row = table.get(key)
            if isinstance(row, Mapping):
                return row
        return None

    @classmethod
    def _candidate_keys(cls, *, provider: str, model_id: str) -> tuple[str, ...]:
        """LiteLLM key forms to try: bare id first, then ``prefix/model_id``.

        Bare ``model_id`` matches the direct-provider product models
        (``claude-*``, ``gpt-*``, ``gemini-*``); the prefixed form covers the
        OpenRouter discovery slugs, which LiteLLM keys as
        ``openrouter/<vendor>/<model>``.
        """

        keys: list[str] = [model_id]
        prefix = cls._LITELLM_PREFIX.get(provider)
        if prefix is not None:
            prefixed = f"{prefix}/{model_id}"
            if prefixed not in keys:
                keys.append(prefixed)
        return tuple(keys)

    def _model_cost_table(self) -> Mapping[str, Mapping[str, object]]:
        if self._model_cost is None:
            # Same offline guardrail as the pricing source: pin the bundled cost
            # map + disable the HF tokenizer download before touching litellm.
            from agent_runtime.pricing.litellm_runtime import (  # noqa: PLC0415
                apply_offline_litellm_config,
            )

            apply_offline_litellm_config()
            import litellm  # noqa: PLC0415 — lazy: keep import graph light, litellm is heavy

            self._model_cost = litellm.model_cost
        return self._model_cost

    @staticmethod
    def _sorted(
        records: list[CatalogModelRecord],
    ) -> tuple[CatalogModelRecord, ...]:
        """Deterministic order: provider ascending, then model id ascending.

        Replaces the models.dev release-date ordering — LiteLLM has no release
        date, and a stable provider/id order is reproducible run to run.
        """

        return tuple(
            sorted(records, key=lambda record: (record.provider, record.model_id))
        )

    @classmethod
    def _per_mtok(cls, per_token: float | None) -> float | None:
        if per_token is None:
            return None
        # Decimal(repr(...)) avoids float->Decimal representation drift; the
        # normalized float renders cleanly in the picker.
        as_million = Decimal(repr(per_token)) * _USD_PER_TOKEN_TO_PER_MILLION
        return float(as_million)

    @staticmethod
    def _float_field(row: Mapping[str, object], key: str) -> float | None:
        value = row.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _int_field(row: Mapping[str, object], key: str) -> int | None:
        value = row.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return None

    @staticmethod
    def _bool_field(row: Mapping[str, object], key: str) -> bool:
        return row.get(key) is True


__all__ = [
    "CatalogModelRecord",
    "CatalogModelSource",
    "LitellmModelSource",
    "ModelDisplayName",
    "ProductModelRegistry",
]
