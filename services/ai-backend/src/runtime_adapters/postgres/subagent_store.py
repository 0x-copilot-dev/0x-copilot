"""Postgres-backed ``SubagentStorePort`` (PR 1.5).

Projects ``SUBAGENT_*`` rows in ``runtime_events`` into one
:class:`SubagentSnapshot` per ``task_id``. Borrows the parent store's pool
and ``_tenant_connection`` helper for RLS scoping; every read is bounded to
the most-recent ``limit`` task ids per conversation so we never scan the
whole event log.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agent_runtime.api.constants import Values
from agent_runtime.persistence.records import (
    SubagentLifecycleStatus,
    SubagentSnapshot,
)
from runtime_api.schemas import RuntimeApiEventType


_SUBAGENT_SOURCE = "subagent"


class _SubagentRowFolder:
    """Fold the rows for one ``task_id`` into a :class:`SubagentSnapshot`."""

    @classmethod
    def fold(
        cls,
        *,
        rows: Sequence[dict[str, object]],
        org_id: str,
        conversation_id: str,
    ) -> SubagentSnapshot | None:
        if not rows:
            return None
        ordered = sorted(rows, key=lambda row: row["created_at"])
        snapshot = cls._seed(
            row=ordered[0],
            org_id=org_id,
            conversation_id=conversation_id,
        )
        for row in ordered:
            snapshot = cls._apply(snapshot=snapshot, row=row)
        return snapshot

    @classmethod
    def _seed(
        cls,
        *,
        row: dict[str, object],
        org_id: str,
        conversation_id: str,
    ) -> SubagentSnapshot:
        return SubagentSnapshot(
            task_id=str(row["task_id"]),
            parent_run_id=str(row["run_id"]),
            conversation_id=conversation_id,
            org_id=org_id,
            subagent_name=cls._subagent_name(row),
            status=SubagentLifecycleStatus.RUNNING,
        )

    @classmethod
    def _apply(
        cls,
        *,
        snapshot: SubagentSnapshot,
        row: dict[str, object],
    ) -> SubagentSnapshot:
        event_type = str(row.get("event_type") or "")
        created_at = cls._coerce_datetime(row["created_at"])
        if event_type == RuntimeApiEventType.SUBAGENT_STARTED.value:
            return snapshot.model_copy(
                update={
                    "status": SubagentLifecycleStatus.RUNNING,
                    "started_at": created_at,
                    "objective_summary": cls._optional_text(row.get("summary"))
                    or snapshot.objective_summary,
                    "display_title": cls._optional_text(row.get("display_title"))
                    or snapshot.display_title,
                    "subagent_name": cls._subagent_name(row),
                }
            )
        if event_type == RuntimeApiEventType.SUBAGENT_PROGRESS.value:
            return snapshot.model_copy(
                update={
                    "display_title": cls._optional_text(row.get("display_title"))
                    or snapshot.display_title,
                }
            )
        if event_type == RuntimeApiEventType.SUBAGENT_COMPLETED.value:
            duration_ms = cls._duration_from_payload(row) or cls._duration_from_started(
                started_at=snapshot.started_at, completed_at=created_at
            )
            return snapshot.model_copy(
                update={
                    "status": cls._terminal_status(row),
                    "completed_at": created_at,
                    "duration_ms": duration_ms,
                    "result_summary": cls._optional_text(row.get("summary"))
                    or snapshot.result_summary,
                }
            )
        return snapshot

    @classmethod
    def _terminal_status(cls, row: dict[str, object]) -> SubagentLifecycleStatus:
        raw = (cls._optional_text(row.get("status")) or "").lower()
        if raw == Values.Status.CANCELLED:
            return SubagentLifecycleStatus.CANCELLED
        if raw == Values.Status.FAILED:
            return SubagentLifecycleStatus.FAILED
        return SubagentLifecycleStatus.COMPLETED

    @classmethod
    def _subagent_name(cls, row: dict[str, object]) -> str:
        candidates = (row.get("subagent_id"), cls._payload_value(row, "subagent_name"))
        for candidate in candidates:
            text = cls._optional_text(candidate)
            if text:
                return text[:128]
        return "subagent"

    @staticmethod
    def _payload_value(row: dict[str, object], key: str) -> object:
        payload = row.get("payload_json_redacted")
        if isinstance(payload, dict):
            return payload.get(key)
        return None

    @staticmethod
    def _duration_from_payload(row: dict[str, object]) -> int | None:
        payload = row.get("payload_json_redacted")
        if not isinstance(payload, dict):
            return None
        raw = payload.get("duration_ms")
        if isinstance(raw, int) and raw >= 0:
            return raw
        return None

    @staticmethod
    def _duration_from_started(
        *, started_at: datetime | None, completed_at: datetime
    ) -> int | None:
        if started_at is None:
            return None
        delta = completed_at - started_at
        return max(0, round(delta.total_seconds() * 1000))

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _coerce_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


class PostgresSubagentStore:
    """Postgres-backed read port. Composes a ``PostgresRuntimeApiStore``."""

    _RECENT_TASK_LIMIT_HARD_CAP = 200

    def __init__(self, parent: object) -> None:
        self._parent = parent

    async def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        running_only: bool,
        limit: int,
    ) -> Sequence[SubagentSnapshot]:
        capped = max(1, min(limit, self._RECENT_TASK_LIMIT_HARD_CAP))
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            task_ids = await self._recent_task_ids(
                conn=conn,
                org_id=org_id,
                conversation_id=conversation_id,
                running_only=running_only,
                limit=capped,
            )
            if not task_ids:
                return ()
            event_rows = await self._events_for_tasks(
                conn=conn,
                org_id=org_id,
                conversation_id=conversation_id,
                task_ids=task_ids,
            )
        return self._fold(
            rows=event_rows,
            order=task_ids,
            org_id=org_id,
            conversation_id=conversation_id,
            running_only=running_only,
        )

    @staticmethod
    async def _recent_task_ids(
        *,
        conn: object,
        org_id: str,
        conversation_id: str,
        running_only: bool,
        limit: int,
    ) -> tuple[str, ...]:
        # We pick the LATEST event per task to decide whether to include it
        # under ``running_only``: a task is "running" iff its most recent
        # event is not SUBAGENT_COMPLETED.
        sql = """
            SELECT task_id, MAX(created_at) AS last_at,
                   BOOL_OR(event_type = %s) AS is_complete
            FROM runtime_events
            WHERE org_id = %s
              AND conversation_id = %s
              AND source = %s
              AND task_id IS NOT NULL
            GROUP BY task_id
            ORDER BY last_at DESC
            LIMIT %s
        """
        cur = await conn.execute(  # type: ignore[attr-defined]
            sql,
            (
                RuntimeApiEventType.SUBAGENT_COMPLETED.value,
                org_id,
                conversation_id,
                _SUBAGENT_SOURCE,
                limit,
            ),
        )
        rows = await cur.fetchall()
        result: list[str] = []
        for row in rows:
            mapping = dict(row)
            if running_only and bool(mapping.get("is_complete")):
                continue
            result.append(str(mapping["task_id"]))
        return tuple(result)

    @staticmethod
    async def _events_for_tasks(
        *,
        conn: object,
        org_id: str,
        conversation_id: str,
        task_ids: tuple[str, ...],
    ) -> dict[str, list[dict[str, object]]]:
        sql = """
            SELECT task_id, event_type, created_at, summary, display_title,
                   status, subagent_id, run_id, payload_json_redacted
            FROM runtime_events
            WHERE org_id = %s
              AND conversation_id = %s
              AND source = %s
              AND task_id = ANY(%s)
            ORDER BY task_id, created_at ASC
        """
        cur = await conn.execute(  # type: ignore[attr-defined]
            sql,
            (org_id, conversation_id, _SUBAGENT_SOURCE, list(task_ids)),
        )
        rows = await cur.fetchall()
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            mapping = dict(row)
            grouped.setdefault(str(mapping["task_id"]), []).append(mapping)
        return grouped

    @staticmethod
    def _fold(
        *,
        rows: dict[str, list[dict[str, object]]],
        order: tuple[str, ...],
        org_id: str,
        conversation_id: str,
        running_only: bool,
    ) -> tuple[SubagentSnapshot, ...]:
        snapshots: list[SubagentSnapshot] = []
        for task_id in order:
            snapshot = _SubagentRowFolder.fold(
                rows=rows.get(task_id, ()),
                org_id=org_id,
                conversation_id=conversation_id,
            )
            if snapshot is None:
                continue
            if running_only and snapshot.status not in {
                SubagentLifecycleStatus.QUEUED,
                SubagentLifecycleStatus.RUNNING,
            }:
                continue
            snapshots.append(snapshot)
        return tuple(snapshots)


__all__ = ("PostgresSubagentStore",)
