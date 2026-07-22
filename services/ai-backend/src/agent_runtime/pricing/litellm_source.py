"""LiteLLM-library-sourced pricing rates, looked up per (provider, model).

The pricing rate for a ``(provider, model)`` pair is read from the installed
``litellm`` package's bundled ``model_cost`` table at lookup time. This replaces
the previously-vendored ``model_prices.json`` snapshot plus the DB-persisted
catalog / ingest machinery — ``litellm.model_cost`` is the live catalog and ships
offline (no network at lookup time).

Reviewed overrides (``config/pricing_overrides.yaml``) win over LiteLLM for models
LiteLLM lacks or misprices (today: ``gemini-3-flash``); the override mechanism is
the sanctioned backstop.

LiteLLM returns rates as ``USD/token`` floats. They are converted to integer
``micro-USD per 1M tokens`` here using the same ``Decimal`` / ``ROUND_HALF_EVEN``
arithmetic as :class:`~agent_runtime.pricing.calculator.CostCalculator`, so the
stored ``cost_micro_usd`` BIGINT contract and banker's rounding are preserved: this
class is only the *rate provider*; the calculator remains the rounding boundary
for the final per-usage cost.

A ``(provider, model)`` that LiteLLM does not price and no override covers returns
``None`` — the established "pricing unavailable" signal that the usage recorder and
budget estimator already handle (``cost_micro_usd=None``: no charge, no crash). A
``pricing.litellm_unpriced`` log line marks it so the miss is never silent.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from importlib.metadata import PackageNotFoundError, version as _package_version
from pathlib import Path
from typing import Final

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.overrides import PricingOverrideSource


_LOGGER = logging.getLogger("agent_runtime.pricing.litellm_source")

# 1 USD = 1_000_000 micro_usd; rate is per 1M tokens. So
# per_token_usd → per_1m_micro_usd is value * 1e6 (tokens) * 1e6 (micro).
_USD_TO_MICRO_PER_MILLION: Final[Decimal] = Decimal(10) ** 12


class _Fields:
    """Stable LiteLLM ``model_cost`` field names — pinned so a rename fails here."""

    INPUT_COST_PER_TOKEN = "input_cost_per_token"
    OUTPUT_COST_PER_TOKEN = "output_cost_per_token"
    CACHE_READ_INPUT_TOKEN_COST = "cache_read_input_token_cost"
    MAX_INPUT_TOKENS = "max_input_tokens"
    MAX_TOKENS = "max_tokens"


class LitellmRateSource:
    """Return a :class:`ModelPricingRecord` for a ``(provider, model)`` from LiteLLM.

    Shaped as a ``lookup_pricing`` port so it drops straight into
    :class:`~agent_runtime.pricing.catalog.ModelPricingCatalog` in place of the
    persistence-backed catalog — callers (usage recorder, budget estimator,
    conversation context) are unchanged.

    Overrides win on ``(provider, model_name, region)`` collision. The override
    index and LiteLLM's ``model_cost`` are loaded lazily on first lookup and
    cached for the lifetime of the instance (both are process-static: LiteLLM's
    table is bundled, overrides are deploy-time config).
    """

    PRICING_SOURCE: Final[str] = "litellm"
    UNPRICED_LOG_EVENT: Final[str] = "pricing.litellm_unpriced"

    # Canonical run-path provider slug → LiteLLM key prefix. Used to build the
    # ``provider/model`` candidate key form (bare ``model`` is tried first).
    _LITELLM_PREFIX: Final[Mapping[str, str]] = {
        "anthropic": "anthropic",
        "openai": "openai",
        "gemini": "gemini",
        "openrouter": "openrouter",
        "ollama": "ollama",
    }

    def __init__(
        self,
        *,
        overrides_path: Path | None = None,
        model_cost: Mapping[str, Mapping[str, object]] | None = None,
        litellm_version: str | None = None,
    ) -> None:
        self._overrides_path = overrides_path
        # Injected in tests to stay hermetic/deterministic; ``None`` resolves
        # from the installed ``litellm`` package on first use.
        self._model_cost = model_cost
        self._version = litellm_version
        self._override_index: dict[tuple[str, str, str], ModelPricingRecord] | None = (
            None
        )

    async def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        """Return the pricing record for ``(provider, model_name, region)``.

        Override wins first; then LiteLLM; else ``None`` (unpriced). Never
        raises — an unknown model is a safe miss, not a run-fatal error.
        """

        canonical = self._canonical_provider(provider)

        override = self._overrides().get((canonical, model_name, region))
        if override is not None:
            return override

        record = self._litellm_record(
            provider=canonical,
            model_name=model_name,
            region=region,
            at=at,
        )
        if record is None:
            _LOGGER.info(
                self.UNPRICED_LOG_EVENT,
                extra={
                    "metadata": {
                        "provider": canonical,
                        "model_name": model_name,
                        "region": region,
                    }
                },
            )
        return record

    def _litellm_record(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        row = self._litellm_row(provider=provider, model_name=model_name)
        if row is None:
            return None

        input_cost = self._float_field(row, _Fields.INPUT_COST_PER_TOKEN)
        output_cost = self._float_field(row, _Fields.OUTPUT_COST_PER_TOKEN)
        # Both rate fields are required. A row missing either (e.g. some
        # embedding rows have no output rate) is treated as unpriced rather
        # than billed against a partial rate.
        if input_cost is None or output_cost is None:
            return None

        cached_cost = self._float_field(row, _Fields.CACHE_READ_INPUT_TOKEN_COST)
        context_window = self._int_field(
            row, _Fields.MAX_INPUT_TOKENS
        ) or self._int_field(row, _Fields.MAX_TOKENS)

        litellm_version = self._litellm_version()
        return ModelPricingRecord(
            id=self._pricing_id(
                litellm_version=litellm_version,
                provider=provider,
                model_name=model_name,
                region=region,
            ),
            provider=provider,
            model_name=model_name,
            region=region,
            effective_from=at,
            input_per_1m_micro_usd=self._usd_per_token_to_micro_per_million(input_cost),
            output_per_1m_micro_usd=self._usd_per_token_to_micro_per_million(
                output_cost
            ),
            cached_input_per_1m_micro_usd=(
                self._usd_per_token_to_micro_per_million(cached_cost)
                if cached_cost is not None
                else None
            ),
            context_window_tokens=context_window,
            pricing_source=self.PRICING_SOURCE,
            pricing_version=f"litellm:{litellm_version}",
        )

    def _litellm_row(
        self,
        *,
        provider: str,
        model_name: str,
    ) -> Mapping[str, object] | None:
        """First matching ``model_cost`` row across candidate key forms."""

        table = self._model_cost_table()
        for key in self._candidate_keys(provider=provider, model_name=model_name):
            row = table.get(key)
            if isinstance(row, Mapping):
                return row
        return None

    @classmethod
    def _candidate_keys(cls, *, provider: str, model_name: str) -> tuple[str, ...]:
        """LiteLLM key forms to try, bare id first then ``provider/model``.

        Bare ``model_name`` matches the direct-provider product models
        (``claude-*``, ``gpt-*``, ``gemini-*``); the prefixed form covers
        aggregator/local slugs whose ids embed a vendor path
        (``openrouter/anthropic/claude-…``, ``ollama/llama3``).
        """

        keys: list[str] = [model_name]
        prefix = cls._LITELLM_PREFIX.get(provider)
        if prefix is not None:
            prefixed = f"{prefix}/{model_name}"
            if prefixed not in keys:
                keys.append(prefixed)
        return tuple(keys)

    def _overrides(self) -> dict[tuple[str, str, str], ModelPricingRecord]:
        if self._override_index is None:
            records = PricingOverrideSource.load_all(
                overrides_path=self._overrides_path
            )
            # Normalise the override provider slug the same way the lookup
            # provider is normalised so ``google`` in the YAML still matches a
            # ``gemini`` run-path lookup (and vice versa).
            self._override_index = {
                (
                    self._canonical_provider(record.provider),
                    record.model_name,
                    record.region,
                ): record
                for record in records
            }
        return self._override_index

    def _model_cost_table(self) -> Mapping[str, Mapping[str, object]]:
        if self._model_cost is None:
            # Shared offline posture (pins the bundled cost map, disables the HF
            # tokenizer download) so pricing, catalog, and counting never fetch.
            from agent_runtime.pricing.litellm_runtime import (  # noqa: PLC0415
                apply_offline_litellm_config,
            )

            apply_offline_litellm_config()
            import litellm  # noqa: PLC0415 — lazy: keep import graph light, litellm is heavy

            self._model_cost = litellm.model_cost
        return self._model_cost

    def _litellm_version(self) -> str:
        if self._version is None:
            try:
                self._version = _package_version("litellm")
            except PackageNotFoundError:  # pragma: no cover — package always present
                self._version = "unknown"
        return self._version

    @staticmethod
    def _pricing_id(
        *,
        litellm_version: str,
        provider: str,
        model_name: str,
        region: str,
    ) -> str:
        """Stable snapshot id: pinned per LiteLLM version + model coordinates.

        Immutability: while ``litellm`` stays pinned, the same model yields the
        same ``pricing_id`` + rates on every lookup, so historical usage rows
        keep a reproducible price reference. A LiteLLM upgrade changes the
        version segment → a new id, leaving already-stamped rows untouched.
        """

        return f"litellm:{litellm_version}:{provider}:{model_name}:{region}"

    @staticmethod
    def _canonical_provider(provider: str) -> str:
        """Canonical run-path slug for ``provider`` (reuses the run-path SSOT).

        Falls back to the lower-cased raw slug when the run path doesn't
        recognise it, so lookup never raises on an unexpected provider string.
        """

        from agent_runtime.execution.models import (  # noqa: PLC0415 — lazy: avoid import cycle
            ModelConfigResolver,
        )

        return (
            ModelConfigResolver.canonical_provider(provider) or provider.strip().lower()
        )

    @staticmethod
    def _usd_per_token_to_micro_per_million(value: float) -> int:
        """Convert ``USD/token`` to integer ``micro-USD per 1M tokens``.

        Uses ``Decimal`` + ``ROUND_HALF_EVEN`` so the rounding semantics match
        :meth:`CostCalculator._token_cost` exactly. Non-positive values map to
        0 (the calculator's fail-soft contract).
        """

        if value <= 0:
            return 0
        # Decimal from str avoids float→Decimal binary-representation drift.
        as_decimal = Decimal(repr(value))
        per_million_micro = as_decimal * _USD_TO_MICRO_PER_MILLION
        return int(per_million_micro.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))

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
