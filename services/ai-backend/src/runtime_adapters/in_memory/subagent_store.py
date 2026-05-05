"""In-memory ``SubagentStorePort`` projecting from runtime events (PR 1.5).

The store does not own its own state. It walks
:class:`runtime_adapters.in_memory.runtime_api_store.InMemoryRuntimeApiStore`
events for the runs that belong to a conversation and folds the
``SUBAGENT_*`` lifecycle into one :class:`SubagentSnapshot` per ``task_id``.

The fold mirrors what the postgres adapter does in SQL — both adapters return
identical ordering for any given input timeline so tests can be authored once
against either.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from agent_runtime.api.constants import Values
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.records import (
    SubagentLifecycleStatus,
    SubagentSnapshot,
)
from runtime_api.schemas import RuntimeApiEventType, RuntimeEventEnvelope


class _SubagentProjector:
    """Pure folder of SUBAGENT_* events into a single snapshot per task."""

    DEFAULT_SUBAGENT_NAME = "subagent"
    EVENT_HORIZON = datetime.min.replace(tzinfo=timezone.utc)
    _RUNNING_STATES = frozenset(
        {SubagentLifecycleStatus.QUEUED, SubagentLifecycleStatus.RUNNING}
    )

    @classmethod
    def project(
        cls,
        *,
        event: RuntimeEventEnvelope,
        current: SubagentSnapshot | None,
        org_id: str,
        conversation_id: str,
    ) -> SubagentSnapshot | None:
        if event.source is not StreamEventSource.SUBAGENT:
            return None
        task_id = event.task_id
        if task_id is None:
            return None
        if event.event_type is RuntimeApiEventType.SUBAGENT_STARTED:
            return cls._on_started(
                event=event,
                org_id=org_id,
                conversation_id=conversation_id,
                current=current,
            )
        if event.event_type is RuntimeApiEventType.SUBAGENT_PROGRESS:
            return cls._on_progress(
                event=event,
                org_id=org_id,
                conversation_id=conversation_id,
                current=current,
            )
        if event.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED:
            return cls._on_completed(
                event=event,
                org_id=org_id,
                conversation_id=conversation_id,
                current=current,
            )
        return current

    @classmethod
    def _on_started(
        cls,
        *,
        event: RuntimeEventEnvelope,
        current: SubagentSnapshot | None,
        org_id: str,
        conversation_id: str,
    ) -> SubagentSnapshot:
        base = current or cls._seed(
            event=event, org_id=org_id, conversation_id=conversation_id
        )
        return base.model_copy(
            update={
                "subagent_name": cls._subagent_name(event) or base.subagent_name,
                "status": SubagentLifecycleStatus.RUNNING,
                "started_at": event.created_at,
                "objective_summary": event.summary or base.objective_summary,
                "display_title": event.display_title or base.display_title,
            }
        )

    @classmethod
    def _on_progress(
        cls,
        *,
        event: RuntimeEventEnvelope,
        current: SubagentSnapshot | None,
        org_id: str,
        conversation_id: str,
    ) -> SubagentSnapshot:
        if current is None:
            return cls._seed(
                event=event,
                org_id=org_id,
                conversation_id=conversation_id,
            ).model_copy(update={"display_title": event.display_title})
        return current.model_copy(
            update={
                "display_title": event.display_title or current.display_title,
                "status": SubagentLifecycleStatus.RUNNING,
            }
        )

    @classmethod
    def _on_completed(
        cls,
        *,
        event: RuntimeEventEnvelope,
        current: SubagentSnapshot | None,
        org_id: str,
        conversation_id: str,
    ) -> SubagentSnapshot:
        base = current or cls._seed(
            event=event, org_id=org_id, conversation_id=conversation_id
        )
        duration_ms = cls._duration_from_payload(event) or cls._duration_from_started(
            started_at=base.started_at, completed_at=event.created_at
        )
        return base.model_copy(
            update={
                "status": cls._terminal_status(event),
                "completed_at": event.created_at,
                "duration_ms": duration_ms,
                "result_summary": event.summary or base.result_summary,
            }
        )

    @classmethod
    def _seed(
        cls,
        *,
        event: RuntimeEventEnvelope,
        org_id: str,
        conversation_id: str,
    ) -> SubagentSnapshot:
        return SubagentSnapshot(
            task_id=event.task_id or "",
            parent_run_id=event.run_id,
            conversation_id=conversation_id,
            org_id=org_id,
            subagent_name=cls._subagent_name(event),
            status=SubagentLifecycleStatus.RUNNING,
        )

    @classmethod
    def _terminal_status(cls, event: RuntimeEventEnvelope) -> SubagentLifecycleStatus:
        payload_status = (event.status or "").strip().lower()
        if payload_status == Values.Status.CANCELLED:
            return SubagentLifecycleStatus.CANCELLED
        if payload_status == Values.Status.FAILED:
            return SubagentLifecycleStatus.FAILED
        return SubagentLifecycleStatus.COMPLETED

    @classmethod
    def _subagent_name(cls, event: RuntimeEventEnvelope) -> str:
        raw = event.subagent_id or event.payload.get("subagent_name")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:128]
        return cls.DEFAULT_SUBAGENT_NAME

    @staticmethod
    def _duration_from_payload(event: RuntimeEventEnvelope) -> int | None:
        raw = event.payload.get("duration_ms")
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

    @classmethod
    def is_running(cls, status: SubagentLifecycleStatus) -> bool:
        return status in cls._RUNNING_STATES

    @classmethod
    def recency_key(cls, snapshot: SubagentSnapshot) -> datetime:
        return snapshot.completed_at or snapshot.started_at or cls.EVENT_HORIZON


class InMemorySubagentStore:
    """Project subagent snapshots from the in-memory event log.

    The store keeps no mutable state — every read folds events from the wrapped
    ``InMemoryRuntimeApiStore``. Tests that seed events via the worker's normal
    path get the same view as production reads do.
    """

    def __init__(self, store: object) -> None:
        # The store argument is duck-typed so callers can pass either the sync
        # ``InMemoryRuntimeApiStore`` or its async wrapper's ``.underlying``.
        self._store = store

    def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        running_only: bool,
        limit: int,
    ) -> Sequence[SubagentSnapshot]:
        run_ids = self._run_ids_for(org_id=org_id, conversation_id=conversation_id)
        snapshots = self._fold_subagent_events(
            org_id=org_id,
            conversation_id=conversation_id,
            run_ids=run_ids,
        )
        if running_only:
            snapshots = [
                s for s in snapshots if _SubagentProjector.is_running(s.status)
            ]
        snapshots.sort(key=_SubagentProjector.recency_key, reverse=True)
        return tuple(snapshots[:limit])

    def _run_ids_for(self, *, org_id: str, conversation_id: str) -> tuple[str, ...]:
        runs = getattr(self._store, "runs", {})
        return tuple(
            run.run_id
            for run in runs.values()
            if run.org_id == org_id and run.conversation_id == conversation_id
        )

    def _fold_subagent_events(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_ids: tuple[str, ...],
    ) -> list[SubagentSnapshot]:
        events_by_run = getattr(self._store, "events_by_run", {})
        snapshots: dict[str, SubagentSnapshot] = {}
        for run_id in run_ids:
            for event in events_by_run.get(run_id, ()):
                projected = _SubagentProjector.project(
                    event=event,
                    current=snapshots.get(event.task_id) if event.task_id else None,
                    org_id=org_id,
                    conversation_id=conversation_id,
                )
                if projected is None:
                    continue
                snapshots[projected.task_id] = projected
        return list(snapshots.values())


__all__ = ("InMemorySubagentStore",)
