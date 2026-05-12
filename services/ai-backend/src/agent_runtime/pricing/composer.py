"""Compose the active pricing catalog from one primary source plus overrides.

Step 2 of the P12 plan switches the source-of-truth from the hand-
authored YAML seeds to LiteLLM. The composer is the single point that
decides which records to upsert:

1. **Primary source** — LiteLLM in production; YAML seeds in air-gapped
   boots. Chosen by ``PricingComposer.load(primary_source=...)``.
2. **Overrides** — ``pricing_overrides.yaml`` entries, each with a
   ``reason`` field. Overrides win on
   ``(provider, model_name, region)`` collision.

Re-ingest is idempotent at the upsert layer: ``PersistencePort.upsert_pricing``
closes any prior active row whose ``effective_from`` is strictly earlier,
then inserts the new one. Callers (typically
``scripts/usage/seed_pricing.py``) iterate the composer output and
hand each record to ``upsert_pricing``.

There is exactly **one merge rule** here so the precedence is unambiguous:
``overrides`` replaces primary records on key collision; primary records
with no override pass through unchanged. The composer never mutates the
underlying records.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Literal

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.litellm_source import LiteLLMPricingSource
from agent_runtime.pricing.overrides import PricingOverrideSource
from agent_runtime.pricing.seed_loader import PricingSeedLoader


_LOGGER = logging.getLogger("agent_runtime.pricing.composer")

PrimarySource = Literal["litellm", "yaml"]

DEFAULT_PRIMARY_SOURCE: Final[PrimarySource] = "litellm"


class PricingComposerError(ValueError):
    """Composer rejected an invalid configuration before producing records."""


class PricingComposer:
    """Merge one primary source with overrides into one record sequence.

    Stateless. Each :meth:`load` call re-reads both sources. Caller is
    responsible for upserting the returned records through a
    persistence port.
    """

    PRIMARY_LITELLM: Final[PrimarySource] = "litellm"
    PRIMARY_YAML: Final[PrimarySource] = "yaml"

    @classmethod
    def load(
        cls,
        *,
        primary_source: PrimarySource = DEFAULT_PRIMARY_SOURCE,
        overrides_path: Path | None = None,
        litellm_data_path: Path | None = None,
        seed_dir: Path | None = None,
        effective_from: datetime | None = None,
    ) -> tuple[ModelPricingRecord, ...]:
        """Return the merged record list to upsert.

        ``effective_from`` is stamped on every LiteLLM and override
        record that doesn't carry an explicit ``effective_from`` of its
        own. Defaults to ``datetime.now(timezone.utc)`` truncated to
        minute. YAML seed records always use their authored
        ``effective_from`` — that's how the YAML seed format works.
        """

        if primary_source not in (cls.PRIMARY_LITELLM, cls.PRIMARY_YAML):
            raise PricingComposerError(
                f"unknown primary_source {primary_source!r}; "
                f"expected one of: {cls.PRIMARY_LITELLM!r}, {cls.PRIMARY_YAML!r}"
            )

        stamp_at = (effective_from or datetime.now(timezone.utc)).replace(
            second=0, microsecond=0
        )

        primary = cls._load_primary(
            primary_source=primary_source,
            litellm_data_path=litellm_data_path,
            seed_dir=seed_dir,
            stamp_at=stamp_at,
        )
        overrides = PricingOverrideSource.load_all(
            overrides_path=overrides_path,
            effective_from=stamp_at,
        )

        merged = cls._merge(primary=primary, overrides=overrides)

        _LOGGER.info(
            "pricing.startup_loaded",
            extra={
                "primary_source": primary_source,
                "primary_count": len(primary),
                "override_count": len(overrides),
                "merged_count": len(merged),
            },
        )
        return merged

    @classmethod
    def _load_primary(
        cls,
        *,
        primary_source: PrimarySource,
        litellm_data_path: Path | None,
        seed_dir: Path | None,
        stamp_at: datetime,
    ) -> tuple[ModelPricingRecord, ...]:
        if primary_source == cls.PRIMARY_LITELLM:
            return LiteLLMPricingSource.load_all(
                data_path=litellm_data_path,
                effective_from=stamp_at,
            )
        # PRIMARY_YAML
        return PricingSeedLoader.load_all(seed_dir=seed_dir)

    @classmethod
    def _merge(
        cls,
        *,
        primary: Iterable[ModelPricingRecord],
        overrides: Iterable[ModelPricingRecord],
    ) -> tuple[ModelPricingRecord, ...]:
        """Overrides replace matching primary rows; non-collisions pass through."""

        override_index = PricingOverrideSource.by_key(overrides)
        used_overrides: set[tuple[str, str, str]] = set()

        merged: list[ModelPricingRecord] = []
        for record in primary:
            key = (record.provider, record.model_name, record.region)
            if key in override_index:
                merged.append(override_index[key])
                used_overrides.add(key)
            else:
                merged.append(record)

        # Overrides that didn't collide with a primary row still need to
        # be emitted — they represent models LiteLLM doesn't ship (or
        # YAML seeds don't carry).
        for key, override_record in override_index.items():
            if key not in used_overrides:
                merged.append(override_record)

        return tuple(merged)
