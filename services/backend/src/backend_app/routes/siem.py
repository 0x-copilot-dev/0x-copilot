"""C9 SIEM admin routes — pause/resume/replay + dead-letter inspection.

The pump itself runs as a separate async task; these routes write the
control rows the pump consults at the start of each tick. Per the C9
spec the surface is small and operator-driven:

  - ``GET /v1/siem/exporters`` — list configured exporters with pause +
    cursor + dead-letter aggregates.
  - ``POST /v1/siem/exporters/{name}/{pause,resume}`` — flip the pause flag.
  - ``POST /v1/siem/exporters/{name}/replay?from_id=&to_id=`` — request a
    backfill window; the pump rewinds the cursor to ``from_id`` on its
    next tick and dead-letters anything outside the window.
  - ``GET /v1/siem/dead_letters`` — paginated read of the parking lot.

All routes require ``admin:siem`` (per A10). The pump and exporter list
are not part of this module — this is the read/control surface only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from enterprise_service_contracts.headers import ORG_HEADER, USER_HEADER
from enterprise_service_contracts.scopes import ADMIN_SIEM
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes


_DEFAULT_DEAD_LETTER_LIMIT = 100
_MAX_DEAD_LETTER_LIMIT = 1_000


@dataclass(frozen=True)
class _ExporterControl:
    exporter_name: str
    paused_at: datetime | None
    replay_from_id: str | None
    replay_to_id: str | None
    replay_requested_at: datetime | None


class ExporterCursorRow(BaseModel):
    source: str = Field(min_length=1)
    last_event_id: str | None
    last_processed_at: datetime


class ExporterStatus(BaseModel):
    name: str = Field(min_length=1)
    paused_at: datetime | None
    replay_from_id: str | None
    replay_to_id: str | None
    replay_requested_at: datetime | None
    dead_letter_count: int = Field(ge=0)
    cursors: tuple[ExporterCursorRow, ...] = ()


class ExporterListResponse(BaseModel):
    exporters: tuple[ExporterStatus, ...] = ()


class ReplayResponse(BaseModel):
    name: str
    from_id: str
    to_id: str | None
    requested_at: datetime


class DeadLetterRow(BaseModel):
    id: str
    exporter_name: str
    source: str
    event_id: str
    last_error: str
    attempts: int = Field(ge=0)
    created_at: datetime


class DeadLetterListResponse(BaseModel):
    dead_letters: tuple[DeadLetterRow, ...] = ()


class _Sql:
    """Centralised SQL strings for the admin surface (DRY across routes)."""

    LIST_CURSORS = (
        "SELECT exporter_name, source, last_event_id, last_processed_at "
        "FROM siem_export_cursors"
    )
    LIST_CONTROLS = (
        "SELECT exporter_name, paused_at, replay_from_id, replay_to_id, "
        "replay_requested_at FROM siem_exporter_controls"
    )
    DEAD_LETTER_COUNTS = (
        "SELECT exporter_name, COUNT(*) AS n "
        "FROM siem_export_dead_letters GROUP BY exporter_name"
    )
    UPSERT_PAUSE = (
        "INSERT INTO siem_exporter_controls "
        "  (exporter_name, paused_at, updated_at, updated_by_user_id) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (exporter_name) DO UPDATE "
        "SET paused_at = EXCLUDED.paused_at, "
        "    updated_at = EXCLUDED.updated_at, "
        "    updated_by_user_id = EXCLUDED.updated_by_user_id"
    )
    UPSERT_REPLAY = (
        "INSERT INTO siem_exporter_controls "
        "  (exporter_name, replay_from_id, replay_to_id, replay_requested_at, "
        "   updated_at, updated_by_user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (exporter_name) DO UPDATE "
        "SET replay_from_id = EXCLUDED.replay_from_id, "
        "    replay_to_id = EXCLUDED.replay_to_id, "
        "    replay_requested_at = EXCLUDED.replay_requested_at, "
        "    updated_at = EXCLUDED.updated_at, "
        "    updated_by_user_id = EXCLUDED.updated_by_user_id"
    )
    LIST_DEAD_LETTERS = (
        "SELECT id, exporter_name, source, event_id, last_error, attempts, "
        "       created_at "
        "  FROM siem_export_dead_letters "
        " WHERE (%(exporter)s::text IS NULL OR exporter_name = %(exporter)s) "
        " ORDER BY created_at DESC "
        " LIMIT %(limit)s"
    )


def register_siem_admin_routes(app: FastAPI) -> None:
    """Attach the C9 SIEM admin endpoints. Idempotent across app rebuilds."""

    @app.get(
        "/v1/siem/exporters",
        response_model=ExporterListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_SIEM))],
    )
    def list_exporters(request: Request) -> ExporterListResponse:
        BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=request.headers.get(ORG_HEADER, ""),
            user_id=request.headers.get(USER_HEADER, ""),
        )
        configured = _configured_exporter_names()
        cursors = _query_cursors()
        controls = _query_controls()
        counts = _query_dead_letter_counts()
        rows: list[ExporterStatus] = []
        for name in sorted(set(configured) | cursors.keys() | controls.keys()):
            ctrl = controls.get(name)
            rows.append(
                ExporterStatus(
                    name=name,
                    paused_at=ctrl.paused_at if ctrl else None,
                    replay_from_id=ctrl.replay_from_id if ctrl else None,
                    replay_to_id=ctrl.replay_to_id if ctrl else None,
                    replay_requested_at=ctrl.replay_requested_at if ctrl else None,
                    dead_letter_count=counts.get(name, 0),
                    cursors=cursors.get(name, ()),
                )
            )
        return ExporterListResponse(exporters=tuple(rows))

    @app.post(
        "/v1/siem/exporters/{name}/pause",
        dependencies=[Depends(RequireScopes(ADMIN_SIEM))],
    )
    def pause(request: Request, name: str) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=request.headers.get(ORG_HEADER, ""),
            user_id=request.headers.get(USER_HEADER, ""),
        )
        now = datetime.now(timezone.utc)
        _execute(_Sql.UPSERT_PAUSE, (name, now, now, identity.user_id or None))
        return {"name": name, "paused_at": now.isoformat()}

    @app.post(
        "/v1/siem/exporters/{name}/resume",
        dependencies=[Depends(RequireScopes(ADMIN_SIEM))],
    )
    def resume(request: Request, name: str) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=request.headers.get(ORG_HEADER, ""),
            user_id=request.headers.get(USER_HEADER, ""),
        )
        now = datetime.now(timezone.utc)
        _execute(_Sql.UPSERT_PAUSE, (name, None, now, identity.user_id or None))
        return {"name": name, "paused_at": None}

    @app.post(
        "/v1/siem/exporters/{name}/replay",
        response_model=ReplayResponse,
        dependencies=[Depends(RequireScopes(ADMIN_SIEM))],
    )
    def replay(
        request: Request,
        name: str,
        from_id: str = Query(..., min_length=1),
        to_id: str | None = Query(None, min_length=1),
    ) -> ReplayResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=request.headers.get(ORG_HEADER, ""),
            user_id=request.headers.get(USER_HEADER, ""),
        )
        now = datetime.now(timezone.utc)
        _execute(
            _Sql.UPSERT_REPLAY,
            (name, from_id, to_id, now, now, identity.user_id or None),
        )
        return ReplayResponse(name=name, from_id=from_id, to_id=to_id, requested_at=now)

    @app.get(
        "/v1/siem/dead_letters",
        response_model=DeadLetterListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_SIEM))],
    )
    def list_dead_letters(
        request: Request,
        exporter: str | None = Query(None, min_length=1),
        limit: int = Query(_DEFAULT_DEAD_LETTER_LIMIT, ge=1, le=_MAX_DEAD_LETTER_LIMIT),
    ) -> DeadLetterListResponse:
        BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=request.headers.get(ORG_HEADER, ""),
            user_id=request.headers.get(USER_HEADER, ""),
        )
        rows = _query_dead_letters(exporter=exporter, limit=limit)
        return DeadLetterListResponse(
            dead_letters=tuple(DeadLetterRow(**row) for row in rows)
        )


def _configured_exporter_names() -> tuple[str, ...]:
    """Return the per-process configured exporter names from env.

    The pump-side wiring writes the comma-separated list to
    ``SIEM_EXPORT_BACKEND_NAMES`` when multiple exporters are active.
    Falls back to the single-name ``SIEM_EXPORT_BACKEND`` for the common
    one-exporter setup. Returns ``()`` when nothing is configured.
    """

    multi = os.environ.get("SIEM_EXPORT_BACKEND_NAMES", "").strip()
    if multi:
        return tuple(part.strip() for part in multi.split(",") if part.strip())
    single = os.environ.get("SIEM_EXPORT_BACKEND", "").strip()
    if single and single.lower() not in {"null", "none"}:
        return (single,)
    return ()


def _database_url_or_503() -> str:
    url = os.environ.get("BACKEND_DATABASE_URL")
    if not url:
        # Surfacing the deployment misconfig here keeps the route closed
        # rather than silently returning empty results.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "siem admin routes require BACKEND_DATABASE_URL",
        )
    return url


def _query_cursors() -> dict[str, tuple[ExporterCursorRow, ...]]:
    rows = _query(_Sql.LIST_CURSORS, ())
    grouped: dict[str, list[ExporterCursorRow]] = {}
    for row in rows:
        grouped.setdefault(row["exporter_name"], []).append(
            ExporterCursorRow(
                source=row["source"],
                last_event_id=row["last_event_id"],
                last_processed_at=row["last_processed_at"],
            )
        )
    return {name: tuple(items) for name, items in grouped.items()}


def _query_controls() -> dict[str, _ExporterControl]:
    rows = _query(_Sql.LIST_CONTROLS, ())
    return {
        row["exporter_name"]: _ExporterControl(
            exporter_name=row["exporter_name"],
            paused_at=row["paused_at"],
            replay_from_id=row["replay_from_id"],
            replay_to_id=row["replay_to_id"],
            replay_requested_at=row["replay_requested_at"],
        )
        for row in rows
    }


def _query_dead_letter_counts() -> dict[str, int]:
    rows = _query(_Sql.DEAD_LETTER_COUNTS, ())
    return {row["exporter_name"]: int(row["n"]) for row in rows}


def _query_dead_letters(*, exporter: str | None, limit: int) -> list[dict[str, Any]]:
    return _query(
        _Sql.LIST_DEAD_LETTERS,
        {"exporter": exporter, "limit": limit},
    )


def _execute(sql: str, params: tuple) -> None:
    import psycopg

    with psycopg.connect(_database_url_or_503()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def _query(sql: str, params: tuple | dict) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_database_url_or_503(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
