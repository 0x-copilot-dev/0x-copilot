"""SCIM filter parser unit tests (A7)."""

from __future__ import annotations

import pytest

from backend_app.identity.scim_filter import (
    ScimFilterError,
    filter_matches,
    parse_filter,
)


class TestParseFilter:
    def test_none_returns_none(self) -> None:
        assert parse_filter(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_filter("") is None
        assert parse_filter("   ") is None

    def test_eq_string(self) -> None:
        node = parse_filter('userName eq "alice@example.com"')
        assert node is not None
        assert filter_matches(node, {"userName": "alice@example.com"})
        assert not filter_matches(node, {"userName": "bob@example.com"})

    def test_eq_string_case_insensitive(self) -> None:
        node = parse_filter('userName eq "ALICE@example.com"')
        assert filter_matches(node, {"userName": "alice@example.com"})

    def test_eq_bool(self) -> None:
        node = parse_filter("active eq true")
        assert filter_matches(node, {"active": True})
        assert not filter_matches(node, {"active": False})

    def test_pr_presence(self) -> None:
        node = parse_filter("displayName pr")
        assert filter_matches(node, {"displayName": "Alice"})
        assert not filter_matches(node, {"displayName": ""})
        assert not filter_matches(node, {"displayName": None})
        assert not filter_matches(node, {})

    def test_and_conjunction(self) -> None:
        node = parse_filter('userName eq "alice@example.com" and active eq true')
        assert filter_matches(node, {"userName": "alice@example.com", "active": True})
        assert not filter_matches(
            node, {"userName": "alice@example.com", "active": False}
        )
        assert not filter_matches(node, {"userName": "bob", "active": True})

    def test_unsupported_operator_raises(self) -> None:
        with pytest.raises(ScimFilterError):
            parse_filter('userName co "alice"')

    def test_unsupported_or_raises(self) -> None:
        with pytest.raises(ScimFilterError):
            parse_filter('userName eq "a" or userName eq "b"')

    def test_unterminated_string_raises(self) -> None:
        with pytest.raises(ScimFilterError):
            parse_filter('userName eq "alice')

    def test_trailing_garbage_raises(self) -> None:
        with pytest.raises(ScimFilterError):
            parse_filter('userName eq "a" foo bar')
