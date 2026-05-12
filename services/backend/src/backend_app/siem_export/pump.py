"""C9 SIEM export pump — single async loop driving multiple exporters.

Per source per exporter:

  1. Read the cursor from ``siem_export_cursors``.
  2. Fetch the next batch (after_id, ordered by ``created_at, id``).
  3. Normalize → ``NormalizedEvent``.
  4. Hand to the exporter; classify the response.
  5. On 2xx: advance the cursor. On 4xx: dead-letter + advance. On 5xx /
     transport: leave the cursor; back off exponentially.

The runtime audit source rides an internal HTTP cursor on ai-backend
(``GET /internal/v1/audit/cursor``) so the pump never reads ai-backend's
DB directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend_app.siem_export.interface import (
    NormalizedEvent,
    SendOutcome,
    SiemExporter,
    SiemExportSource,
)
from backend_app.siem_export.normalizer import EventNormalizer


_LOGGER = logging.getLogger("backend.siem_export.pump")


class SiemExportPumpEnv:
    INTERVAL_SECONDS = "SIEM_PUMP_INTERVAL_SECONDS"
    BATCH_SIZE = "SIEM_PUMP_BATCH_SIZE"
    AI_BACKEND_BASE_URL = "AI_BACKEND_INTERNAL_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"

    DEFAULT_INTERVAL_SECONDS = 5.0
    DEFAULT_BATCH_SIZE = 100
    BACKOFF_BASE_SECONDS = 1.0
    BACKOFF_MAX_SECONDS = 60.0

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default


@dataclass(frozen=True)
class _Cursor:
    last_event_id: str | None
    last_processed_at: datetime


class SiemExportPump:
    """One async loop driving every configured exporter × source."""

    def __init__(
        self,
        *,
        database_url: str,
        exporters: tuple[SiemExporter, ...],
        ai_backend_base_url: str | None = None,
        service_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        interval_seconds: float | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._database_url = database_url
        self._exporters = exporters
        self._ai_base = (
            ai_backend_base_url
            if ai_backend_base_url is not None
            else os.environ.get(SiemExportPumpEnv.AI_BACKEND_BASE_URL)
        )
        self._service_token = (
            service_token
            if service_token is not None
            else os.environ.get(SiemExportPumpEnv.SERVICE_TOKEN)
        )
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else SiemExportPumpEnv.env_float(
                SiemExportPumpEnv.INTERVAL_SECONDS,
                SiemExportPumpEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._batch_size = (
            batch_size
            if batch_size is not None
            else SiemExportPumpEnv.env_int(
                SiemExportPumpEnv.BATCH_SIZE,
                SiemExportPumpEnv.DEFAULT_BATCH_SIZE,
            )
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Per-(exporter, source) backoff multiplier; resets on success.
        self._backoff_multiplier: dict[tuple[str, SiemExportSource], float] = {}

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="siem-export-pump")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            try:
                await self.tick()
            except Exception:
                _LOGGER.warning("siem_pump_tick_failed", exc_info=True)

    async def tick(self) -> None:
        """One iteration: ship a batch per (exporter, source).

        Public so tests can drive the pump synchronously.
        """

        for exporter in self._exporters:
            for source in (
                SiemExportSource.MCP_AUDIT,
                SiemExportSource.IDENTITY_AUDIT,
                SiemExportSource.RUNTIME_AUDIT_REMOTE,
            ):
                key = (exporter.name, source)
                multiplier = self._backoff_multiplier.get(key, 0.0)
                if multiplier > 0:
                    # Skip this source for one tick after a 5xx so we don't
                    # hammer; the multiplier shrinks as ticks elapse.
                    self._backoff_multiplier[key] = max(0.0, multiplier - 1.0)
                    continue
                try:
                    await self._process_one(exporter, source)
                except Exception:
                    _LOGGER.warning(
                        "siem_pump_source_failed",
                        extra={
                            "metadata": {
                                "exporter": exporter.name,
                                "source": source.value,
                            }
                        },
                        exc_info=True,
                    )

    async def _process_one(
        self, exporter: SiemExporter, source: SiemExportSource
    ) -> None:
        cursor = await asyncio.to_thread(self._read_cursor, exporter.name, source)
        rows = await self._fetch_batch(source, cursor)
        if not rows:
            return
        events = tuple(self._normalize_row(source, row) for row in rows)
        result = await exporter.send(events)
        if result.outcome is SendOutcome.OK:
            self._backoff_multiplier.pop((exporter.name, source), None)
            new_cursor = _Cursor(
                last_event_id=str(rows[-1]["id"]),
                last_processed_at=datetime.now(timezone.utc),
            )
            await asyncio.to_thread(
                self._write_cursor, exporter.name, source, new_cursor
            )
            return
        if result.outcome is SendOutcome.DEAD_LETTER:
            # Park rejected events; advance cursor past them.
            await asyncio.to_thread(
                self._write_dead_letters,
                exporter.name,
                source,
                events,
                result.last_error or "rejected",
            )
            self._backoff_multiplier.pop((exporter.name, source), None)
            new_cursor = _Cursor(
                last_event_id=str(rows[-1]["id"]),
                last_processed_at=datetime.now(timezone.utc),
            )
            await asyncio.to_thread(
                self._write_cursor, exporter.name, source, new_cursor
            )
            return
        # RETRY: leave cursor; raise multiplier (capped). Backoff is the
        # number of ticks we skip before retrying.
        prior = self._backoff_multiplier.get((exporter.name, source), 0.0)
        next_mult = min(
            prior * 2 + 1,
            SiemExportPumpEnv.BACKOFF_MAX_SECONDS / max(self._interval, 0.001),
        )
        self._backoff_multiplier[(exporter.name, source)] = next_mult

    async def _fetch_batch(
        self, source: SiemExportSource, cursor: _Cursor
    ) -> list[dict[str, Any]]:
        if source is SiemExportSource.MCP_AUDIT:
            return await asyncio.to_thread(
                self._fetch_local, "mcp_audit_events", cursor
            )
        if source is SiemExportSource.IDENTITY_AUDIT:
            return await asyncio.to_thread(
                self._fetch_local, "identity_audit_events", cursor
            )
        if source is SiemExportSource.RUNTIME_AUDIT_REMOTE:
            return await self._fetch_runtime_remote(cursor)
        raise ValueError(f"unknown source: {source!r}")

    def _normalize_row(
        self, source: SiemExportSource, row: dict[str, Any]
    ) -> NormalizedEvent:
        if source is SiemExportSource.MCP_AUDIT:
            return EventNormalizer.from_mcp_audit(row)
        if source is SiemExportSource.IDENTITY_AUDIT:
            return EventNormalizer.from_identity_audit(row)
        return EventNormalizer.from_runtime_audit(row)

    # ------------------------------------------------------------------
    # DB helpers (sync; called via ``asyncio.to_thread``).
    # ------------------------------------------------------------------

    def _read_cursor(self, exporter_name: str, source: SiemExportSource) -> _Cursor:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_event_id, last_processed_at FROM siem_export_cursors "
                    "WHERE exporter_name = %s AND source = %s",
                    (exporter_name, source.value),
                )
                row = cur.fetchone()
        if row is None:
            return _Cursor(
                last_event_id=None,
                last_processed_at=datetime.fromtimestamp(0, tz=timezone.utc),
            )
        return _Cursor(
            last_event_id=row["last_event_id"],
            last_processed_at=row["last_processed_at"],
        )

    def _fetch_local(self, table: str, cursor: _Cursor) -> list[dict[str, Any]]:
        sql = f"""
            SELECT * FROM {table}
             WHERE (%(after_id)s IS NULL OR id > %(after_id)s)
             ORDER BY created_at ASC, id ASC
             LIMIT %(limit)s
        """
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {
                        "after_id": cursor.last_event_id,
                        "limit": self._batch_size,
                    },
                )
                return list(cur.fetchall())

    async def _fetch_runtime_remote(self, cursor: _Cursor) -> list[dict[str, Any]]:
        if not self._ai_base or not self._service_token:
            return []
        params: dict[str, Any] = {"limit": str(self._batch_size)}
        if cursor.last_event_id:
            params["after_id"] = cursor.last_event_id
        try:
            response = await self._http.get(
                f"{self._ai_base.rstrip('/')}/internal/v1/audit/cursor",
                params=params,
                headers={"X-Enterprise-Service-Token": self._service_token},
            )
        except httpx.HTTPError:
            return []
        if response.status_code != 200:
            return []
        body = response.json()
        events = body.get("events", [])
        return [event for event in events if isinstance(event, dict)]

    def _write_cursor(
        self,
        exporter_name: str,
        source: SiemExportSource,
        cursor: _Cursor,
    ) -> None:
        with psycopg.connect(self._database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO siem_export_cursors (
                        exporter_name, source, last_event_id, last_processed_at
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (exporter_name, source) DO UPDATE
                       SET last_event_id = EXCLUDED.last_event_id,
                           last_processed_at = EXCLUDED.last_processed_at
                    """,
                    (
                        exporter_name,
                        source.value,
                        cursor.last_event_id,
                        cursor.last_processed_at,
                    ),
                )
            conn.commit()

    def _write_dead_letters(
        self,
        exporter_name: str,
        source: SiemExportSource,
        events: tuple[NormalizedEvent, ...],
        last_error: str,
    ) -> None:
        with psycopg.connect(self._database_url) as conn:
            with conn.cursor() as cur:
                for event in events:
                    cur.execute(
                        """
                        INSERT INTO siem_export_dead_letters (
                            id, exporter_name, source, event_id,
                            payload_json, last_error, attempts, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            f"dl_{uuid4().hex}",
                            exporter_name,
                            source.value,
                            event.composite_id,
                            Jsonb(event.model_dump(mode="json")),
                            last_error[:1000],
                            1,
                            datetime.now(timezone.utc),
                        ),
                    )
            conn.commit()
