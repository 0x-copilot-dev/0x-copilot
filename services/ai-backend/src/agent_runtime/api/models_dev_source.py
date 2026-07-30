"""Catalog discovery backed by models.dev, falling back to LiteLLM.

Why a second source at all: LiteLLM's bundled ``model_cost`` table carries no
release date (only a sparse ``deprecation_date``), no product-line grouping, and
no display names — so a picker built on it cannot order by recency, cannot
derive a size ladder, and must synthesise labels from the slug (``claude-haiku-
4-5-20251001`` -> "Claude Haiku 4.5.20251001"). models.dev carries all three
(``release_date``, ``family``, ``name``) plus explicit ``modalities``, which is
the only reliable way to drop audio models: LiteLLM labels ``gpt-audio`` as
``mode: "chat"`` and — in the *bundled offline* table this service pins — even
labels ``gpt-realtime`` that way.

Posture: models.dev is primary, LiteLLM is the fallback, and the network is
never on the request path.

* ``records()`` is synchronous (the :class:`CatalogModelSource` contract) and
  only ever reads a local cache file. It never performs I/O against the network.
* A stale or missing cache schedules a **background daemon-thread refresh**,
  rate-limited by :attr:`ModelsDevCatalogCache.RETRY_INTERVAL_SECONDS`, and the
  call returns immediately from whatever is already available.
* With no usable cache — first boot, or permanently offline — every call falls
  through to :class:`LitellmModelSource`, so the picker degrades to today's
  behaviour instead of emptying out.

That ordering means a fresh install shows the LiteLLM catalog for the first few
seconds and the models.dev catalog from the next fetch onward, and an offline
desktop keeps working forever on the last good snapshot.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from agent_runtime.api.litellm_model_source import (
    CatalogModelRecord,
    CatalogModelSource,
    LitellmModelSource,
)

_LOGGER = logging.getLogger(__name__)


class ModelsDevCatalogCache:
    """On-disk snapshot of ``models.dev/api.json`` with a background refresh.

    The cache file is the only thing :meth:`ModelsDevModelSource.records` reads,
    so a slow or dead network can never delay a catalog request. Refreshes run on
    a daemon thread; failures are logged once at debug level and retried no more
    often than :attr:`RETRY_INTERVAL_SECONDS`.
    """

    URL: Final[str] = "https://models.dev/api.json"
    #: Serve a snapshot older than this only while a refresh is in flight.
    TTL_SECONDS: Final[float] = 24 * 60 * 60
    #: Floor between refresh attempts, so a hard-offline host retries calmly.
    RETRY_INTERVAL_SECONDS: Final[float] = 10 * 60
    TIMEOUT_SECONDS: Final[float] = 10.0
    #: Env override for the snapshot location; otherwise derived (see _default_path).
    PATH_ENV: Final[str] = "RUNTIME_MODEL_CATALOG_CACHE"
    FILE_STORE_ROOT_ENV: Final[str] = "RUNTIME_FILE_STORE_ROOT"
    FILENAME: Final[str] = "models-dev-catalog.json"

    def __init__(
        self,
        *,
        path: Path | None = None,
        ttl_seconds: float | None = None,
        fetcher: "ModelsDevFetcher | None" = None,
    ) -> None:
        self._path = path if path is not None else self._default_path()
        self._ttl = self.TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self._fetcher = fetcher if fetcher is not None else ModelsDevFetcher()
        self._lock = threading.Lock()
        self._refreshing = False
        self._last_attempt: float | None = None

    @classmethod
    def _default_path(cls) -> Path:
        """Snapshot location: explicit env > the run store root > the temp dir."""

        override = (os.environ.get(cls.PATH_ENV) or "").strip()
        if override:
            return Path(override)
        store_root = (os.environ.get(cls.FILE_STORE_ROOT_ENV) or "").strip()
        base = Path(store_root) if store_root else Path(tempfile.gettempdir())
        return base / cls.FILENAME

    def payload(self) -> Mapping[str, Any] | None:
        """Return the cached snapshot, scheduling a refresh when stale/absent.

        Deliberately returns a *stale* snapshot rather than nothing: an outdated
        model list beats collapsing to the LiteLLM fallback mid-session.
        """

        snapshot, age = self._read()
        if snapshot is None or age is None or age > self._ttl:
            self._schedule_refresh()
        return snapshot

    def _read(self) -> tuple[Mapping[str, Any] | None, float | None]:
        try:
            raw = self._path.read_text(encoding="utf-8")
            age = time.time() - self._path.stat().st_mtime
        except OSError:
            return None, None
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None, None
        return (parsed, age) if isinstance(parsed, Mapping) else (None, None)

    def _schedule_refresh(self) -> None:
        now = time.time()
        with self._lock:
            if self._refreshing:
                return
            if (
                self._last_attempt is not None
                and now - self._last_attempt < self.RETRY_INTERVAL_SECONDS
            ):
                return
            self._refreshing = True
            self._last_attempt = now
        threading.Thread(
            target=self._refresh, name="models-dev-catalog-refresh", daemon=True
        ).start()

    def _refresh(self) -> None:
        try:
            payload = self._fetcher.fetch(self.URL, timeout=self.TIMEOUT_SECONDS)
            if payload is not None:
                self._write(payload)
        finally:
            with self._lock:
                self._refreshing = False

    def _write(self, payload: Mapping[str, Any]) -> None:
        """Atomically replace the snapshot so a torn write can't poison reads."""

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:  # pragma: no cover - disk-shape dependent
            _LOGGER.debug("models.dev cache write failed: %s", exc)


