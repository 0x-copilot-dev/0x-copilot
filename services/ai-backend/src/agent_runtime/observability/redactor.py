"""Redactor Protocol and default ``RegexRedactor`` implementation.

This module owns the swappable redaction contract. Callers should resolve
the active implementation via :class:`RedactorRegistry`; the legacy
:class:`agent_runtime.observability.redaction.ObservabilityRedactor`
classmethod surface delegates here so existing call sites keep working
while later phases (P11.2 / P11.3 / P11.6) swap in library-backed engines.

The ``Redactor`` Protocol is runtime-checkable: any object that exposes
``redact_json_object`` and ``redact_json_value`` satisfies it. New
engines (detect-secrets / Presidio backed) only need to implement those
two methods to slot into :meth:`RedactorRegistry.set_default`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import ClassVar, Protocol, runtime_checkable

from agent_runtime.observability.constants import Defaults, UserContentKeys


# Exact-match deny set for dict keys carrying credentials in free-form
# ``metadata`` payloads. P11.2 replaced the prior substring-regex match
# (``Patterns.SENSITIVE_KEY.search(key)``) with this closed set; the
# regex over-matched on observability counters like ``input_tokens``
# (any name containing the substring ``token``) and required a hand-
# maintained ``_TOKEN_COUNT_KEYS`` allowlist. Exact match doesn't need
# the workaround.
#
# Add a key here only when a new credential-shaped field name lands in
# code review. The set is intentionally small and stable — pattern-
# matching against value contents lives nowhere in this codebase.
DENY_KEYS: frozenset[str] = frozenset(
    {
        # Generic credentials
        "password",
        "passwd",
        "secret",
        "credential",
        "credentials",
        # API tokens
        "api_key",
        "apikey",
        "api-key",
        # OAuth / session tokens
        "authorization",
        "auth_token",
        "access_token",
        "refresh_token",
        # Asymmetric crypto material
        "private_key",
        "client_secret",
        # Generic catch-all (exact-match only — substrings don't fire)
        "token",
    }
)


@runtime_checkable
class Redactor(Protocol):
    """Swappable contract for redacting JSON-shaped payloads.

    Implementations must be safe to call from a Pydantic ``mode="before"``
    field validator: no IO, no async, no thread-pool indirection. The
    runtime resolves the active implementation via
    :class:`RedactorRegistry` so any engine that satisfies this Protocol
    can replace :class:`RegexRedactor` without touching call sites.
    """

    def redact_json_object(
        self,
        value: object,
        *,
        max_string_length: int | None = ...,
        user_content: bool = False,
    ) -> dict[str, object]:
        """Return a JSON-compatible mapping with sensitive values removed."""

    def redact_json_value(
        self,
        value: object,
        *,
        max_string_length: int | None = ...,
        user_content: bool = False,
    ) -> object:
        """Return a redacted JSON scalar, list, or object."""


class RegexRedactor:
    """Default :class:`Redactor` implementation backed by the legacy regex.

    The behavior is the verbatim move of the previous
    ``ObservabilityRedactor`` classmethods to instance methods. Future
    sub-PRDs swap this out by registering a different Protocol-satisfying
    instance via :meth:`RedactorRegistry.set_default`.

    User-visible content (model output, tool output, reasoning summaries,
    composer drafts — see ``UserContentKeys.KEYS``) is treated specially:
    the ``SENSITIVE_VALUE`` regex is **not** applied to its string
    leaves, because that regex is heuristic and over-fires on any prose
    or code that happens to contain ``password = …``, ``token: …`` and
    similar patterns. Destroying the entire streamed assistant message
    because the model wrote one illustrative ``api_key = "..."`` line is
    worse than the leak risk it tries to mitigate.

    The structural ``SENSITIVE_KEY`` scrub still applies everywhere — a
    dict literally keyed ``"password"`` is dropped even inside user
    content, because that's the real attack pattern (a tool emitting
    ``{"password": "hunter2"}``). The user-content flag also propagates
    through nested structures: anything reached via a user-content key
    stays in user-content territory unless an inner key flips it.
    """

    def redact_json_object(
        self,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            return {
                "value": self.redact_json_value(
                    value,
                    max_string_length=max_string_length,
                    user_content=user_content,
                )
            }
        return {
            str(key): self._redact_key_value(
                str(key),
                item,
                max_string_length=max_string_length,
                user_content=user_content,
            )
            for key, item in value.items()
        }

    def redact_json_value(
        self,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> object:
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return self._redact_string(
                value,
                max_string_length=max_string_length,
                user_content=user_content,
            )
        if isinstance(value, Mapping):
            return self.redact_json_object(
                value,
                max_string_length=max_string_length,
                user_content=user_content,
            )
        if isinstance(value, Iterable):
            return [
                self.redact_json_value(
                    item,
                    max_string_length=max_string_length,
                    user_content=user_content,
                )
                for item in value
            ]
        return self._redact_string(
            str(value),
            max_string_length=max_string_length,
            user_content=user_content,
        )

    def _redact_key_value(
        self,
        key: str,
        value: object,
        *,
        max_string_length: int | None,
        user_content: bool,
    ) -> object:
        if key in DENY_KEYS:
            return Defaults.REDACTED
        if key in UserContentKeys.KEYS:
            # Entering user-content territory: drop the length cap so
            # full chat replies / tool outputs render unclipped. The
            # exact-match key scrub on ``DENY_KEYS`` still fires for
            # any nested credential-shaped key (handled by this method
            # on each recursion).
            return self.redact_json_value(
                value,
                max_string_length=None,
                user_content=True,
            )
        return self.redact_json_value(
            value,
            max_string_length=max_string_length,
            user_content=user_content,
        )

    def _redact_string(
        self,
        value: str,
        *,
        max_string_length: int | None,
        user_content: bool,
    ) -> str:
        # P11.2 removed value-pattern scrubbing entirely. The redactor
        # no longer scans string contents for credential-shaped
        # substrings — that was a false-positive magnet, and the new
        # direction (parent PRD §8) treats sensitivity as a property
        # of the *field* not the *value*. Only length clipping remains.
        if max_string_length is None or len(value) <= max_string_length:
            return value
        return f"{value[:max_string_length]}{Defaults.TRUNCATED}"


class RedactorRegistry:
    """Process-wide singleton holder for the active :class:`Redactor`.

    Tests should use :meth:`set_default` to swap in a fake and capture
    the prior default for restoration. Production wiring (P11.6) will
    register the library-backed engine at startup; until then the
    default is :class:`RegexRedactor`.
    """

    _DEFAULT: ClassVar[Redactor | None] = None

    @classmethod
    def default(cls) -> Redactor:
        """Return the active default redactor.

        Lazy-initialised so module import order doesn't force a
        ``RegexRedactor`` construction before the registry is used.
        """

        if cls._DEFAULT is None:
            cls._DEFAULT = RegexRedactor()
        return cls._DEFAULT

    @classmethod
    def set_default(cls, redactor: Redactor) -> Redactor:
        """Install a new default redactor and return the prior one.

        Callers (tests in particular) should capture the return value
        and restore it in teardown to keep the swap process-scoped.
        """

        previous = cls.default()
        cls._DEFAULT = redactor
        return previous

    @classmethod
    def reset_for_tests(cls) -> None:
        """Clear the cached default so the next ``default()`` re-creates a
        fresh :class:`RegexRedactor`. Test-only hook; do not call in
        production code paths."""

        cls._DEFAULT = None
