"""Notification dispatcher gate: v1 JSONB → v2 typed-table cutover (PR 8.0.5).

A single gate that decides whether one (user, event_kind, channel)
notification should fire. Reads from one of two sources depending on
the ``BACKEND_NOTIFICATION_DISPATCHER_VERSION`` env:

* ``v1`` (default): legacy JSONB blob in
  ``user_preferences.preferences.notifications.matrix`` —
  ``{event: {channel: enabled}}`` written by PR 4.1's
  ``register_me_preferences_routes``.
* ``v2``: typed ``notification_preferences`` table populated by
  PR B4's routes + the PR 8.0.3e backfill script. Adds the
  ``notification_quiet_hours`` carve-out: only ``approval_requested``
  events break through quiet hours; everything else is suppressed.

The gate is the *single* place the cutover lives — a future
dispatcher impl can import :class:`NotificationGate` and call
``gate.should_notify(...)`` without knowing which read source is
active. Flipping the env from v1 → v2 is the entire cutover; v1's
read path stays available for one release cycle as a rollback.

Both backends are read-only on this path — the gate never writes.
That keeps the dual-read window safe to schedule (the backfill
script keeps the typed tables in sync; the v1 dispatcher is the
only writer until cutover; flipping just changes which side the
dispatcher reads).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Protocol, runtime_checkable

try:  # Python 3.9+ stdlib; ai-backend pins 3.13 so always present.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover — defensive
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment, misc]

from backend_app.notifications.store import (
    NotificationChannel,
    NotificationEventKind,
    NotificationPrefsStore,
    NotificationQuietHoursRow,
)


# v1 → v2 mappings reused from the backfill script. Kept in sync there
# (it's the only other consumer).
_V1_TO_V2_EVENT: Mapping[str, str] = {
    "mention": NotificationEventKind.MENTION.value,
    "approval_needed": NotificationEventKind.APPROVAL_REQUESTED.value,
    "run_finished": NotificationEventKind.LONG_TASK_FINISHED.value,
    "weekly_digest": NotificationEventKind.WEEKLY_DIGEST.value,
}
_V2_TO_V1_EVENT: Mapping[str, str] = {v: k for k, v in _V1_TO_V2_EVENT.items()}
_V2_TO_V1_CHANNEL: Mapping[str, str] = {
    NotificationChannel.EMAIL.value: "email",
    NotificationChannel.IN_APP.value: "desktop",
    # ``push`` (v2) had no v1 counterpart — v1 readers default to off.
}


@runtime_checkable
class V1MatrixReader(Protocol):
    """Read the legacy JSONB matrix for a single user.

    The dispatcher only needs read access; we deliberately accept a
    minimal Protocol rather than the full ``MeStore`` so tests can
    inject a dict-backed fake without standing up the larger store.
    """

    def read_v1_matrix(self, *, user_id: str) -> Mapping[str, Mapping[str, bool]]:
        """Return ``{event: {channel: enabled}}`` or ``{}`` if absent."""


@dataclass(frozen=True)
class NotificationGateConfig:
    """Operator-tunable knobs read once at gate construction."""

    version: str
    use_v2: bool

    @classmethod
    def from_env(cls) -> "NotificationGateConfig":
        raw = (
            os.environ.get("BACKEND_NOTIFICATION_DISPATCHER_VERSION", "v1")
            .strip()
            .lower()
        )
        return cls(version=raw, use_v2=raw == "v2")


class NotificationGate:
    """Single decision point for "should this notification fire?".

    Composition is by injection — the gate doesn't import the v1
    store directly because the legacy ``MeStore`` lives in
    ``backend_app.identity`` and the v2 store lives in
    ``backend_app.notifications``; depending on either eagerly would
    invert the layering on at least one side. Callers wire both at
    construction time.
    """

    def __init__(
        self,
        *,
        v1_reader: V1MatrixReader,
        v2_store: NotificationPrefsStore,
        config: NotificationGateConfig | None = None,
        clock: "callable | None" = None,  # noqa: UP037 — runtime-callable
    ) -> None:
        self._v1 = v1_reader
        self._v2 = v2_store
        self._config = config or NotificationGateConfig.from_env()
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))

    def should_notify(
        self,
        *,
        user_id: str,
        event_kind: NotificationEventKind,
        channel: NotificationChannel,
    ) -> bool:
        """Decide whether the dispatcher should send.

        v2 path additionally consults quiet hours: during a user's
        configured quiet window, only ``approval_requested`` events
        fire — everything else suppresses.
        """

        if self._config.use_v2:
            return self._should_notify_v2(
                user_id=user_id, event_kind=event_kind, channel=channel
            )
        return self._should_notify_v1(
            user_id=user_id, event_kind=event_kind, channel=channel
        )

    # -- v2 ------------------------------------------------------------

    def _should_notify_v2(
        self,
        *,
        user_id: str,
        event_kind: NotificationEventKind,
        channel: NotificationChannel,
    ) -> bool:
        if self._is_quiet_now(user_id=user_id, event_kind=event_kind):
            return False
        rows = self._v2.list_preferences(user_id=user_id)
        for row in rows:
            if row.event_kind is event_kind and row.channel is channel:
                return row.enabled
        # Absent row ⇒ deployment default. The route-layer hydration
        # mirrors this so the FE always sees a complete matrix; here
        # we encode the conservative "off until the user opts in" rule
        # for push, "on" for in_app/email on the high-signal events.
        return _DEPLOYMENT_DEFAULTS.get((event_kind, channel), False)

    def _is_quiet_now(
        self,
        *,
        user_id: str,
        event_kind: NotificationEventKind,
    ) -> bool:
        # ``approval_requested`` is critical-by-default; never gated by
        # quiet hours.
        if event_kind is NotificationEventKind.APPROVAL_REQUESTED:
            return False
        quiet = self._v2.get_quiet_hours(user_id=user_id)
        if quiet is None or not quiet.enabled:
            return False
        return _is_within_quiet_window(quiet=quiet, now=self._clock())

    # -- v1 ------------------------------------------------------------

    def _should_notify_v1(
        self,
        *,
        user_id: str,
        event_kind: NotificationEventKind,
        channel: NotificationChannel,
    ) -> bool:
        v1_event = _V2_TO_V1_EVENT.get(event_kind.value)
        v1_channel = _V2_TO_V1_CHANNEL.get(channel.value)
        if v1_event is None or v1_channel is None:
            # v2 added events (connector_error, product_updates) and a
            # channel (push) that v1 never wrote. Default to off so a
            # v1-era dispatcher doesn't surprise users with new alerts.
            return False
        matrix = self._v1.read_v1_matrix(user_id=user_id) or {}
        cell = matrix.get(v1_event, {}) if isinstance(matrix, Mapping) else {}
        if not isinstance(cell, Mapping):
            return False
        value = cell.get(v1_channel)
        if isinstance(value, bool):
            return value
        return _DEPLOYMENT_DEFAULTS.get((event_kind, channel), False)


_DEPLOYMENT_DEFAULTS: dict[tuple[NotificationEventKind, NotificationChannel], bool] = {
    # Mirrors the route-layer hydration in
    # ``register_notification_preferences_routes`` so absence-of-row
    # produces the same value either way.
    (NotificationEventKind.LONG_TASK_FINISHED, NotificationChannel.IN_APP): True,
    (NotificationEventKind.APPROVAL_REQUESTED, NotificationChannel.IN_APP): True,
    (NotificationEventKind.APPROVAL_REQUESTED, NotificationChannel.EMAIL): True,
    (NotificationEventKind.MENTION, NotificationChannel.IN_APP): True,
    (NotificationEventKind.MENTION, NotificationChannel.EMAIL): True,
    (NotificationEventKind.CONNECTOR_ERROR, NotificationChannel.IN_APP): True,
    (NotificationEventKind.CONNECTOR_ERROR, NotificationChannel.EMAIL): True,
    (NotificationEventKind.WEEKLY_DIGEST, NotificationChannel.EMAIL): True,
}


def _is_within_quiet_window(*, quiet: NotificationQuietHoursRow, now: datetime) -> bool:
    local = _local_time(now=now, tz=quiet.tz)
    if local is None:
        return False
    start = _parse_hhmm(quiet.from_local)
    end = _parse_hhmm(quiet.to_local)
    if start is None or end is None:
        return False
    if start <= end:
        return start <= local < end
    # Overnight window (e.g. 21:00 → 07:00). True for either half.
    return local >= start or local < end


def _local_time(*, now: datetime, tz: str) -> time | None:
    if ZoneInfo is None:
        return None
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        return None
    return now.astimezone(zone).timetz().replace(tzinfo=None)


def _parse_hhmm(value: str) -> time | None:
    if len(value) != 5 or value[2] != ":":
        return None
    head, tail = value[:2], value[3:]
    if not (head.isdigit() and tail.isdigit()):
        return None
    hour, minute = int(head), int(tail)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


__all__ = [
    "NotificationGate",
    "NotificationGateConfig",
    "V1MatrixReader",
]