class ModelsDevFetcher:
    """The single network hop. Isolated so tests can substitute it wholesale."""

    def fetch(self, url: str, *, timeout: float) -> Mapping[str, Any] | None:
        """GET the catalog. Returns ``None`` on any failure — never raises."""

        try:
            import httpx  # noqa: PLC0415 — lazy: keeps httpx off the import graph

            response = httpx.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - fail-soft by contract
            _LOGGER.debug("models.dev fetch failed: %s", exc)
            return None
        return payload if isinstance(payload, Mapping) else None


class ModelsDevCatalogPolicy:
    """What counts as a selectable chat model, and how ids are normalized."""

    #: models.dev provider key -> this product's provider slug. Mirrors
    #: ``ModelConfigResolver``'s allowlist (``google`` normalizes to ``gemini``).
    PROVIDERS: Final[Mapping[str, str]] = {
        "anthropic": "anthropic",
        "openai": "openai",
        "google": "gemini",
        "openrouter": "openrouter",
    }
    DEAD_STATUSES: Final[frozenset[str]] = frozenset({"deprecated", "retired"})
    #: Trailing dated-snapshot suffix: ``-20251001`` or ``-2025-10-01``.
    DATED_SUFFIX: Final[re.Pattern[str]] = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")
    #: Purpose-specific lines that are not general chat models. Matched against
    #: the id AND the family so a renamed variant can't slip through one of them.
    #: These are *product lines*, not versions, so the list stays stable as new
    #: releases ship — a new ``gpt-5.7-codex`` is excluded without a code change.
    NICHE: Final[re.Pattern[str]] = re.compile(
        r"(codex|realtime|live|image|video|tts|audio|speech|search|embed|rerank"
        r"|guard|moderat|robotics|computer-use|translate|deep-research"
        r"|:free|:exacto)",
        re.IGNORECASE,
    )

    @classmethod
    def eligible(cls, model: Mapping[str, Any]) -> bool:
        """Whether a models.dev row is a general chat model the run path can use."""

        modalities = model.get("modalities")
        modalities = modalities if isinstance(modalities, Mapping) else {}
        outputs = cls._string_list(modalities.get("output"))
        inputs = cls._string_list(modalities.get("input"))
        # Text-ONLY output is the decisive audio/image filter. `mode`-style flags
        # cannot do this job: LiteLLM calls `gpt-audio` a chat model, and it is —
        # it just also speaks, which the composer has no way to render.
        if outputs != ("text",) or "text" not in inputs:
            return False
        if not model.get("tool_call"):
            return False
        # NB: `experimental` is an OPTIONS OBJECT on some rows (Claude Opus 5
        # carries its "fast" mode there), so only an explicit True means the
        # model itself is experimental. Truthiness would drop the flagship.
        if model.get("experimental") is True:
            return False
        status = model.get("status")
        if isinstance(status, str) and status.strip().lower() in cls.DEAD_STATUSES:
            return False
        if not cls.release_date(model):
            return False
        identifier = model.get("id")
        family = model.get("family")
        return not (
            (isinstance(identifier, str) and cls.NICHE.search(identifier))
            or (isinstance(family, str) and cls.NICHE.search(family))
        )

    @classmethod
    def release_date(cls, model: Mapping[str, Any]) -> str | None:
        value = model.get("release_date")
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @classmethod
    def prefer_stable_over_snapshot(
        cls, models: Mapping[str, Mapping[str, Any]]
    ) -> tuple[Mapping[str, Any], ...]:
        """Drop ``claude-haiku-4-5-20251001`` when ``claude-haiku-4-5`` exists.

        Both describe one product; the stable alias is what a user should pin.
        A dated id with no stable sibling is kept — some models ship dated-only.
        """

        kept: list[Mapping[str, Any]] = []
        for identifier, model in models.items():
            base = cls.DATED_SUFFIX.sub("", identifier)
            if base != identifier and base in models:
                continue
            kept.append(model)
        return tuple(kept)

    @staticmethod
    def _string_list(value: object) -> tuple[str, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()
        return tuple(item for item in value if isinstance(item, str))


class ModelsDevCatalogParser:
    """Map the models.dev payload onto :class:`CatalogModelRecord` values."""

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> tuple[CatalogModelRecord, ...]:
        """Return every eligible record across the providers we can execute."""

        records: list[CatalogModelRecord] = []
        for source_key, provider in ModelsDevCatalogPolicy.PROVIDERS.items():
            entry = payload.get(source_key)
            if not isinstance(entry, Mapping):
                continue
            models = entry.get("models")
            if not isinstance(models, Mapping):
                continue
            eligible = {
                identifier: model
                for identifier, model in models.items()
                if isinstance(identifier, str)
                and isinstance(model, Mapping)
                and ModelsDevCatalogPolicy.eligible(model)
            }
            for model in ModelsDevCatalogPolicy.prefer_stable_over_snapshot(eligible):
                record = cls._record(provider=provider, model=model)
                if record is not None:
                    records.append(record)
        # Deterministic order (provider asc, id asc), matching LitellmModelSource
        # so a source swap never reshuffles the picker on its own.
        return tuple(sorted(records, key=lambda r: (r.provider, r.model_id)))

    @classmethod
    def _record(
        cls, *, provider: str, model: Mapping[str, Any]
    ) -> CatalogModelRecord | None:
        identifier = model.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            return None
        name = model.get("name")
        limit = model.get("limit")
        limit = limit if isinstance(limit, Mapping) else {}
        cost = model.get("cost")
        cost = cost if isinstance(cost, Mapping) else {}
        family = model.get("family")
        return CatalogModelRecord(
            provider=provider,
            model_id=identifier.strip(),
            # models.dev ships curated labels ("Claude Opus 5", "GPT-5.4 nano"),
            # which is the whole reason the picker stops showing slug-derived
            # strings like "Claude Haiku 4.5.20251001".
            display_name=name.strip()
            if isinstance(name, str) and name.strip()
            else identifier.strip(),
            context_window=cls._int(limit.get("context")),
            max_output_tokens=cls._int(limit.get("output")),
            input_cost_per_mtok=cls._float(cost.get("input")),
            output_cost_per_mtok=cls._float(cost.get("output")),
            supports_reasoning=bool(model.get("reasoning")),
            supports_tools=bool(model.get("tool_call")),
            supports_attachments=cls._has_attachment_input(model),
            release_date=ModelsDevCatalogPolicy.release_date(model),
            family=family.strip()
            if isinstance(family, str) and family.strip()
            else None,
        )

    @staticmethod
    def _has_attachment_input(model: Mapping[str, Any]) -> bool:
        if model.get("attachment"):
            return True
        modalities = model.get("modalities")
        modalities = modalities if isinstance(modalities, Mapping) else {}
        inputs = ModelsDevCatalogPolicy._string_list(modalities.get("input"))
        return any(kind in inputs for kind in ("image", "pdf"))

    @staticmethod
    def _int(value: object) -> int | None:
        return int(value) if isinstance(value, (int, float)) and value > 0 else None

    @staticmethod
    def _float(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)


class ModelsDevModelSource:
    """models.dev-backed :class:`CatalogModelSource` with a LiteLLM fallback."""

    def __init__(
        self,
        *,
        cache: ModelsDevCatalogCache | None = None,
        fallback: CatalogModelSource | None = None,
    ) -> None:
        self._cache = cache if cache is not None else ModelsDevCatalogCache()
        self._fallback = fallback if fallback is not None else LitellmModelSource()

    def records(self) -> tuple[CatalogModelRecord, ...]:
        """Eligible records from the cached snapshot, else the LiteLLM fallback.

        An empty parse counts as a failure: a payload whose shape we no longer
        understand must degrade to LiteLLM, not to an empty picker.
        """

        payload = self._cache.payload()
        if payload is not None:
            records = ModelsDevCatalogParser.parse(payload)
            if records:
                return records
        return self._fallback.records()


__all__ = [
    "ModelsDevCatalogCache",
    "ModelsDevCatalogParser",
    "ModelsDevCatalogPolicy",
    "ModelsDevFetcher",
    "ModelsDevModelSource",
]
