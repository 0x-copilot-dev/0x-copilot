"""Per-run open-lifecycle ledger.

Single source of truth for "what is currently in flight on this run."
Every ``*_STARTED`` event registers an entry; the matching ``*_COMPLETED``
removes it. At run termination the
:class:`~agent_runtime.api.run_termination.RunTerminationCoordinator`
drains the ledger, synthesizing terminal events for any still-open entry
so the frontend never sees a "stuck running" subagent or tool call.

Scope: paired ``*_STARTED`` / ``*_COMPLETED`` lifecycles only. This is
not a generic event bus and intentionally rejects any other event type.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


_LOGGER = logging.getLogger("agent_runtime.observability.lifecycle_ledger")


class LifecycleKind(StrEnum):
    """Kinds of paired lifecycles the ledger tracks."""

    SUBAGENT = "subagent"
    TOOL_CALL = "tool_call"
    MODEL_CALL = "model_call"


@dataclass(frozen=True)
class OpenLifecycleEntry:
    """A still-open lifecycle entry awaiting its terminal event."""

    kind: LifecycleKind
    entity_id: str
    parent_task_id: str | None
    subagent_id: str | None
    started_at: datetime
    # Snapshot of the started-event payload so a synthesized terminal
    # event can carry forward identifying fields (tool_name, subagent_name,
    # etc.) without the caller re-supplying them.
    payload_snapshot: Mapping[str, Any] = field(default_factory=dict)


class LifecycleLedger:
    """Async-safe per-run tracker of open ``*_STARTED`` lifecycles.

    Operations are O(1) and protected by a single ``asyncio.Lock``; the
    ledger sees on the order of tens of operations per run, so finer
    sharding is unnecessary.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[LifecycleKind, str], OpenLifecycleEntry] = {}
        self._lock = asyncio.Lock()

    async def open(self, entry: OpenLifecycleEntry) -> None:
        """Register a new open entry.

        Re-opening the same ``(kind, entity_id)`` is a producer bug — the
        previous entry is replaced and a warning is logged. We never
        silently double-count.
        """

        key = (entry.kind, entry.entity_id)
        async with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                _LOGGER.warning(
                    "lifecycle_ledger.open_replaces_existing",
                    extra={
                        "metadata": {
                            "kind": entry.kind.value,
                            "entity_id": entry.entity_id,
                            "previous_started_at": existing.started_at.isoformat(),
                        }
                    },
                )
            self._entries[key] = entry

    async def close(
        self, *, kind: LifecycleKind, entity_id: str
    ) -> OpenLifecycleEntry | None:
        """Remove the matching open entry.

        Closing an unknown ``(kind, entity_id)`` is a logged no-op — the
        ledger is defensive against duplicate ``*_COMPLETED`` deliveries
        (e.g. retries) and never raises on the close side.
        """

        key = (kind, entity_id)
        async with self._lock:
            removed = self._entries.pop(key, None)
        if removed is None:
            _LOGGER.debug(
                "lifecycle_ledger.close_unknown",
                extra={
                    "metadata": {
                        "kind": kind.value,
                        "entity_id": entity_id,
                    }
                },
            )
        return removed

    async def open_entries(self) -> Sequence[OpenLifecycleEntry]:
        """Snapshot of all currently-open entries, in insertion order."""

        async with self._lock:
            return tuple(self._entries.values())

    async def open_count(self) -> int:
        """Total number of open entries (across all kinds)."""

        async with self._lock:
            return len(self._entries)


class LifecycleEventInspector:
    """Maps a runtime event type + payload to a lifecycle ledger op.

    Centralized so `RuntimeEventProducer` does not have to know each
    event type's id field. Adding a new lifecycle pair = adding one row
    to ``_OPEN_FIELDS`` / ``_CLOSE_FIELDS`` here, nowhere else.
    """

    # event_type.value -> (kind, payload-key-for-entity-id)
    _OPEN_FIELDS: dict[str, tuple[LifecycleKind, str]] = {
        "subagent_started": (LifecycleKind.SUBAGENT, "task_id"),
        "tool_call_started": (LifecycleKind.TOOL_CALL, "call_id"),
        "model_call_started": (LifecycleKind.MODEL_CALL, "message_id"),
    }

    _CLOSE_FIELDS: dict[str, tuple[LifecycleKind, str]] = {
        "subagent_completed": (LifecycleKind.SUBAGENT, "task_id"),
        "tool_call_completed": (LifecycleKind.TOOL_CALL, "call_id"),
        "model_call_completed": (LifecycleKind.MODEL_CALL, "message_id"),
    }

    @classmethod
    def open_op(
        cls,
        *,
        event_type_value: str,
        payload: Mapping[str, Any],
        parent_task_id: str | None,
        subagent_id: str | None,
    ) -> OpenLifecycleEntry | None:
        """Return an ``OpenLifecycleEntry`` if the event opens a lifecycle."""

        binding = cls._OPEN_FIELDS.get(event_type_value)
        if binding is None:
            return None
        kind, id_field = binding
        entity_id = payload.get(id_field)
        if not isinstance(entity_id, str) or not entity_id:
            # Producer emitted a started event without a usable id field.
            # Don't crash the producer — just skip the ledger op.
            _LOGGER.warning(
                "lifecycle_event_inspector.missing_entity_id",
                extra={
                    "metadata": {
                        "event_type": event_type_value,
                        "expected_field": id_field,
                    }
                },
            )
            return None
        return OpenLifecycleEntry(
            kind=kind,
            entity_id=entity_id,
            parent_task_id=parent_task_id,
            subagent_id=subagent_id,
            started_at=datetime.now(timezone.utc),
            payload_snapshot=dict(payload),
        )

    @classmethod
    def close_op(
        cls,
        *,
        event_type_value: str,
        payload: Mapping[str, Any],
    ) -> tuple[LifecycleKind, str] | None:
        """Return ``(kind, entity_id)`` if the event closes a lifecycle."""

        binding = cls._CLOSE_FIELDS.get(event_type_value)
        if binding is None:
            return None
        kind, id_field = binding
        entity_id = payload.get(id_field)
        if not isinstance(entity_id, str) or not entity_id:
            return None
        return (kind, entity_id)


__all__ = (
    "LifecycleEventInspector",
    "LifecycleKind",
    "LifecycleLedger",
    "OpenLifecycleEntry",
)
