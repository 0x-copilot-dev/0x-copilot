"""Pricing overrides (B3 / P12 Step 2).

Overrides are the escape hatch for ``(provider, model_name, region)``
triples where the primary source (LiteLLM, or the YAML seed in
air-gapped boots) does not produce the value we want to bill at. Two
canonical use cases:

1. **Custom / fine-tune models** that LiteLLM doesn't ship rates for
   (e.g. an internal Anthropic fine-tune named ``claude-internal-x``).
2. **Migration legacy** — at the Step 2 cutover, any seed value that
   diverges from LiteLLM gets pinned to the legacy value via an
   override so the **active row at switchover reads identically**.
   See [`config/pricing_overrides.yaml`](../../../config/pricing_overrides.yaml)
   for the current pinned set.

Every override entry requires a ``reason`` field. The loader fails
closed if it is missing — the design assumes overrides are reviewable
by whoever inherits the catalog. ``pricing.override_applied`` is
logged once per ingest per override.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import yaml

from agent_runtime.persistence.records import ModelPricingRecord


_LOGGER = logging.getLogger("agent_runtime.pricing.overrides")

DEFAULT_OVERRIDES_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3] / "config" / "pricing_overrides.yaml"
)

DEFAULT_PRICING_VERSION: Final[str] = "manual-overrides"


class PricingOverrideLoadError(ValueError):
    """Override entry rejected at load time (missing ``reason``, bad shape, …)."""


class _Keys:
    """Stable YAML field names — pinned so a rename fails loudly here."""

    OVERRIDES = "overrides"
    OVERRIDES_VERSION = "overrides_version"
    PROVIDER = "provider"
    MODEL_NAME = "model_name"
    REGION = "region"
    EFFECTIVE_FROM = "effective_from"
    INPUT = "input_per_1m_micro_usd"
    OUTPUT = "output_per_1m_micro_usd"
    CACHED = "cached_input_per_1m_micro_usd"
    CONTEXT = "context_window_tokens"
    REASON = "reason"


class PricingOverrideSource:
    """Read ``pricing_overrides.yaml`` and yield ``ModelPricingRecord`` rows.

    The source is stateless. Path resolution defaults to
    ``services/ai-backend/config/pricing_overrides.yaml`` so the file
    lives alongside other deploy-time config; tests inject a tmp path.

    Override entries are yielded with ``pricing_source="override"`` and
    a ``pricing_version`` derived from the YAML's top-level
    ``overrides_version`` field (fallback: ``"manual-overrides"``).
    """

    PRICING_SOURCE = "override"

    @classmethod
    def load_all(
        cls,
        *,
        overrides_path: Path | None = None,
        effective_from: datetime | None = None,
    ) -> tuple[ModelPricingRecord, ...]:
        """Return every override row from the YAML file.

        Returns an empty tuple if the file does not exist — overrides
        are optional. Raises :class:`PricingOverrideLoadError` if the
        file exists but any entry is malformed.
        """

        path = overrides_path or DEFAULT_OVERRIDES_PATH
        if not path.exists():
            return ()

        text = path.read_text()
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise PricingOverrideLoadError(f"failed to parse {path}: {exc}") from exc

        if data is None:
            return ()
        if not isinstance(data, dict):
            raise PricingOverrideLoadError(
                f"{path} root must be a YAML mapping (got {type(data).__name__})"
            )

        overrides = data.get(_Keys.OVERRIDES) or []
        if not isinstance(overrides, list):
            raise PricingOverrideLoadError(
                f"{path} `overrides:` must be a list (got {type(overrides).__name__})"
            )

        pricing_version = str(
            data.get(_Keys.OVERRIDES_VERSION) or DEFAULT_PRICING_VERSION
        )
        ingest_at = cls._minute_floor(effective_from or datetime.now(timezone.utc))

        return tuple(
            cls._row_to_record(
                entry=entry,
                pricing_version=pricing_version,
                ingest_at=ingest_at,
                source_path=path,
            )
            for entry in overrides
        )

    @classmethod
    def _row_to_record(
        cls,
        *,
        entry: object,
        pricing_version: str,
        ingest_at: datetime,
        source_path: Path,
    ) -> ModelPricingRecord:
        if not isinstance(entry, dict):
            raise PricingOverrideLoadError(
                f"{source_path}: override entries must be mappings"
            )

        # Reason is required — overrides are reviewable, and a row with
        # no rationale is a row that will outlive its purpose silently.
        reason = entry.get(_Keys.REASON)
        if not isinstance(reason, str) or not reason.strip():
            raise PricingOverrideLoadError(
                f"{source_path}: override missing required `reason` field for "
                f"{entry.get(_Keys.PROVIDER)!r}/{entry.get(_Keys.MODEL_NAME)!r}"
            )

        provider = cls._required_str(entry, _Keys.PROVIDER, source_path)
        model_name = cls._required_str(entry, _Keys.MODEL_NAME, source_path)
        region = str(entry.get(_Keys.REGION) or "global")

        effective_from_raw = entry.get(_Keys.EFFECTIVE_FROM)
        effective_from = (
            cls._parse_datetime(effective_from_raw)
            if effective_from_raw is not None
            else ingest_at
        )

        input_per_1m = cls._required_int(entry, _Keys.INPUT, source_path)
        output_per_1m = cls._required_int(entry, _Keys.OUTPUT, source_path)
        cached_input = entry.get(_Keys.CACHED)
        context_window = entry.get(_Keys.CONTEXT)

        _LOGGER.info(
            "pricing.override_applied",
            extra={
                "provider": provider,
                "model_name": model_name,
                "region": region,
                "reason": reason,
            },
        )

        return ModelPricingRecord(
            provider=provider,
            model_name=model_name,
            region=region,
            effective_from=effective_from,
            input_per_1m_micro_usd=input_per_1m,
            output_per_1m_micro_usd=output_per_1m,
            cached_input_per_1m_micro_usd=(
                int(cached_input) if cached_input is not None else None
            ),
            context_window_tokens=(
                int(context_window) if context_window is not None else None
            ),
            pricing_source=cls.PRICING_SOURCE,
            pricing_version=pricing_version,
        )

    @staticmethod
    def _required_str(entry: dict[str, Any], key: str, source_path: Path) -> str:
        value = entry.get(key)
        if not isinstance(value, str) or not value:
            raise PricingOverrideLoadError(
                f"{source_path}: override missing required field {key!r}"
            )
        return value

    @staticmethod
    def _required_int(entry: dict[str, Any], key: str, source_path: Path) -> int:
        value = entry.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise PricingOverrideLoadError(
                f"{source_path}: override field {key!r} must be int (got "
                f"{type(value).__name__})"
            )
        return value

    @staticmethod
    def _parse_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    @staticmethod
    def _minute_floor(value: datetime) -> datetime:
        return value.replace(second=0, microsecond=0)

    @staticmethod
    def by_key(
        records: Iterable[ModelPricingRecord],
    ) -> dict[tuple[str, str, str], ModelPricingRecord]:
        index: dict[tuple[str, str, str], ModelPricingRecord] = {}
        for record in records:
            index[(record.provider, record.model_name, record.region)] = record
        return index
