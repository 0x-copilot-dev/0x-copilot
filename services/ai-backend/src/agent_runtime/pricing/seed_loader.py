"""Load pricing seeds from YAML files into the catalog (B3).

Idempotent: re-running the loader closes any prior active row whose
``effective_from`` is strictly earlier than the seed's, then inserts the
seed row. If the latest active row already matches the seed
(``pricing_version`` equal), the loader skips it so re-runs are no-ops
when nothing changed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

import yaml

from agent_runtime.persistence.records import ModelPricingRecord


SEED_DIR = Path(__file__).resolve().parent / "seeds"


class PricingSeedLoader:
    """Read YAML seed files into ``ModelPricingRecord`` instances.

    The loader does not touch persistence directly; it produces records
    and yields them to the caller (typically ``scripts/usage/seed_pricing.py``)
    which is responsible for upserting via the persistence port. This
    keeps the loader unit-testable without a DB.
    """

    @classmethod
    def load_all(
        cls, *, seed_dir: Path | None = None
    ) -> tuple[ModelPricingRecord, ...]:
        target = seed_dir or SEED_DIR
        records: list[ModelPricingRecord] = []
        for path in sorted(target.glob("*.yaml")):
            records.extend(cls.load_file(path))
        return tuple(records)

    @classmethod
    def load_file(cls, path: Path) -> Iterable[ModelPricingRecord]:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"pricing seed {path} is not a YAML mapping")
        provider = str(data["provider"])
        pricing_version = str(data["pricing_version"])
        prices = data.get("prices") or []
        records: list[ModelPricingRecord] = []
        for price in prices:
            records.append(
                ModelPricingRecord(
                    provider=provider,
                    model_name=str(price["model_name"]),
                    region=str(price.get("region") or "global"),
                    effective_from=cls._datetime(price["effective_from"]),
                    effective_until=(
                        cls._datetime(price["effective_until"])
                        if price.get("effective_until") is not None
                        else None
                    ),
                    input_per_1m_micro_usd=int(price["input_per_1m_micro_usd"]),
                    output_per_1m_micro_usd=int(price["output_per_1m_micro_usd"]),
                    cached_input_per_1m_micro_usd=(
                        int(price["cached_input_per_1m_micro_usd"])
                        if price.get("cached_input_per_1m_micro_usd") is not None
                        else None
                    ),
                    context_window_tokens=(
                        int(price["context_window_tokens"])
                        if price.get("context_window_tokens") is not None
                        else None
                    ),
                    pricing_source="yaml-seed",
                    pricing_version=pricing_version,
                )
            )
        return records

    @staticmethod
    def _datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
