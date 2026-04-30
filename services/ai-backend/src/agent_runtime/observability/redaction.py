"""Payload redaction helpers for stream and observation surfaces."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_runtime.observability.constants import Defaults, Patterns


class ObservabilityRedactor:
    """Redact secrets and shrink oversized payloads before stream emission."""

    @classmethod
    def redact_json_object(
        cls,
        value: object,
        *,
        max_string_length: int = Defaults.MAX_STREAM_FIELD_LENGTH,
    ) -> dict[str, object]:
        """Return a JSON-compatible mapping with sensitive values removed."""

        if value is None:
            return {}
        if not isinstance(value, Mapping):
            return {
                "value": cls.redact_json_value(
                    value, max_string_length=max_string_length
                )
            }
        return {
            str(key): cls._redact_key_value(
                str(key),
                item,
                max_string_length=max_string_length,
            )
            for key, item in value.items()
        }

    @classmethod
    def redact_json_value(
        cls,
        value: object,
        *,
        max_string_length: int = Defaults.MAX_STREAM_FIELD_LENGTH,
    ) -> object:
        """Return a redacted JSON scalar, list, or object."""

        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return cls._redact_string(value, max_string_length=max_string_length)
        if isinstance(value, Mapping):
            return cls.redact_json_object(value, max_string_length=max_string_length)
        if isinstance(value, Iterable):
            return [
                cls.redact_json_value(item, max_string_length=max_string_length)
                for item in value
            ]
        return cls._redact_string(str(value), max_string_length=max_string_length)

    @classmethod
    def _redact_key_value(
        cls,
        key: str,
        value: object,
        *,
        max_string_length: int,
    ) -> object:
        if Patterns.SENSITIVE_KEY.search(key):
            return Defaults.REDACTED
        return cls.redact_json_value(value, max_string_length=max_string_length)

    @classmethod
    def _redact_string(cls, value: str, *, max_string_length: int) -> str:
        if Patterns.SENSITIVE_VALUE.search(value):
            return Defaults.REDACTED
        if len(value) <= max_string_length:
            return value
        return f"{value[:max_string_length]}{Defaults.TRUNCATED}"
