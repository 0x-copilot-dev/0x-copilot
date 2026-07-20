"""models.dev-backed model metadata with cache and vendored-snapshot fallbacks.

:class:`ModelsDevCatalogSource` supplies the per-model metadata behind
:class:`agent_runtime.api.model_catalog.ModelCatalog` — display names,
context windows, token costs, capability flags — sourced from the public
https://models.dev registry instead of hardcoded lists.

Three data tiers, best available wins and **no tier ever raises**:

1. **live** — ``GET https://models.dev/api.json`` (10s timeout), refreshed
   in a background thread so catalog requests never block on the network.
2. **cache** — the last successful fetch, pruned and persisted under
   ``RUNTIME_MODEL_CATALOG_CACHE_DIR``. A stale cache still beats the
   snapshot; its age only decides whether a background refresh is due.
3. **snapshot** — the vendored ``config/models_dev_snapshot.json`` shipped
   with the service so a first boot with no network still produces a
   sensible catalog. Regenerate by fetching the API URL and pruning to
   :class:`ModelsDevPayloadParser.PROVIDER_SLUGS` and the fields modelled
   below (the cache writer produces exactly this shape).

The models.dev payload is untrusted input: every model row is validated
individually through lenient Pydantic boundary models, invalid rows are
skipped (one aggregate warning), and parse output is a strict
:class:`CatalogModelRecord` tuple with a deterministic order — provider
ascending, then release date descending, then model id ascending.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from agent_runtime.execution.contracts import RuntimeContract

_logger = logging.getLogger(__name__)


class _Messages:
    """Log copy for the catalog source (server logs only, never client-facing)."""

    REFRESH_FAILED = "models.dev refresh failed; keeping %s tier: %s"
    PAYLOAD_EMPTY = "models.dev payload contained no parseable models"
    PAYLOAD_NOT_OBJECT = "models.dev payload is not a JSON object"
    PAYLOAD_TOO_LARGE = "models.dev payload exceeded the size limit"
    SKIPPED_MODELS = "models.dev parse skipped %d invalid model rows"
    CACHE_READ_FAILED = "model catalog cache read failed at %s: %s"
    CACHE_WRITE_FAILED = "model catalog cache write failed at %s: %s"
    SNAPSHOT_UNAVAILABLE = "model catalog snapshot unavailable at %s: %s"
    NO_TIER = "model catalog has no data tier; serving an empty catalog"


class _LenientContract(BaseModel):
    """Boundary base for untrusted models.dev rows: ignore unknown fields."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class ModelsDevModalities(_LenientContract):
    """Input/output modality lists for one model."""

    input: tuple[str, ...] = ()
    output: tuple[str, ...] = ()


class ModelsDevLimit(_LenientContract):
    """Token limits for one model (``context`` window and max ``output``)."""

    context: int | None = None
    output: int | None = None


class ModelsDevCost(_LenientContract):
    """USD cost per 1M tokens for one model."""

    input: float | None = None
    output: float | None = None


class ModelsDevModel(_LenientContract):
    """One untrusted model row from the models.dev payload."""

    id: str | None = None
    name: str | None = None
    attachment: bool = False
    reasoning: bool = False
    tool_call: bool = False
    release_date: str | None = None
    modalities: ModelsDevModalities | None = None
    limit: ModelsDevLimit | None = None
    cost: ModelsDevCost | None = None


class CatalogModelRecord(RuntimeContract):
    """Normalized, trusted metadata for one catalog model (post-validation)."""

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
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    release_date: str | None = None


@dataclass(frozen=True)
class ParsedModelsDevCatalog:
    """Parse result: normalized records plus the pruned re-serializable payload."""

    records: tuple[CatalogModelRecord, ...]
    pruned_payload: dict[str, object]


