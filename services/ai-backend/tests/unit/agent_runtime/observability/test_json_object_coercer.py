"""Tests for :class:`JsonObjectCoercer` — coercion-only Pydantic helper.

P11.5 split structural coercion from redaction. Non-log callsites
(events, persistence records, runtime context, runs / conversations
schemas) now call ``JsonObjectCoercer.coerce`` instead of
``ObservabilityRedactor.redact_json_object``. The coercer enforces the
``JsonObject`` dict shape without scrubbing keys, scanning values, or
clipping length. Tests pin those behaviors.

Log emitters are NOT covered here — they still run their own deny-key
filter and have separate tests under ``test_logging.py`` /
``test_http_logging.py``.
"""

from __future__ import annotations

from agent_runtime.observability.redactor import JsonObjectCoercer


class TestNoneCoercion:
    def test_none_returns_empty_dict(self) -> None:
        assert JsonObjectCoercer.coerce(None) == {}

    def test_empty_dict_returns_empty_dict(self) -> None:
        assert JsonObjectCoercer.coerce({}) == {}


class TestNonMappingCoercion:
    def test_string_is_wrapped_under_value_key(self) -> None:
        assert JsonObjectCoercer.coerce("hello") == {"value": "hello"}

    def test_integer_is_wrapped(self) -> None:
        assert JsonObjectCoercer.coerce(42) == {"value": 42}

    def test_list_is_wrapped(self) -> None:
        assert JsonObjectCoercer.coerce([1, 2, 3]) == {"value": [1, 2, 3]}


class TestMappingPassThrough:
    def test_simple_dict_passes_through(self) -> None:
        assert JsonObjectCoercer.coerce({"a": 1, "b": "two"}) == {
            "a": 1,
            "b": "two",
        }

    def test_returns_a_new_dict_not_the_input(self) -> None:
        # Coercer returns a fresh dict so callers can safely mutate
        # without affecting the original.
        source = {"a": 1}
        coerced = JsonObjectCoercer.coerce(source)

        assert coerced == source
        assert coerced is not source

    def test_nested_dict_passes_through_at_top_level(self) -> None:
        # Coercion is shallow — only the top-level shape is enforced.
        # Nested structures are not normalized.
        input_dict = {"outer": {"inner": "value"}}

        assert JsonObjectCoercer.coerce(input_dict) == {"outer": {"inner": "value"}}


class TestRedactionIsNotApplied:
    """Critical contract pin: the coercer does NOT scrub credential
    keys, scan values, or clip strings. Pre-P11.5 callers asserting
    on `[redacted]` placeholders rely on this no-op behavior."""

    def test_credential_key_is_not_scrubbed(self) -> None:
        # ``password`` is in the canonical DENY_KEYS set used by log
        # emitters — but the coercer passes it through unchanged.
        # Logs filter on emission; SSE / persistence flow whole.
        assert JsonObjectCoercer.coerce({"password": "hunter2"}) == {
            "password": "hunter2"
        }

    def test_api_key_value_is_not_scrubbed(self) -> None:
        assert JsonObjectCoercer.coerce({"api_key": "sk-1234"}) == {
            "api_key": "sk-1234"
        }

    def test_long_string_is_not_clipped(self) -> None:
        # Pre-P11.5 strings >2000 chars outside user-content keys were
        # truncated. The coercer does no length clipping.
        long_text = "x" * 10_000
        result = JsonObjectCoercer.coerce({"diagnostic_blob": long_text})

        assert result["diagnostic_blob"] == long_text

    def test_nested_credential_keys_are_not_scrubbed(self) -> None:
        # Coercion is shallow; nested dicts pass through whole.
        input_dict = {"args": {"password": "x", "ok": "fine"}}

        assert JsonObjectCoercer.coerce(input_dict) == {
            "args": {"password": "x", "ok": "fine"}
        }

    def test_credential_shaped_string_value_passes_through(self) -> None:
        # Pre-P11.5 the value regex matched ``api_key=...`` shapes
        # and replaced the whole string with [redacted]. The coercer
        # does no value scanning.
        assert JsonObjectCoercer.coerce(
            {"note": "set api_key=sk-1234 in your env"}
        ) == {"note": "set api_key=sk-1234 in your env"}
