"""PR 8.0.5 §2.6 — notification gate v1/v2 cutover + quiet hours."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

import pytest

from backend_app.notifications.dispatcher_gate import (
    NotificationGate,
    NotificationGateConfig,
)
from backend_app.notifications.store import (
    InMemoryNotificationPrefsStore,
    NotificationChannel,
    NotificationEventKind,
    NotificationPreferenceRow,
    NotificationQuietHoursRow,
)


class _StaticV1Reader:
    """Dict-backed ``V1MatrixReader`` for tests."""

    def __init__(self, matrix: Mapping[str, Mapping[str, bool]]) -> None:
        self._matrix = matrix

    def read_v1_matrix(self, *, user_id: str) -> Mapping[str, Mapping[str, bool]]:
        del user_id
        return self._matrix


def _gate(
    *,
    use_v2: bool = False,
    matrix: Mapping[str, Mapping[str, bool]] | None = None,
    v2_store: InMemoryNotificationPrefsStore | None = None,
    clock: "callable | None" = None,  # noqa: UP037
) -> NotificationGate:
    return NotificationGate(
        v1_reader=_StaticV1Reader(matrix or {}),
        v2_store=v2_store or InMemoryNotificationPrefsStore(),
        config=NotificationGateConfig(version="v2" if use_v2 else "v1", use_v2=use_v2),
        clock=clock,
    )


class TestV1ReadPath:
    def test_v1_off_blocks(self) -> None:
        gate = _gate(matrix={"mention": {"email": False, "desktop": True}})
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.EMAIL,
            )
            is False
        )

    def test_v1_on_allows(self) -> None:
        gate = _gate(matrix={"mention": {"email": True}})
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.EMAIL,
            )
            is True
        )

    def test_v1_unknown_v2_event_blocks(self) -> None:
        # connector_error didn't exist in v1; the gate plays safe and
        # blocks until the operator flips to v2.
        gate = _gate(matrix={})
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.CONNECTOR_ERROR,
                channel=NotificationChannel.EMAIL,
            )
            is False
        )

    def test_v1_push_channel_blocks(self) -> None:
        gate = _gate(matrix={"mention": {"email": True}})
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.PUSH,
            )
            is False
        )


class TestV2ReadPath:
    def test_stored_cell_overrides_default(self) -> None:
        store = InMemoryNotificationPrefsStore()
        store.upsert_preference(
            NotificationPreferenceRow(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.IN_APP,
                enabled=False,
            )
        )
        gate = _gate(use_v2=True, v2_store=store)
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.IN_APP,
            )
            is False
        )

    def test_absent_cell_uses_deployment_default(self) -> None:
        gate = _gate(use_v2=True)
        # Deployment default: mention × in_app = on.
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.IN_APP,
            )
            is True
        )
        # Push defaults off across the board.
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.PUSH,
            )
            is False
        )


class TestQuietHours:
    def _store_with_quiet(
        self, *, from_local: str, to_local: str, tz: str
    ) -> InMemoryNotificationPrefsStore:
        store = InMemoryNotificationPrefsStore()
        store.upsert_quiet_hours(
            NotificationQuietHoursRow(
                user_id="usr_a",
                enabled=True,
                from_local=from_local,
                to_local=to_local,
                tz=tz,
            )
        )
        return store

    def test_quiet_window_suppresses_non_critical(self) -> None:
        store = self._store_with_quiet(from_local="20:00", to_local="08:00", tz="UTC")

        # 02:00 UTC — inside the overnight window.
        def clock() -> datetime:
            return datetime(2026, 5, 6, 2, 0, tzinfo=timezone.utc)

        gate = _gate(use_v2=True, v2_store=store, clock=clock)
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.IN_APP,
            )
            is False
        )

    def test_approval_requested_breaks_through_quiet(self) -> None:
        store = self._store_with_quiet(from_local="20:00", to_local="08:00", tz="UTC")

        def clock() -> datetime:
            return datetime(2026, 5, 6, 2, 0, tzinfo=timezone.utc)

        gate = _gate(use_v2=True, v2_store=store, clock=clock)
        # Approval requests are critical-by-default; quiet hours never
        # gate them.
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.APPROVAL_REQUESTED,
                channel=NotificationChannel.EMAIL,
            )
            is True
        )

    def test_outside_quiet_window_allows(self) -> None:
        store = self._store_with_quiet(from_local="20:00", to_local="08:00", tz="UTC")

        def clock() -> datetime:
            return datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)

        gate = _gate(use_v2=True, v2_store=store, clock=clock)
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.IN_APP,
            )
            is True
        )

    def test_disabled_quiet_hours_dont_gate(self) -> None:
        store = InMemoryNotificationPrefsStore()
        store.upsert_quiet_hours(
            NotificationQuietHoursRow(
                user_id="usr_a",
                enabled=False,
                from_local="20:00",
                to_local="08:00",
                tz="UTC",
            )
        )

        def clock() -> datetime:
            return datetime(2026, 5, 6, 2, 0, tzinfo=timezone.utc)

        gate = _gate(use_v2=True, v2_store=store, clock=clock)
        assert (
            gate.should_notify(
                user_id="usr_a",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.IN_APP,
            )
            is True
        )


class TestEnvFlag:
    def test_env_default_is_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BACKEND_NOTIFICATION_DISPATCHER_VERSION", raising=False)
        config = NotificationGateConfig.from_env()
        assert config.use_v2 is False

    def test_env_v2_flips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_NOTIFICATION_DISPATCHER_VERSION", "v2")
        config = NotificationGateConfig.from_env()
        assert config.use_v2 is True
