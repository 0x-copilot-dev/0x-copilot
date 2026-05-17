"""Tests for the Home section composers (Phase 2).

Greeting is the only real composer in this PR — the rest are stubs
returning `SectionResult{status: "ok", data: []}` (with one
`unavailable` for upcoming_meetings). The stub tests live in
``test_home_routes.py`` (where the integration is asserted); this
file focuses on the greeting fallback chain and time-segment buckets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from types import SimpleNamespace

from backend_app.home.service import (
    compose_activity_stub,
    compose_favorite_tools_stub,
    compose_greeting,
    compose_pinned_chats_stub,
    compose_recent_runs_stub,
    compose_todays_focus_stub,
    compose_upcoming_meetings_stub,
)


def _user(
    *,
    display_name: str | None = "Sarah Chen",
    metadata: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(display_name=display_name, metadata=metadata or {})


class TestComposeGreeting:
    """The fallback chain: IdP given_name → display_name first-token → None."""

    def test_idp_given_name_wins(self) -> None:
        user = _user(display_name="Sarah Chen", metadata={"given_name": "Sara"})
        greeting = compose_greeting(
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            user=user,
        )
        # IdP given_name is preferred even when display_name first-token
        # would yield a different value (Sara, not Sarah).
        assert greeting["display_name"] == "Sara"

    def test_falls_back_to_display_name_first_token(self) -> None:
        user = _user(display_name="Sarah Chen", metadata={})
        greeting = compose_greeting(
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            user=user,
        )
        assert greeting["display_name"] == "Sarah"

    def test_returns_none_when_no_signal_available(self) -> None:
        """Service account / SCIM-imported user with neither IdP claims
        nor a display_name → null so the FE renders 'Good morning.'."""

        user = _user(display_name=None, metadata={})
        greeting = compose_greeting(
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            user=user,
        )
        assert greeting["display_name"] is None

    def test_blank_idp_given_name_falls_through(self) -> None:
        """Whitespace-only IdP claim → fall through to display_name."""

        user = _user(display_name="Sarah Chen", metadata={"given_name": "   "})
        greeting = compose_greeting(
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            user=user,
        )
        assert greeting["display_name"] == "Sarah"

    def test_blank_display_name_falls_through_to_none(self) -> None:
        user = _user(display_name="   ", metadata={})
        greeting = compose_greeting(
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            user=user,
        )
        assert greeting["display_name"] is None

    def test_never_uses_email_local_part(self) -> None:
        """The fallback chain must NOT reach into ``primary_email``.

        Greeting leaks signal when it does (job titles, role names,
        internal usernames). cross-audit §9.5 explicitly forbids it.
        """

        user = SimpleNamespace(
            display_name=None,
            metadata={},
            primary_email="sarah.chen+marketing@acme.com",
        )
        greeting = compose_greeting(
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            user=user,
        )
        assert greeting["display_name"] is None


class TestTimeSegment:
    """Three buckets: morning (<12), afternoon (12-16), evening (17+)."""

    def test_morning_before_noon(self) -> None:
        user = _user()
        for hour in (0, 6, 9, 11):
            greeting = compose_greeting(
                now=datetime(2026, 5, 18, hour, 0, tzinfo=timezone.utc),
                user=user,
            )
            assert greeting["time_segment"] == "morning", f"hour={hour}"

    def test_afternoon_noon_to_five(self) -> None:
        user = _user()
        for hour in (12, 13, 15, 16):
            greeting = compose_greeting(
                now=datetime(2026, 5, 18, hour, 0, tzinfo=timezone.utc),
                user=user,
            )
            assert greeting["time_segment"] == "afternoon", f"hour={hour}"

    def test_evening_after_five(self) -> None:
        user = _user()
        for hour in (17, 19, 22, 23):
            greeting = compose_greeting(
                now=datetime(2026, 5, 18, hour, 0, tzinfo=timezone.utc),
                user=user,
            )
            assert greeting["time_segment"] == "evening", f"hour={hour}"


class TestStubComposers:
    """Every stub returns a SectionResult-shaped dict."""

    def test_activity_stub(self) -> None:
        assert compose_activity_stub() == {"status": "ok", "data": []}

    def test_pinned_chats_stub(self) -> None:
        assert compose_pinned_chats_stub() == {"status": "ok", "data": []}

    def test_recent_runs_stub(self) -> None:
        assert compose_recent_runs_stub() == {"status": "ok", "data": []}

    def test_favorite_tools_stub(self) -> None:
        assert compose_favorite_tools_stub() == {"status": "ok", "data": []}

    def test_todays_focus_stub(self) -> None:
        assert compose_todays_focus_stub() == {"status": "ok", "data": []}

    def test_upcoming_meetings_stub_is_unavailable(self) -> None:
        # Drives the FE 'Connect a calendar' CTA — code is stable.
        result = compose_upcoming_meetings_stub()
        assert result["status"] == "unavailable"
        assert result["error"] == "no_calendar_connector"
