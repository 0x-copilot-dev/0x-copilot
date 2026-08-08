"""On-disk JSON snapshot with a background refresh, shared by catalog sources.

Both upstream model catalogs this service reads — ``models.dev`` and the
Virtuals compute gateway — need the identical posture, and it is the posture
that matters rather than the payload:

* ``records()`` on a :class:`~agent_runtime.api.litellm_model_source.CatalogModelSource`
  is SYNCHRONOUS and sits on the ``/v1/agent/models`` request path, so it must
  never perform network I/O. It reads a local file, nothing else.
* A stale or missing snapshot schedules a **background daemon-thread refresh**,
  rate-limited by :attr:`JsonSnapshotCache.RETRY_INTERVAL_SECONDS`, and the call
  returns immediately from whatever is already on disk.
* A *stale* snapshot is served in preference to nothing: an outdated model list
  beats a picker that empties out mid-session.

Subclasses supply :attr:`URL`, :attr:`FILENAME`, :attr:`PATH_ENV` and
:attr:`LABEL`. ``LABEL`` is not cosmetic — it names the upstream in every log
line, so a failing Virtuals fetch does not report itself as a models.dev
failure.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)


class JsonSnapshotFetcher:
    """The single network hop. Isolated so tests can substitute it wholesale."""

    def fetch(
        self, url: str, *, timeout: float, label: str
    ) -> Mapping[str, Any] | None:
        """GET ``url``. Returns ``None`` on any failure — never raises.

        Fail-soft by contract: the caller's whole design is "serve what is on
        disk", so a transport error, a non-2xx, or a non-JSON body are all the
        same outcome — no new snapshot this round.
        """

        try:
            import httpx  # noqa: PLC0415 — lazy: keeps httpx off the import graph

            response = httpx.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - fail-soft by contract
            _LOGGER.debug("%s fetch failed: %s", label, exc)
            return None
        return payload if isinstance(payload, Mapping) else None


class JsonSnapshotCache:
    """A cached upstream JSON document, refreshed off the request path."""

    #: Upstream document. Subclasses MUST override.
    URL: Final[str] = ""
    #: Human name for this upstream, used in log lines.
    LABEL: Final[str] = "catalog"
    #: Serve a snapshot older than this only while a refresh is in flight.
    TTL_SECONDS: Final[float] = 24 * 60 * 60
    #: Floor between refresh attempts, so a hard-offline host retries calmly.
    RETRY_INTERVAL_SECONDS: Final[float] = 10 * 60
    TIMEOUT_SECONDS: Final[float] = 10.0
    #: Env override for the snapshot location; otherwise derived (see _default_path).
    PATH_ENV: Final[str] = ""
    FILE_STORE_ROOT_ENV: Final[str] = "RUNTIME_FILE_STORE_ROOT"
    FILENAME: Final[str] = "catalog.json"

    def __init__(
        self,
        *,
        path: Path | None = None,
        ttl_seconds: float | None = None,
        fetcher: JsonSnapshotFetcher | None = None,
    ) -> None:
        self._path = path if path is not None else self._default_path()
        self._ttl = self.TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self._fetcher = fetcher if fetcher is not None else JsonSnapshotFetcher()
        self._lock = threading.Lock()
        self._refreshing = False
        self._last_attempt: float | None = None

    @classmethod
    def _default_path(cls) -> Path:
        """Snapshot location: explicit env > the run store root > the temp dir."""

        override = (os.environ.get(cls.PATH_ENV) or "").strip() if cls.PATH_ENV else ""
        if override:
            return Path(override)
        store_root = (os.environ.get(cls.FILE_STORE_ROOT_ENV) or "").strip()
        base = Path(store_root) if store_root else Path(tempfile.gettempdir())
        return base / cls.FILENAME

    def payload(self) -> Mapping[str, Any] | None:
        """Return the cached snapshot, scheduling a refresh when stale/absent.

        Deliberately returns a *stale* snapshot rather than nothing — see the
        module docstring.
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
            target=self._refresh,
            name=f"{self.LABEL}-catalog-refresh",
            daemon=True,
        ).start()

    def _refresh(self) -> None:
        try:
            payload = self._fetcher.fetch(
                self.URL, timeout=self.TIMEOUT_SECONDS, label=self.LABEL
            )
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
            _LOGGER.debug("%s cache write failed: %s", self.LABEL, exc)


__all__ = ["JsonSnapshotCache", "JsonSnapshotFetcher"]