class ModelsDevPayloadParser:
    """Validates a raw models.dev payload into :class:`CatalogModelRecord` rows."""

    # models.dev provider id -> our runtime provider slug. Anything not
    # listed here is skipped — the catalog only advertises providers the
    # product knows how to talk about.
    PROVIDER_SLUGS: Mapping[str, str] = {
        "openai": "openai",
        "anthropic": "anthropic",
        "google": "gemini",
        "openrouter": "openrouter",
        "groq": "groq",
        "xai": "xai",
    }

    class Keys:
        """Raw payload keys read outside the Pydantic boundary models."""

        MODELS = "models"
        ID = "id"
        NAME = "name"

    @classmethod
    def parse(cls, raw: object) -> ParsedModelsDevCatalog:
        """Parse an untrusted payload; raises ``ValueError`` only for a non-object root.

        Individual invalid model rows are skipped with one aggregate
        warning; providers outside :attr:`PROVIDER_SLUGS` are ignored.
        """

        if not isinstance(raw, Mapping):
            raise ValueError(_Messages.PAYLOAD_NOT_OBJECT)

        records: list[CatalogModelRecord] = []
        pruned_payload: dict[str, object] = {}
        skipped = 0
        for models_dev_id, provider_slug in cls.PROVIDER_SLUGS.items():
            provider_raw = raw.get(models_dev_id)
            if not isinstance(provider_raw, Mapping):
                continue
            models_raw = provider_raw.get(cls.Keys.MODELS)
            if not isinstance(models_raw, Mapping):
                continue
            pruned_models: dict[str, object] = {}
            for model_key, model_raw in models_raw.items():
                try:
                    model = ModelsDevModel.model_validate(model_raw)
                except ValidationError:
                    skipped += 1
                    continue
                record = cls._record_from_model(
                    provider_slug=provider_slug,
                    model_key=str(model_key),
                    model=model,
                )
                if record is None:
                    skipped += 1
                    continue
                records.append(record)
                pruned_models[str(model_key)] = model.model_dump(
                    mode="json", exclude_none=True
                )
            if pruned_models:
                pruned_payload[models_dev_id] = {
                    cls.Keys.ID: models_dev_id,
                    cls.Keys.MODELS: pruned_models,
                }
        if skipped:
            _logger.warning(_Messages.SKIPPED_MODELS, skipped)
        return ParsedModelsDevCatalog(
            records=cls._sorted(records),
            pruned_payload=pruned_payload,
        )

    @classmethod
    def _record_from_model(
        cls,
        *,
        provider_slug: str,
        model_key: str,
        model: ModelsDevModel,
    ) -> CatalogModelRecord | None:
        """Normalize one validated row; ``None`` when no usable model id exists."""

        model_id = (model.id or model_key).strip()
        if not model_id:
            return None
        display_name = (model.name or "").strip() or model_id
        limit = model.limit or ModelsDevLimit()
        cost = model.cost or ModelsDevCost()
        modalities = model.modalities or ModelsDevModalities()
        return CatalogModelRecord(
            provider=provider_slug,
            model_id=model_id,
            display_name=display_name,
            context_window=cls._positive_or_none(limit.context),
            max_output_tokens=cls._positive_or_none(limit.output),
            input_cost_per_mtok=cls._non_negative_or_none(cost.input),
            output_cost_per_mtok=cls._non_negative_or_none(cost.output),
            supports_reasoning=model.reasoning,
            supports_tools=model.tool_call,
            supports_attachments=model.attachment,
            input_modalities=cls._clean_modalities(modalities.input),
            output_modalities=cls._clean_modalities(modalities.output),
            release_date=(model.release_date or "").strip() or None,
        )

    @staticmethod
    def _positive_or_none(value: int | None) -> int | None:
        return value if value is not None and value > 0 else None

    @staticmethod
    def _non_negative_or_none(value: float | None) -> float | None:
        return value if value is not None and value >= 0 else None

    @staticmethod
    def _clean_modalities(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(v.strip().lower() for v in values if v.strip())

    @staticmethod
    def _sorted(
        records: list[CatalogModelRecord],
    ) -> tuple[CatalogModelRecord, ...]:
        """Deterministic order: provider asc, release date desc, model id asc."""

        ordered = sorted(records, key=lambda r: r.model_id)
        ordered.sort(key=lambda r: r.release_date or "", reverse=True)
        ordered.sort(key=lambda r: r.provider)
        return tuple(ordered)


class CatalogTier(StrEnum):
    """Which data tier the source is currently serving."""

    LIVE = "live"
    CACHE = "cache"
    SNAPSHOT = "snapshot"
    EMPTY = "empty"


class ModelsDevCatalogSource:
    """Serves catalog records from live -> cache -> snapshot tiers, never blocking.

    ``records()`` is safe on any request path: it only ever performs local
    disk reads inline; the network fetch happens lazily in a daemon thread
    (kicked on first request and again once the TTL lapses), and requests
    made while a fetch is in flight keep serving the previous tier.
    """

    API_URL = "https://models.dev/api.json"
    CACHE_FILENAME = "models_dev.json"
    CACHE_TTL_SECONDS = 24 * 60 * 60
    FETCH_TIMEOUT_SECONDS = 10.0
    # After a failed refresh, wait this long before another attempt so a
    # dead network does not spawn a thread per catalog request.
    RETRY_INTERVAL_SECONDS = 5 * 60
    MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
    REFRESH_THREAD_NAME = "models-dev-catalog-refresh"
    DEFAULT_SNAPSHOT_PATH = (
        Path(__file__).resolve().parents[3] / "config" / "models_dev_snapshot.json"
    )

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        snapshot_path: str | Path | None = None,
        http_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        auto_refresh: bool = True,
    ) -> None:
        # ``http_client`` is injectable for tests (httpx.MockTransport) and is
        # never closed by this class. ``auto_refresh=False`` disables the
        # background thread entirely — tests drive fetches via ``refresh_now``.
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._snapshot_path = (
            Path(snapshot_path) if snapshot_path else self.DEFAULT_SNAPSHOT_PATH
        )
        self._http_client = http_client
        self._clock = clock
        self._auto_refresh = auto_refresh
        self._lock = threading.Lock()
        self._records: tuple[CatalogModelRecord, ...] | None = None
        self._tier = CatalogTier.EMPTY
        self._fetched_at: float | None = None
        self._last_attempt_at: float | None = None
        self._refresh_in_flight = False

    def records(self) -> tuple[CatalogModelRecord, ...]:
        """Return the best-available records; never raises, never blocks on network."""

        spawn = False
        with self._lock:
            if self._records is None:
                self._load_offline_tiers_locked()
            if (
                self._auto_refresh
                and not self._refresh_in_flight
                and self._needs_refresh_locked()
            ):
                self._refresh_in_flight = True
                spawn = True
            records = self._records or ()
        if spawn:
            threading.Thread(
                target=self._background_refresh,
                name=self.REFRESH_THREAD_NAME,
                daemon=True,
            ).start()
        return records

    def current_tier(self) -> CatalogTier:
        """Return which tier the source is serving (observability and tests)."""

        with self._lock:
            return self._tier

    def refresh_now(self) -> bool:
        """Fetch and swap in live data synchronously; ``False`` (never raise) on failure."""

        with self._lock:
            self._last_attempt_at = self._clock()
            previous_tier = self._tier
        try:
            payload = self._fetch_payload()
            parsed = ModelsDevPayloadParser.parse(payload)
            if not parsed.records:
                raise ValueError(_Messages.PAYLOAD_EMPTY)
        except Exception as exc:  # noqa: BLE001 — every failure falls to the next tier.
            _logger.warning(_Messages.REFRESH_FAILED, previous_tier.value, exc)
            return False
        self._write_cache(parsed.pruned_payload)
        with self._lock:
            self._records = parsed.records
            self._tier = CatalogTier.LIVE
            self._fetched_at = self._clock()
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _background_refresh(self) -> None:
        try:
            self.refresh_now()
        finally:
            with self._lock:
                self._refresh_in_flight = False

    def _needs_refresh_locked(self) -> bool:
        now = self._clock()
        if (
            self._last_attempt_at is not None
            and now - self._last_attempt_at < self.RETRY_INTERVAL_SECONDS
        ):
            return False
        if self._fetched_at is None:
            # Snapshot or empty tier — always worth trying to go live.
            return True
        return now - self._fetched_at >= self.CACHE_TTL_SECONDS

    def _fetch_payload(self) -> object:
        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.Client(timeout=self.FETCH_TIMEOUT_SECONDS)
        try:
            response = client.get(self.API_URL)
            response.raise_for_status()
            if len(response.content) > self.MAX_PAYLOAD_BYTES:
                raise ValueError(_Messages.PAYLOAD_TOO_LARGE)
            return response.json()
        finally:
            if owns_client:
                client.close()

    def _load_offline_tiers_locked(self) -> None:
        """Populate records from cache, else snapshot, else empty. Lock held."""

        cached = self._read_cache()
        if cached is not None:
            records, cached_at = cached
            self._records = records
            self._tier = CatalogTier.CACHE
            # A stale cache still serves; its age just makes a refresh due.
            self._fetched_at = cached_at
            return
        snapshot = self._read_snapshot()
        if snapshot is not None:
            self._records = snapshot
            self._tier = CatalogTier.SNAPSHOT
            self._fetched_at = None
            return
        _logger.warning(_Messages.NO_TIER)
        self._records = ()
        self._tier = CatalogTier.EMPTY
        self._fetched_at = None

    def _cache_path(self) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / self.CACHE_FILENAME

    def _read_cache(self) -> tuple[tuple[CatalogModelRecord, ...], float] | None:
        path = self._cache_path()
        if path is None:
            return None
        try:
            if not path.is_file():
                return None
            modified_at = path.stat().st_mtime
            parsed = ModelsDevPayloadParser.parse(json.loads(path.read_text()))
        except Exception as exc:  # noqa: BLE001 — a bad cache falls back to snapshot.
            _logger.warning(_Messages.CACHE_READ_FAILED, path, exc)
            return None
        if not parsed.records:
            return None
        return parsed.records, modified_at

    def _read_snapshot(self) -> tuple[CatalogModelRecord, ...] | None:
        path = self._snapshot_path
        try:
            parsed = ModelsDevPayloadParser.parse(json.loads(path.read_text()))
        except Exception as exc:  # noqa: BLE001 — a bad snapshot yields an empty tier.
            _logger.warning(_Messages.SNAPSHOT_UNAVAILABLE, path, exc)
            return None
        return parsed.records or None

    def _write_cache(self, pruned_payload: dict[str, object]) -> None:
        path = self._cache_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(pruned_payload))
            tmp_path.replace(path)
        except Exception as exc:  # noqa: BLE001 — cache persistence is best-effort.
            _logger.warning(_Messages.CACHE_WRITE_FAILED, path, exc)


__all__ = [
    "CatalogModelRecord",
    "CatalogTier",
    "ModelsDevCatalogSource",
    "ModelsDevPayloadParser",
    "ParsedModelsDevCatalog",
]
