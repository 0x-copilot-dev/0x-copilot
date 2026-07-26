"""Server-derived trust clauses on the connector consent card."""

from __future__ import annotations

import pytest

from agent_runtime.api.connector_trust import ConnectorTrustLine


class TestAuthHost:
    @pytest.mark.parametrize(
        ("auth_url", "expected"),
        [
            ("https://linear.app/oauth/authorize?client_id=x", "linear.app"),
            ("https://www.notion.so/install", "notion.so"),
            ("https://accounts.google.com/o/oauth2/v2/auth", "accounts.google.com"),
            ("http://localhost:8100/oauth/start", "localhost"),
        ],
    )
    def test_names_the_host_the_redirect_actually_opens(
        self, auth_url: str, expected: str
    ) -> None:
        assert ConnectorTrustLine.auth_host(auth_url) == expected

    @pytest.mark.parametrize(
        "auth_url",
        ["", "not a url", "http://[unclosed", "mailto:someone@example.com"],
    )
    def test_unusable_url_yields_no_promise(self, auth_url: str) -> None:
        # The card must drop the "OAuth on <host>" clause rather than guess a
        # host from the connector slug — a promise about where the user will
        # type their password has to be backed by the issued redirect.
        assert ConnectorTrustLine.auth_host(auth_url) == ""
