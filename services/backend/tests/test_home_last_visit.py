"""Tests for ``home.last_visit.read_and_advance_last_visit``.

The cutoff lives inside ``user_preferences.preferences['home']
['last_visit_iso']`` until the ``users.home_last_visit_at`` column
migration lands. Tests cover:

* First visit returns ``now - 24h`` and persists ``now``.
* Subsequent visits observe the previous cutoff.
* The atomic UPSERT preserves co-located ``home.*`` keys (so the
  Phase 2 ``activity_window_hours`` pref survives).
* Store errors fall back silently — the briefing must render.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend_app.home.last_visit import read_and_advance_last_visit
from backend_app.identity.me_store import (
    InMemoryMeStore,
    UserPreferencesRecord,
)


_ORG = "org_acme"
_USR = "usr_sarah"
_NOW = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)


class TestFirstVisit:
    def test_returns_24h_lookback(self) -> None:
        me = InMemoryMeStore()
        previous = read_and_advance_last_visit(
            me_store=me, org_id=_ORG, user_id=_USR, now=_NOW
        )
        assert previous == (_NOW - timedelta(hours=24)).isoformat()

    def test_advances_cutoff(self) -> None:
        me = InMemoryMeStore()
        read_and_advance_last_visit(me_store=me, org_id=_ORG, user_id=_USR, now=_NOW)
        # Second call now observes the advanced cutoff.
        previous = read_and_advance_last_visit(
            me_store=me,
            org_id=_ORG,
            user_id=_USR,
            now=_NOW + timedelta(hours=1),
        )
        assert previous == _NOW.isoformat()


class TestPreserveCoLocatedKeys:
    def test_preserves_activity_window_hours(self) -> None:
        """The Phase 2 ``home.activity_window_hours`` pref must survive
        the visit-cutoff UPSERT (both live under ``preferences.home``)."""

        me = InMemoryMeStore()
        me.upsert_preferences(
            UserPreferencesRecord(
                org_id=_ORG,
                user_id=_USR,
                preferences={"home": {"activity_window_hours": 48}},
            )
        )
        read_and_advance_last_visit(me_store=me, org_id=_ORG, user_id=_USR, now=_NOW)
        record = me.get_preferences(org_id=_ORG, user_id=_USR)
        assert record is not None
        assert record.preferences["home"]["activity_window_hours"] == 48
        assert record.preferences["home"]["last_visit_iso"] == _NOW.isoformat()

    def test_preserves_other_pref_groups(self) -> None:
        me = InMemoryMeStore()
        me.upsert_preferences(
            UserPreferencesRecord(
                org_id=_ORG,
                user_id=_USR,
                preferences={"theme": {"mode": "dark"}},
            )
        )
        read_and_advance_last_visit(me_store=me, org_id=_ORG, user_id=_USR, now=_NOW)
        record = me.get_preferences(org_id=_ORG, user_id=_USR)
        assert record is not None
        assert record.preferences["theme"]["mode"] == "dark"


class TestStoreFailureSafety:
    def test_get_failure_returns_safe_fallback(self) -> None:
        class _BrokenStore(InMemoryMeStore):
            def get_preferences(self, **_: object) -> UserPreferencesRecord | None:
                raise RuntimeError("boom")

        previous = read_and_advance_last_visit(
            me_store=_BrokenStore(),
            org_id=_ORG,
            user_id=_USR,
            now=_NOW,
        )
        assert previous == (_NOW - timedelta(hours=24)).isoformat()

    @pytest.mark.parametrize("upsert_failure", [True])
    def test_upsert_failure_does_not_raise(self, upsert_failure: bool) -> None:
        del upsert_failure

        class _BrokenUpsert(InMemoryMeStore):
            def upsert_preferences(
                self, *_: object, **__: object
            ) -> UserPreferencesRecord:
                raise RuntimeError("boom")

        # Must not propagate; returns the safe fallback.
        previous = read_and_advance_last_visit(
            me_store=_BrokenUpsert(),
            org_id=_ORG,
            user_id=_USR,
            now=_NOW,
        )
        assert previous == (_NOW - timedelta(hours=24)).isoformat()


class TestNaiveNowCoercion:
    def test_naive_datetime_treated_as_utc(self) -> None:
        me = InMemoryMeStore()
        naive = datetime(2026, 5, 18, 9, 0)  # tzinfo absent
        previous = read_and_advance_last_visit(
            me_store=me, org_id=_ORG, user_id=_USR, now=naive
        )
        # 24h lookback in UTC.
        assert previous.endswith("+00:00")
