"""Time-keyed pricing lookup with a small in-process LRU cache.

Worker cost-computation hooks fire on every RUN_COMPLETED, so a naive DB hit per
row inflates p99 lookup latency. The cache key is ``(provider, model_name, region,
at_floor_to_minute)`` — minute granularity is sufficient because pricing changes at
most quarterly. Cache size is bounded to prevent unbounded growth in long-running workers.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from agent_runtime.persistence.records import ModelPricingRecord


class _PricingPort(Protocol):
    async def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None: ...


class ModelPricingCatalog:
    """In-process cache for ``lookup_pricing`` calls."""

    _CACHE_MAXSIZE = 256

    def __init__(self, port: _PricingPort) -> None:
        self._port = port
        self._cache: OrderedDict[
            tuple[str, str, str, datetime], ModelPricingRecord | None
        ] = OrderedDict()

    @classmethod
    def from_litellm(
        cls, *, overrides_path: Path | None = None
    ) -> "ModelPricingCatalog":
        """Build a catalog backed by the LiteLLM library rate source.

        The single construction point for the production pricing catalog:
        rates come from ``litellm.model_cost`` with the reviewed override
        backstop, wrapped in this in-process cache.
        """

        from agent_runtime.pricing.litellm_source import (  # noqa: PLC0415 — break import cycle
            LitellmRateSource,
        )

        return cls(LitellmRateSource(overrides_path=overrides_path))

    async def lookup(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        key = (provider, model_name, region, self._floor_to_minute(at))
        if key in self._cache:
            value = self._cache.pop(key)
            self._cache[key] = value  # LRU bump
            return value
        value = await self._port.lookup_pricing(
            provider=provider,
            model_name=model_name,
            region=region,
            at=at,
        )
        self._cache[key] = value
        if len(self._cache) > self._CACHE_MAXSIZE:
            self._cache.popitem(last=False)
        return value

    def invalidate(self) -> None:
        """Clear the cache after a seed/rotation event."""

        self._cache.clear()

    @staticmethod
    def _floor_to_minute(value: datetime) -> datetime:
        # Truncate sub-minute precision so cache hits cluster.
        return value - timedelta(seconds=value.second, microseconds=value.microsecond)
