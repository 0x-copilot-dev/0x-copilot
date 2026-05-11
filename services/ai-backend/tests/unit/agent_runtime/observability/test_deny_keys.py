"""Tests for the P11.2 exact-match deny set semantics.

After P11.2 the redactor matches dict keys against a closed
:data:`DENY_KEYS` ``frozenset`` rather than the prior
``Patterns.SENSITIVE_KEY`` regex substring search. These tests pin the
behavior contract:

- All names in the deny set scrub their value to ``[redacted]``.
- Substring matches no longer fire (``tokenizer``, ``input_tokens``,
  ``my_password_field`` pass through).
- Lookups are case-sensitive (see PRD §5.3 for the rationale).
- The deny-set membership is closed and inspectable.
"""

from __future__ import annotations

from agent_runtime.observability.constants import Defaults
from agent_runtime.observability.redaction import ObservabilityRedactor
from agent_runtime.observability.redactor import DENY_KEYS


class _DenyKeyConstants:
    """Frozen expected contents for ``DENY_KEYS`` so test edits to the
    set are forced through this fixture (and through PRD §4)."""

    EXPECTED: frozenset[str] = frozenset(
        {
            "password",
            "passwd",
            "secret",
            "credential",
            "credentials",
            "api_key",
            "apikey",
            "api-key",
            "authorization",
            "auth_token",
            "access_token",
            "refresh_token",
            "private_key",
            "client_secret",
            "token",
        }
    )


class TestDenyKeyMembership:
    """The set itself — locked down so additions go through PRD review."""

    def test_deny_keys_membership_is_exactly_the_documented_set(self) -> None:
        assert DENY_KEYS == _DenyKeyConstants.EXPECTED

    def test_deny_keys_is_frozen(self) -> None:
        assert isinstance(DENY_KEYS, frozenset)


class TestExactMatchScrubbing:
    """Keys explicitly in the deny set scrub their value."""

    def test_password_key_is_redacted(self) -> None:
        result = ObservabilityRedactor.redact_json_object({"password": "hunter2"})

        assert result["password"] == Defaults.REDACTED

    def test_api_key_variants_are_all_redacted(self) -> None:
        for variant in ("api_key", "apikey", "api-key"):
            result = ObservabilityRedactor.redact_json_object({variant: "sk-secret"})
            assert result[variant] == Defaults.REDACTED, variant

    def test_authorization_header_value_is_redacted(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"authorization": "Bearer eyJhbGciOi..."}
        )

        assert result["authorization"] == Defaults.REDACTED

    def test_private_key_and_client_secret_are_redacted(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"private_key": "-----BEGIN", "client_secret": "abc"}
        )

        assert result["private_key"] == Defaults.REDACTED
        assert result["client_secret"] == Defaults.REDACTED


class TestSubstringMatchesNoLongerFire:
    """The P11.2 fix: substring search is gone."""

    def test_input_tokens_passes_through_as_integer(self) -> None:
        # Pre-P11.2 this was an explicit ``_TOKEN_COUNT_KEYS`` allowlist
        # carve-out because the substring ``token`` matched the
        # SENSITIVE_KEY regex. Exact match doesn't need the workaround.
        result = ObservabilityRedactor.redact_json_object({"input_tokens": 42})

        assert result["input_tokens"] == 42

    def test_observability_counter_keys_all_pass_through(self) -> None:
        counters = {
            "before_tokens": 1,
            "after_tokens": 2,
            "input_tokens": 3,
            "output_tokens": 4,
            "cached_input_tokens": 5,
            "reasoning_tokens": 6,
            "total_tokens": 7,
            "context_tokens": 8,
            "max_input_tokens": 9,
            "max_output_tokens": 10,
        }

        result = ObservabilityRedactor.redact_json_object(counters)

        assert result == counters

    def test_tokenizer_name_passes_through(self) -> None:
        result = ObservabilityRedactor.redact_json_object({"tokenizer": "claude"})

        assert result["tokenizer"] == "claude"

    def test_my_password_field_passes_through(self) -> None:
        # ``my_password_field`` contains ``password`` as a substring
        # but does not equal it — exact match means the value passes
        # through. Genuinely credential-carrying field names should be
        # added to ``DENY_KEYS`` explicitly during code review.
        result = ObservabilityRedactor.redact_json_object({"my_password_field": "x"})

        assert result["my_password_field"] == "x"

    def test_account_authorization_id_passes_through(self) -> None:
        # ``authorization`` matches; ``account_authorization_id`` does
        # not (substring match retired).
        result = ObservabilityRedactor.redact_json_object(
            {"account_authorization_id": "acct_123"}
        )

        assert result["account_authorization_id"] == "acct_123"


class TestCaseSensitivity:
    """Lookup is case-sensitive per PRD §5.3."""

    def test_capitalised_password_passes_through(self) -> None:
        # PRD §5.3 (Option A): exact case match. All production payloads
        # use ASCII lowercase snake_case keys; the case-sensitive
        # tightening is acceptable. Add ``.lower()`` upstream if a real
        # external case-mismatch shows up.
        result = ObservabilityRedactor.redact_json_object({"Password": "x"})

        assert result["Password"] == "x"


class TestValueScanningRemoved:
    """P11.2 also removed the SENSITIVE_VALUE regex entirely."""

    def test_credential_shaped_string_value_passes_through(self) -> None:
        # Pre-P11.2 the redactor scanned every string leaf for
        # ``api_key=...`` shapes and replaced the whole string with
        # ``[redacted]``. That over-fired on prose. P11.2 removed
        # value scanning entirely.
        result = ObservabilityRedactor.redact_json_object(
            {"description": "Set api_key=sk-1234 in your env"}
        )

        assert result["description"] == "Set api_key=sk-1234 in your env"

    def test_password_equals_value_in_free_text_passes_through(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"note": "the password = hunter2 was set"}
        )

        assert result["note"] == "the password = hunter2 was set"


class TestKeyScrubInsideUserContent:
    """User-content carve-out drops the length cap but does not bypass
    the structural deny-key scrub — a tool emitting
    ``{"args": {"password": "..."}}`` still has the credential dropped."""

    def test_password_key_inside_args_is_still_redacted(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"args": {"password": "hunter2", "visible": "value"}}
        )

        assert result["args"]["password"] == Defaults.REDACTED
        assert result["args"]["visible"] == "value"
