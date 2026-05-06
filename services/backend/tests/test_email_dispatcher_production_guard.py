"""Production fail-closed guard for the magic-link email dispatcher.

In production, ``LoggingEmailDispatcher`` would silently drop magic-link
emails (write the URL to logs and never send mail). The guard in
``backend_app.app._assert_email_dispatcher_safe_for_environment`` refuses
to start with that adapter when ``BACKEND_ENVIRONMENT=production`` and
magic-link is enabled. Operators must inject a real adapter
(SES/SMTP/Postmark).

We test the guard directly (it's the smallest surface) plus one
integration smoke against ``create_app`` in dev mode to confirm the
fallback is wired and the logger emits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from backend_app.app import (
    _assert_email_dispatcher_safe_for_environment,
    create_app,
)
from backend_app.identity.email_dispatcher import (
    EmailDispatcherPort,
    LoggingEmailDispatcher,
)


@dataclass
class _RealEmailDispatcher:
    """Stand-in for an SES/SMTP/Postmark adapter in tests."""

    sent: list[dict[str, object]]

    def send_magic_link(
        self,
        *,
        to_email: str,
        org_display_name: str | None,
        login_url: str,
        expires_minutes: int,
        request_ip: str | None,
        request_user_agent: str | None,
    ) -> None:
        self.sent.append(
            {
                "to_email": to_email,
                "org_display_name": org_display_name,
                "login_url": login_url,
                "expires_minutes": expires_minutes,
            }
        )


def test_real_dispatcher_satisfies_port() -> None:
    """Sanity: the test stand-in actually conforms to the port."""
    real: EmailDispatcherPort = _RealEmailDispatcher(sent=[])
    real.send_magic_link(
        to_email="x@y.com",
        org_display_name=None,
        login_url="https://example.com/x",
        expires_minutes=15,
        request_ip=None,
        request_user_agent=None,
    )


class TestAssertEmailDispatcherSafe:
    def test_production_with_logging_fallback_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        dispatcher = LoggingEmailDispatcher(logger=logging.getLogger("t"))

        with pytest.raises(RuntimeError) as ei:
            _assert_email_dispatcher_safe_for_environment(
                dispatcher, magic_link_enabled=True
            )

        message = str(ei.value)
        assert "BACKEND_ENVIRONMENT=production" in message
        assert "magic-link" in message
        # Operator hint that a real adapter is needed.
        assert "SES" in message or "SMTP" in message

    def test_production_with_real_dispatcher_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        real = _RealEmailDispatcher(sent=[])

        # Does not raise.
        _assert_email_dispatcher_safe_for_environment(real, magic_link_enabled=True)

    def test_production_with_magic_link_disabled_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bank / strict-SSO deploys turn magic-link off entirely. The guard
        # must not fire — there's no email to send.
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        dispatcher = LoggingEmailDispatcher(logger=logging.getLogger("t"))

        _assert_email_dispatcher_safe_for_environment(
            dispatcher, magic_link_enabled=False
        )

    def test_development_default_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BACKEND_ENVIRONMENT", raising=False)
        dispatcher = LoggingEmailDispatcher(logger=logging.getLogger("t"))

        _assert_email_dispatcher_safe_for_environment(
            dispatcher, magic_link_enabled=True
        )

    def test_explicit_development_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        dispatcher = LoggingEmailDispatcher(logger=logging.getLogger("t"))

        _assert_email_dispatcher_safe_for_environment(
            dispatcher, magic_link_enabled=True
        )

    def test_uppercase_production_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Whitespace + mixed case in env vars is the kind of thing operators
        # accidentally introduce; the guard normalizes both.
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "  PRODUCTION  ")
        dispatcher = LoggingEmailDispatcher(logger=logging.getLogger("t"))

        with pytest.raises(RuntimeError):
            _assert_email_dispatcher_safe_for_environment(
                dispatcher, magic_link_enabled=True
            )


class TestCreateAppIntegration:
    def test_development_default_uses_logging_dispatcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Smoke: confirm the wire-up path actually attaches a dispatcher
        # to ``app.state`` in dev. Production-mode integration is covered
        # by the unit tests above + the deployment profile loader's
        # existing fail-closed tests.
        monkeypatch.delenv("BACKEND_ENVIRONMENT", raising=False)
        monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "x" * 64)

        app = create_app(magic_link_globally_enabled=True)

        assert isinstance(app.state.email_dispatcher, LoggingEmailDispatcher)


class TestLoggingEmailDispatcherWritesStructured:
    def test_logging_dispatcher_emits_dispatch_record(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Belt-and-braces: the fallback never raises and writes the
        # ``magic_link.dispatch`` record. If this log line goes missing
        # in dev, the guard's value collapses (the visible signal that an
        # adapter is needed).
        dispatcher = LoggingEmailDispatcher(logger=logging.getLogger("test"))

        with caplog.at_level(logging.INFO, logger="test"):
            dispatcher.send_magic_link(
                to_email="user@example.com",
                org_display_name="Acme",
                login_url="https://app.example.com/auth/magic-link/callback?token=tkn",
                expires_minutes=15,
                request_ip="127.0.0.1",
                request_user_agent="pytest",
            )

        records = [r for r in caplog.records if r.message == "magic_link.dispatch"]
        assert len(records) == 1
