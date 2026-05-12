"""LiteLLM-sourced pricing ingestion (B3 / P12 Step 1).

LiteLLM ships ``model_prices_and_context_window.json`` — a community-
maintained per-model rate catalog covering Anthropic, OpenAI, Google,
and the long tail. We vendor that file under ``litellm_data/`` and
parse it into :class:`ModelPricingRecord` instances using the same
``Decimal`` / ``ROUND_HALF_EVEN`` arithmetic ``CostCalculator`` uses,
so the rounded micro-USD values stay byte-identical to the calculator
that consumes them.

Step 1 of the P12 plan is observation-only: the source produces records
but nothing in the runtime upserts them yet. The
``compare_litellm`` CLI uses this module to print a parity diff
against the YAML seeds.

Why vendored JSON instead of ``import litellm``: the runtime version of
``litellm`` pins ``openai`` and ``pydantic`` at strictly older versions
than agent-runtime requires (P12 PRD §9 "LiteLLM dependency surface" —
the documented fallback path). Refreshing the vendored copy is a
dev-time script, not a runtime dependency.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Final

from agent_runtime.persistence.records import ModelPricingRecord


_LOGGER = logging.getLogger("agent_runtime.pricing.litellm_source")

LITELLM_DATA_PATH: Final[Path] = (
    Path(__file__).resolve().parent / "litellm_data" / "model_prices.json"
)

# Provider strings as they appear in our YAML seeds and runtime contexts.
# LiteLLM's `litellm_provider` field already uses these canonical names
# for the providers we care about; the mapping is identity but explicit
# so a future LiteLLM rename doesn't silently miscategorise rows.
_CANONICAL_PROVIDERS: Final[frozenset[str]] = frozenset(
    {"anthropic", "openai", "google", "vertex_ai", "azure", "cohere", "mistral"}
)

# Modes we ingest. LiteLLM's `mode` distinguishes chat-completion-style
# models from embedding / image-generation / audio rows that are billed
# through different paths we don't run.
_BILLABLE_MODES: Final[frozenset[str]] = frozenset({"chat", "completion", "responses"})

_SKIPPED_MODES: Final[frozenset[str]] = frozenset(
    {
        "embedding",
        "image_generation",
        "audio_transcription",
        "audio_speech",
        "moderation",
        "rerank",
    }
)

# 1 USD = 1_000_000 micro_usd; rate is per 1M tokens. So
# per_token_usd → per_1m_micro_usd is value * 1e6 (tokens) * 1e6 (micro).
_USD_TO_MICRO_PER_MILLION: Final[Decimal] = Decimal(10) ** 12

_SAMPLE_SPEC_KEY: Final[str] = "sample_spec"


class _Fields:
    """Stable LiteLLM JSON field names — pinned so a rename surfaces here."""

    PROVIDER = "litellm_provider"
    MODE = "mode"
    INPUT_COST_PER_TOKEN = "input_cost_per_token"
    OUTPUT_COST_PER_TOKEN = "output_cost_per_token"
    CACHE_READ_INPUT_TOKEN_COST = "cache_read_input_token_cost"
    OUTPUT_REASONING_TOKEN_COST = "output_reasoning_token_cost"
    MAX_INPUT_TOKENS = "max_input_tokens"
    MAX_TOKENS = "max_tokens"


class LiteLLMPricingSource:
    """Read the vendored LiteLLM ``model_prices.json`` and yield records.

    The source is stateless — every call to :meth:`load_all` re-parses
    the JSON. Callers that need caching should wrap with
    :class:`agent_runtime.pricing.catalog.ModelPricingCatalog` after
    upserting the records into the persistence port.

    Step 1 of P12: the source produces records; the existing
    ``scripts/usage/seed_pricing.py`` does not yet consume them. The
    ``compare_litellm`` CLI uses this module directly to print parity
    diffs vs the YAML seeds.
    """

    DEFAULT_PRICING_VERSION_PREFIX = "litellm"

    @classmethod
    def load_all(
        cls,
        *,
        data_path: Path | None = None,
        effective_from: datetime | None = None,
        pricing_version: str | None = None,
    ) -> tuple[ModelPricingRecord, ...]:
        """Return every billable model in the vendored LiteLLM catalog.

        Args:
            data_path: override the vendored JSON path (tests).
            effective_from: timestamp stamped on every record. Defaults
                to ``datetime.now(timezone.utc)`` truncated to minute.
            pricing_version: version string stamped on every record.
                Defaults to ``litellm-<effective_from-isoformat>``.

        Skipped rows (with reason) are logged via
        ``pricing.litellm_skipped``. Reasoning-only rate columns are
        dropped with a ``pricing.reasoning_field_dropped`` log line so
        the future reasoning-billing PRD has data.
        """

        path = data_path or LITELLM_DATA_PATH
        raw: Mapping[str, Mapping[str, object]] = json.loads(path.read_text())

        stamp_at = cls._minute_floor(effective_from or datetime.now(timezone.utc))
        version = (
            pricing_version
            or f"{cls.DEFAULT_PRICING_VERSION_PREFIX}-{stamp_at.date().isoformat()}"
        )

        records: list[ModelPricingRecord] = []
        for key, row in raw.items():
            if key == _SAMPLE_SPEC_KEY:
                continue
            if not isinstance(row, Mapping):
                _LOGGER.debug(
                    "pricing.litellm_skipped",
                    extra={"key": key, "reason": "not_mapping"},
                )
                continue
            try:
                record = cls._row_to_record(
                    key=key,
                    row=row,
                    effective_from=stamp_at,
                    pricing_version=version,
                )
            except _SkipRow as exc:
                _LOGGER.debug(
                    "pricing.litellm_skipped",
                    extra={"key": key, "reason": exc.reason},
                )
                continue
            records.append(record)
        return tuple(records)

    @classmethod
    def _row_to_record(
        cls,
        *,
        key: str,
        row: Mapping[str, object],
        effective_from: datetime,
        pricing_version: str,
    ) -> ModelPricingRecord:
        mode = row.get(_Fields.MODE)
        if isinstance(mode, str) and mode in _SKIPPED_MODES:
            raise _SkipRow(f"mode={mode}")
        if isinstance(mode, str) and mode not in _BILLABLE_MODES:
            # Anything that's neither billable-chat nor a known skip mode
            # — be conservative and skip (e.g. "video_generation").
            raise _SkipRow(f"mode={mode}")

        provider = row.get(_Fields.PROVIDER)
        if not isinstance(provider, str) or not provider:
            raise _SkipRow("missing_provider")

        input_cost = cls._float_field(row, _Fields.INPUT_COST_PER_TOKEN)
        output_cost = cls._float_field(row, _Fields.OUTPUT_COST_PER_TOKEN)
        if input_cost is None or output_cost is None:
            raise _SkipRow("missing_input_or_output_cost")

        if _Fields.OUTPUT_REASONING_TOKEN_COST in row:
            _LOGGER.info(
                "pricing.reasoning_field_dropped",
                extra={
                    "key": key,
                    "provider": provider,
                    "value": row.get(_Fields.OUTPUT_REASONING_TOKEN_COST),
                },
            )

        cached_input_cost = cls._float_field(row, _Fields.CACHE_READ_INPUT_TOKEN_COST)
        context_window = cls._int_field(
            row, _Fields.MAX_INPUT_TOKENS
        ) or cls._int_field(row, _Fields.MAX_TOKENS)

        model_name = cls._canonical_model_name(key)

        return ModelPricingRecord(
            provider=provider,
            model_name=model_name,
            region="global",
            effective_from=effective_from,
            input_per_1m_micro_usd=cls._usd_per_token_to_micro_per_million(input_cost),
            output_per_1m_micro_usd=cls._usd_per_token_to_micro_per_million(
                output_cost
            ),
            cached_input_per_1m_micro_usd=(
                cls._usd_per_token_to_micro_per_million(cached_input_cost)
                if cached_input_cost is not None
                else None
            ),
            context_window_tokens=context_window,
            pricing_source="litellm",
            pricing_version=pricing_version,
        )

    @staticmethod
    def _canonical_model_name(key: str) -> str:
        """Strip provider prefixes LiteLLM puts on some keys.

        LiteLLM ships multiple aliases for the same model — bare
        (``claude-opus-4-7``), slash-prefixed (``anthropic/claude-opus-4-7``),
        bedrock-style (``us.anthropic.claude-opus-4-7``). We keep the
        bare form so the canonical name matches our YAML seeds.
        Region-prefixed keys (``us.``, ``eu.``, ``au.``, ``global.``)
        are normalised by stripping the leading region segment; if a
        provider segment remains, it's stripped too.
        """

        name = key
        # Strip slash-prefixed provider (e.g. "anthropic/claude-...").
        if "/" in name:
            name = name.split("/", 1)[1]
        # Strip Bedrock-style region + provider dot-prefixes:
        # "us.anthropic.claude-..." → "claude-..."
        # "anthropic.claude-..." → "claude-..."
        segments = name.split(".")
        # Drop leading segments that are known region tags or canonical
        # providers. Stop at the first segment that isn't a tag.
        region_tags = {"us", "eu", "au", "ap", "global"}
        i = 0
        while i < len(segments) - 1 and (
            segments[i] in region_tags or segments[i] in _CANONICAL_PROVIDERS
        ):
            i += 1
        name = ".".join(segments[i:])
        return name

    @staticmethod
    def _usd_per_token_to_micro_per_million(value: float) -> int:
        """Convert ``USD/token`` to ``micro-USD per million tokens``.

        Uses ``Decimal`` + ``ROUND_HALF_EVEN`` so the rounding semantics
        match ``CostCalculator._token_cost`` exactly. Returns 0 for
        negative values (calculator fail-soft contract).
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
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _int_field(row: Mapping[str, object], key: str) -> int | None:
        value = row.get(key)
        if value is None:
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            return int(value)
        return None

    @staticmethod
    def _minute_floor(value: datetime) -> datetime:
        return value.replace(second=0, microsecond=0)

    @classmethod
    def by_key(
        cls,
        records: Iterable[ModelPricingRecord],
    ) -> dict[tuple[str, str, str], ModelPricingRecord]:
        """Index records by ``(provider, model_name, region)`` for diffing."""

        index: dict[tuple[str, str, str], ModelPricingRecord] = {}
        for record in records:
            index[(record.provider, record.model_name, record.region)] = record
        return index


class _SkipRow(Exception):
    """Raised by the row converter to skip with a reason; not user-facing."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
