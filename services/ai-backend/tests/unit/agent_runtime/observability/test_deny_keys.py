"""Membership pin for :data:`DENY_KEYS`.

P11.2 introduced the canonical credential-key deny set; P11.6 deleted
the ``ObservabilityRedactor`` facade that previously exercised it from
this test file. P13 step 2 consolidated the two per-model redactors
into one ``MetadataRedactor`` in ``observability.redactor``. The
deny-key SEMANTIC is now tested at the actual consumer boundaries:

- ``test_logging.py::test_runtime_log_event_blocks_sensitive_and_complex_metadata``
  covers ``MetadataRedactor`` on ``RuntimeLogEvent.metadata``.
- ``test_http_logging.py::TestHttpLogEvent::test_drops_sensitive_metadata_keys``
  covers ``MetadataRedactor`` on ``HttpLogEvent.metadata``.
- ``test_context_memory_management.py::test_compression_event_redacts_sensitive_metadata``
  covers ``MemoryRedactor.redact_metadata``.

This file just pins the contents of the set itself so additions go
through code review.
"""

from __future__ import annotations

from agent_runtime.observability.redactor import DENY_KEYS


class _DenyKeyConstants:
    """Frozen expected contents for ``DENY_KEYS``. PRD §4 in
    ``01b-redaction-exact-match-deny-keys.md`` is the source of truth."""

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

    def test_deny_keys_size_is_pinned(self) -> None:
        # New entries should be deliberate. Update the count and PRD §4
        # in lockstep when adding.
        assert len(DENY_KEYS) == 15
