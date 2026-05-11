"""Backwards-compat surface for the redaction subsystem.

The active implementation lives in
:mod:`agent_runtime.observability.redactor` behind the :class:`Redactor`
Protocol. This module preserves the classmethod-only
:class:`ObservabilityRedactor` shape that 19 call sites already depend
on; each classmethod delegates to the swappable
:class:`~agent_runtime.observability.redactor.RedactorRegistry` default.

Phase P11.6 will delete this shim and migrate the call sites to import
the registry directly. Until then, new code should prefer
``RedactorRegistry.default()``.
"""

from __future__ import annotations

from agent_runtime.observability.constants import Defaults
from agent_runtime.observability.redactor import RedactorRegistry


class ObservabilityRedactor:
    """Backwards-compat facade over :class:`RedactorRegistry.default`.

    Existing call sites keep importing this class and calling its
    classmethods. Behavior is byte-identical to the prior
    implementation because the registry default is
    :class:`~agent_runtime.observability.redactor.RegexRedactor`, which
    carries the verbatim regex/structural logic.
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

        return RedactorRegistry.default().redact_json_object(
            value,
            max_string_length=max_string_length,
            user_content=user_content,
        )

    @classmethod
    def redact_json_value(
        cls,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> object:
        """Return a redacted JSON scalar, list, or object."""

        return RedactorRegistry.default().redact_json_value(
            value,
            max_string_length=max_string_length,
            user_content=user_content,
        )
