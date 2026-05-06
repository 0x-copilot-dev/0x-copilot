"""Typed notification preferences store (PR B4 / 8.0.3e).

Backs ``services/backend/migrations/0024_notification_preferences.sql``.

Two tables, one store:

* ``notification_preferences`` — one row per ``(user_id, event_kind,
  channel)``. Cell value is ``enabled BOOLEAN``. Absence of a row
  means "use the deployment default" — the route layer hydrates so
  the FE always sees a complete matrix.
* ``notification_quiet_hours`` — one row per user. ``enabled`` toggles
  the entire feature; ``from_local`` / ``to_local`` are wall-clock
  ``HH:MM`` strings; ``tz`` is the user's IANA tz id (read at
  dispatch time, not on save). During quiet hours only
  ``approval_requested`` (critical-by-default) breaks through.

The dispatcher itself is out of scope for this PR — this is the
storage layer the dispatcher will read once it ships.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationEventKind(StrEnum):
    """The event kinds the v2 dispatcher targets."""

    LONG_TASK_FINISHED = "long_task_finished"
    APPROVAL_REQUESTED = "approval_requested"
    MENTION = "mention"
    CONNECTOR_ERROR = "connector_error"
    WEEKLY_DIGEST = "weekly_digest"
    PRODUCT_UPDATES = "product_updates"


class NotificationChannel(StrEnum):
    """Delivery channels."""

    IN_APP = "in_app"
    EMAIL = "email"
    PUSH = "push"


class NotificationPreferenceRow(BaseModel):
    """A single ``(event_kind, channel) → enabled`` cell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    event_kind: NotificationEventKind
    channel: NotificationChannel
    enabled: bool
    updated_at: datetime = Field(default_factory=_now)


class NotificationQuietHoursRow(BaseModel):
    """One row per user. Wall-clock strings + IANA tz id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    enabled: bool = False
    from_local: str = "20:00"
    to_local: str = "08:00"
    tz: str = "UTC"
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("from_local", "to_local")
    @classmethod
    def _validate_hhmm(cls, value: str) -> str:
        if not _hhmm_pattern_ok(value):
            raise ValueError("invalid_time_format")
        return value


def _hhmm_pattern_ok(value: str) -> bool:
    """Lightweight ``HH:MM`` (00:00..23:59) validation. Avoids a heavy
    regex import for one cheap check."""

    if len(value) != 5 or value[2] != ":":
        return False
    head, tail = value[:2], value[3:]
    if not (head.isdigit() and tail.isdigit()):
        return False
    hour = int(head)
    minute = int(tail)
    return 0 <= hour <= 23 and 0 <= minute <= 59


class NotificationPrefsStore(Protocol):
    """Adapter contract — every adapter implements every method."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def list_preferences(
        self, *, user_id: str
    ) -> tuple[NotificationPreferenceRow, ...]:
        """Return every stored cell for the user (possibly empty)."""

    def get_quiet_hours(self, *, user_id: str) -> NotificationQuietHoursRow | None:
        """Return the user's quiet-hours row or None."""

    def upsert_preference(
        self,
        row: NotificationPreferenceRow,
        *,
        conn: Any | None = None,
    ) -> NotificationPreferenceRow:
        """Insert or update a single ``(user, event, channel)`` cell."""

    def replace_preferences(
        self,
        *,
        user_id: str,
        rows: tuple[NotificationPreferenceRow, ...],
        conn: Any | None = None,
    ) -> tuple[NotificationPreferenceRow, ...]:
        """Atomically replace the user's full preferences set.

        Used by the bulk PUT path. Implementations MUST upsert every
        row in ``rows`` and leave any cells not present in ``rows``
        intact (partial replace) so a single-cell PUT doesn't reset
        siblings to deployment defaults.
        """

    def upsert_quiet_hours(
        self,
        row: NotificationQuietHoursRow,
        *,
        conn: Any | None = None,
    ) -> NotificationQuietHoursRow:
        """Insert or update the user's quiet-hours row."""


@dataclass
class InMemoryNotificationPrefsStore:
    """Dict-backed adapter for tests + dev. Mirrors postgres semantics."""

    preferences: dict[
        tuple[str, NotificationEventKind, NotificationChannel],
        NotificationPreferenceRow,
    ] = field(default_factory=dict)
    quiet_hours: dict[str, NotificationQuietHoursRow] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def list_preferences(
        self, *, user_id: str
    ) -> tuple[NotificationPreferenceRow, ...]:
        return tuple(
            row
            for (row_user, _event, _channel), row in self.preferences.items()
            if row_user == user_id
        )

    def get_quiet_hours(self, *, user_id: str) -> NotificationQuietHoursRow | None:
        return self.quiet_hours.get(user_id)

    def upsert_preference(
        self,
        row: NotificationPreferenceRow,
        *,
        conn: Any | None = None,
    ) -> NotificationPreferenceRow:
        del conn
        saved = row.model_copy(update={"updated_at": _now()})
        self.preferences[(row.user_id, row.event_kind, row.channel)] = saved
        return saved

    def replace_preferences(
        self,
        *,
        user_id: str,
        rows: tuple[NotificationPreferenceRow, ...],
        conn: Any | None = None,
    ) -> tuple[NotificationPreferenceRow, ...]:
        del conn
        saved: list[NotificationPreferenceRow] = []
        for row in rows:
            if row.user_id != user_id:
                raise ValueError("row.user_id must match the bulk-PUT user_id")
            persisted = row.model_copy(update={"updated_at": _now()})
            self.preferences[(row.user_id, row.event_kind, row.channel)] = persisted
            saved.append(persisted)
        return tuple(saved)

    def upsert_quiet_hours(
        self,
        row: NotificationQuietHoursRow,
        *,
        conn: Any | None = None,
    ) -> NotificationQuietHoursRow:
        del conn
        saved = row.model_copy(update={"updated_at": _now()})
        self.quiet_hours[row.user_id] = saved
        return saved


__all__ = [
    "InMemoryNotificationPrefsStore",
    "NotificationChannel",
    "NotificationEventKind",
    "NotificationPrefsStore",
    "NotificationPreferenceRow",
    "NotificationQuietHoursRow",
]
