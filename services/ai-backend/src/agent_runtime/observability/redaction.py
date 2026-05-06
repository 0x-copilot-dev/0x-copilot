"""Payload redaction helpers for stream and observation surfaces."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_runtime.observability.constants import Defaults, Patterns, UserContentKeys

# Known-safe integer/count keys whose names happen to contain the
# substring ``token`` (the redactor's sensitive-key regex matches
# anywhere in the key name). These carry observability counters — never
# credentials — so we surface their numeric values to clients rather
# than redacting them.
_TOKEN_COUNT_KEYS = frozenset(
    {
        "before_tokens",
        "after_tokens",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "total_tokens",
        "context_tokens",
        "max_input_tokens",
        "max_output_tokens",
    }
)


class ObservabilityRedactor:
    """Redact secrets and shrink oversized payloads before stream emission.

    User-visible content (model output, tool output, reasoning summaries,
    composer drafts — see ``UserContentKeys.KEYS``) is treated specially:
    the ``SENSITIVE_VALUE`` regex is **not** applied to its string leaves,
    because that regex is heuristic and over-fires on any prose or code
    that happens to contain ``password = …``, ``token: …`` and similar
    patterns. Destroying the entire streamed assistant message because
    the model wrote one illustrative ``api_key = "..."`` line is worse
    than the leak risk it tries to mitigate.

    The structural ``SENSITIVE_KEY`` scrub still applies everywhere — a
    dict literally keyed ``"password"`` is dropped even inside user
    content, because that's the real attack pattern (a tool emitting
    ``{"password": "hunter2"}``). The user-content flag also propagates
    through nested structures: anything reached via a user-content key
    stays in user-content territory unless an inner key flips it.
    """

    @classmethod
    def redact_json_object(
        cls,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> dict[str, object]:
        """Return a JSON-compatible mapping with sensitive values removed."""

        if value is None:
            return {}
        if not isinstance(value, Mapping):
            return {
                "value": cls.redact_json_value(
                    value,
                    max_string_length=max_string_length,
                    user_content=user_content,
                )
            }
        return {
            str(key): cls._redact_key_value(
                str(key),
                item,
                max_string_length=max_string_length,
                user_content=user_content,
            )
            for key, item in value.items()
        }

    @classmethod
    def redact_json_value(
        cls,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> object:
        """Return a redacted JSON scalar, list, or object."""

        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return cls._redact_string(
                value,
                max_string_length=max_string_length,
                user_content=user_content,
            )
        if isinstance(value, Mapping):
            return cls.redact_json_object(
                value,
                max_string_length=max_string_length,
                user_content=user_content,
            )
        if isinstance(value, Iterable):
            return [
                cls.redact_json_value(
                    item,
                    max_string_length=max_string_length,
                    user_content=user_content,
                )
                for item in value
            ]
        return cls._redact_string(
            str(value),
            max_string_length=max_string_length,
            user_content=user_content,
        )

    @classmethod
    def _redact_key_value(
        cls,
        key: str,
        value: object,
        *,
        max_string_length: int | None,
        user_content: bool,
    ) -> object:
        if key in _TOKEN_COUNT_KEYS:
            return cls.redact_json_value(
                value,
                max_string_length=max_string_length,
                user_content=user_content,
            )
        if Patterns.SENSITIVE_KEY.search(key):
            return Defaults.REDACTED
        if key in UserContentKeys.KEYS:
            # Entering user-content territory: drop the length cap and
            # tell descendants to skip the SENSITIVE_VALUE regex on
            # string leaves. SENSITIVE_KEY still fires on nested keys
            # (handled in this method on each recursion).
            return cls.redact_json_value(
                value,
                max_string_length=None,
                user_content=True,
            )
        return cls.redact_json_value(
            value,
            max_string_length=max_string_length,
            user_content=user_content,
        )

    @classmethod
    def _redact_string(
        cls,
        value: str,
        *,
        max_string_length: int | None,
        user_content: bool,
    ) -> str:
        if not user_content and Patterns.SENSITIVE_VALUE.search(value):
            return Defaults.REDACTED
        if max_string_length is None or len(value) <= max_string_length:
            return value
        return f"{value[:max_string_length]}{Defaults.TRUNCATED}"
