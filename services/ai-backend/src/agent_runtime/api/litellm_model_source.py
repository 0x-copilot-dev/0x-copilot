"""Upstream-owned model discovery backed by LiteLLM's bundled catalog.

The picker does not carry a hand-maintained model inventory. It derives its
records from the installed ``litellm`` package's offline ``model_cost`` table,
using only generic product policy:

* the provider must have a run path in this product;
* the row must be a chat model with tool calling (Deep Agents requires tools);
* rows LiteLLM marks deprecated and fine-tuned placeholders are omitted.

That leaves LiteLLM responsible for additions, capability metadata, context
windows, and pricing while this module remains responsible only for what the
product can execute. Provider mirrors such as Vertex and Bedrock are excluded
by their LiteLLM provider slug, and provider prefixes are normalized away so a
direct Gemini model is not duplicated under two key forms.

Display names are derived from the normalized model id
(:class:`ModelDisplayName`) because LiteLLM carries no display name. Catalog
ordering is provider-then-id for deterministic output.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Final, Protocol, runtime_checkable

from agent_runtime.execution.contracts import ModelReasoningEffort, RuntimeContract

_LOGGER = logging.getLogger(__name__)


# LiteLLM returns cost as USD per single token; the picker shows USD per 1M
# tokens. Convert with Decimal so 5e-6 * 1e6 lands on exactly 5.0 (no binary
# float drift that would render as "5.000000000000001" in the picker).
_USD_PER_TOKEN_TO_PER_MILLION: Final[Decimal] = Decimal(1_000_000)


class CatalogModelRecord(RuntimeContract):
    """Normalized, trusted metadata for one catalog model.

    ``release_date`` / ``family`` are populated only by sources that carry them
    (:class:`~agent_runtime.api.models_dev_source.ModelsDevModelSource`).
    LiteLLM's table has neither — it ships ``deprecation_date`` and nothing else
    temporal — so records from :class:`LitellmModelSource` leave them ``None``
    and consumers must treat a missing release date as "unknown", never "old".
    """

    provider: str
    model_id: str
    display_name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None
    supports_reasoning: bool = False
    # The effort rungs THIS model accepts, cheapest-first, as advertised by the
    # source. Empty means the source carried no ladder — "unknown", never "none":
    # LiteLLM publishes no reasoning options at all, so every record from that
    # fallback leaves this empty while still being a reasoning model. Consumers
    # that need a control fall back to their own default rungs when it is empty.
    reasoning_efforts: tuple[ModelReasoningEffort, ...] = ()
    supports_tools: bool = False
    supports_attachments: bool = False
    # ISO ``YYYY-MM-DD``. Drives newest-first ordering in Settings -> Models and
    # the "latest per family" step of the default-set selection.
    release_date: str | None = None
    # Vendor product line (``claude-opus``, ``gpt-nano``, ``gemini-flash``).
    # This is the size axis: one representative per family, ranked by cost,
    # yields the small/medium/big ladder. See :class:`ModelSizeTierResolver`.
    family: str | None = None


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


@runtime_checkable
class CatalogModelSource(Protocol):
    """The seam :class:`~agent_runtime.api.model_catalog.ModelCatalog` builds on.

    A thin protocol so tests can inject a fake source (e.g. to exercise the
    ``supports_provider`` filter with an out-of-allowlist provider record)
    without constructing a full :class:`LitellmModelSource`.
    """

    def records(self) -> tuple[CatalogModelRecord, ...]: ...


class CompositeModelSource:
    """Union several :class:`CatalogModelSource` s into one.

    Distinct from a FALLBACK chain, and the difference matters. A fallback asks
    the next source only when the previous one failed — right when two sources
    describe the SAME catalog (models.dev and LiteLLM both describe the frontier
    vendors, so serving both would double every row). A union asks all of them —
    right when each source owns a catalog the others do not carry, which is the
    case for a gateway like Virtuals.

    Source order is preserved, and so is each source's internal order, because
    :class:`~agent_runtime.api.model_catalog.ModelCatalog` de-duplicates by id
    keeping the FIRST occurrence's position. Pass the richer source first.

    A source that raises is skipped rather than allowed to empty the picker: one
    upstream having a bad day must not take the whole catalog down with it.
    """

    def __init__(self, *sources: CatalogModelSource) -> None:
        self._sources = sources

    def records(self) -> tuple[CatalogModelRecord, ...]:
        collected: list[CatalogModelRecord] = []
        for source in self._sources:
            try:
                collected.extend(source.records())
            except Exception as exc:  # noqa: BLE001 - one bad source must not
                # empty the catalog; the others still serve.
                _LOGGER.warning(
                    "Catalog source %s failed; skipping it: %s",
                    type(source).__name__,
                    exc,
                )
        return tuple(collected)


class LitellmModelSource:
    """Discover executable chat models from LiteLLM's upstream-owned table."""

    class _Fields:
        """Stable LiteLLM ``model_cost`` field names — pinned so a rename fails here."""

        PROVIDER = "litellm_provider"
        MODE = "mode"
        DEPRECATION_DATE = "deprecation_date"
        INPUT_COST_PER_TOKEN = "input_cost_per_token"
        OUTPUT_COST_PER_TOKEN = "output_cost_per_token"
        MAX_INPUT_TOKENS = "max_input_tokens"
        MAX_TOKENS = "max_tokens"
        MAX_OUTPUT_TOKENS = "max_output_tokens"
        SUPPORTS_REASONING = "supports_reasoning"
        SUPPORTS_FUNCTION_CALLING = "supports_function_calling"
        SUPPORTS_VISION = "supports_vision"
        SUPPORTS_PDF_INPUT = "supports_pdf_input"

    # This is provider policy, not a model inventory. These are the remote
    # providers that both have a product run path and an authoritative,
    # globally-selectable LiteLLM catalog. Ollama is deliberately absent:
    # its actual model set is local-machine-specific and must be discovered
    # from that user's Ollama server rather than advertised from a global table.
    _DISCOVERABLE_PROVIDERS: Final[Mapping[str, str]] = {
        "anthropic": "anthropic",
        "openai": "openai",
        "gemini": "gemini",
        "openrouter": "openrouter",
    }
    _CHAT_MODE: Final[str] = "chat"

    def __init__(
        self,
        *,
        model_cost: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        # Injected in tests to stay hermetic/deterministic; ``None`` resolves
        # from the installed ``litellm`` package on first use.
        self._model_cost = model_cost

    def records(self) -> tuple[CatalogModelRecord, ...]:
        """Return eligible LiteLLM rows, normalized and deterministically deduped."""

        records: dict[tuple[str, str], CatalogModelRecord] = {}
        for raw_key, row in self._model_cost_table().items():
            candidate = self._candidate(raw_key=raw_key, row=row)
            if candidate is None:
                continue
            dedupe_key = (candidate.provider, candidate.model_id)
            # Prefer the explicit ``provider/model`` row when LiteLLM also
            # carries a bare alias (notably Gemini). Both describe the same
            # normalized picker entry, but the provider-qualified row is the
            # direct provider's canonical table record.
            if dedupe_key not in records or raw_key.startswith(
                f"{candidate.provider}/"
            ):
                records[dedupe_key] = candidate
        return self._sorted(list(records.values()))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _candidate(
        self,
        *,
        raw_key: str,
        row: Mapping[str, object],
    ) -> CatalogModelRecord | None:
        """Map one eligible LiteLLM row into the product's provider namespace."""

        provider_value = row.get(self._Fields.PROVIDER)
        if not isinstance(provider_value, str):
            return None
        provider = provider_value.strip().lower()
        prefix = self._DISCOVERABLE_PROVIDERS.get(provider)
        if prefix is None:
            return None
        if row.get(self._Fields.MODE) != self._CHAT_MODE:
            return None
        if not self._bool_field(row, self._Fields.SUPPORTS_FUNCTION_CALLING):
            return None
        if row.get(self._Fields.DEPRECATION_DATE):
            return None

        model_id = self._normalize_model_id(raw_key=raw_key, prefix=prefix)
        if model_id is None or model_id.startswith("ft:"):
            return None
        display_name = self._display_name(provider=provider, model_id=model_id)
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
    def _normalize_model_id(*, raw_key: str, prefix: str) -> str | None:
        """Strip a direct-provider prefix and reject other provider namespaces."""

        key = raw_key.strip()
        if not key:
            return None
        qualified_prefix = f"{prefix}/"
        if key.startswith(qualified_prefix):
            model_id = key[len(qualified_prefix) :]
        elif "/" in key:
            # A slash without this row's own provider prefix is a mirror or
            # namespace alias, not a direct model id for the product run path.
            return None
        else:
            model_id = key
        return model_id or None

    @staticmethod
    def _display_name(*, provider: str, model_id: str) -> str:
        """Derive a readable direct or OpenRouter label without a model list."""

        if provider == "openrouter":
            leaf = model_id.rsplit("/", maxsplit=1)[-1]
            return f"{ModelDisplayName.derive(leaf)} (OpenRouter)"
        return ModelDisplayName.derive(model_id)

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
    "CompositeModelSource",
    "LitellmModelSource",
    "ModelDisplayName",
]
