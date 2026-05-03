"""Shared validation utilities for Pydantic boundary normalizers.

Every subsystem that defines Pydantic contracts (tools, MCP, skills,
persistence, memory, subagents, streaming, runtime API) shares the same
handful of normalization primitives.  This module is the single source of
truth so those subsystems can delegate rather than duplicate.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_runtime.execution.contracts import AgentRuntimeContext

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*(?::[a-z0-9][a-z0-9_.-]*)*$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ValueNormalizer:
    """Canonical normalization helpers shared across Pydantic boundaries."""

    @classmethod
    def normalize_nonempty_string(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be empty")
        return normalized

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if not _ID_PATTERN.fullmatch(normalized):
            raise ValueError(f"{field_name} contains unsupported characters")
        return normalized

    @classmethod
    def normalize_optional_id(cls, value: object, field_name: str) -> str | None:
        if value is None:
            return None
        return cls.normalize_id(value, field_name)

    @classmethod
    def normalize_slug(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not _SLUG_PATTERN.fullmatch(normalized):
            raise ValueError(f"{field_name} must be a stable slug")
        return normalized

    @classmethod
    def normalize_optional_text(cls, value: object, field_name: str) -> str | None:
        if value is None:
            return None
        return cls.normalize_nonempty_string(value, field_name)

    @classmethod
    def normalize_sha256(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError(f"{field_name} must be a valid SHA-256 hash")
        return normalized

    @classmethod
    def normalize_scope(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not _SCOPE_PATTERN.fullmatch(normalized):
            raise ValueError(f"{field_name} must contain explicit permission scopes")
        return normalized

    @classmethod
    def normalize_slug_set(cls, value: object, field_name: str) -> frozenset[str]:
        values = cls.coerce_iterable(value, field_name)
        return frozenset(cls.normalize_slug(item, field_name) for item in values)

    @classmethod
    def normalize_scope_set(cls, value: object, field_name: str) -> frozenset[str]:
        values = cls.coerce_iterable(value, field_name)
        return frozenset(cls.normalize_scope(item, field_name) for item in values)

    @classmethod
    def normalize_id_set(cls, value: object, field_name: str) -> frozenset[str]:
        values = cls.coerce_iterable(value, field_name)
        return frozenset(cls.normalize_id(item, field_name) for item in values)

    @classmethod
    def coerce_iterable(cls, value: object, field_name: str) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError(f"{field_name} must be an iterable, not a string")
        if not isinstance(value, Iterable):
            raise ValueError(f"{field_name} must be an iterable")
        return tuple(value)

    @classmethod
    def redact_json_object(cls, value: object) -> dict:
        from agent_runtime.observability.redaction import ObservabilityRedactor

        return ObservabilityRedactor.redact_json_object(value)  # type: ignore[return-value]

    @staticmethod
    def first_duplicate_name(names: Iterable[str]) -> str | None:
        """Return the first alphabetically-sorted duplicate, or ``None``."""
        counts = Counter(names)
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        return duplicates[0] if duplicates else None

    @staticmethod
    def coerce_runtime_context(
        context: object,
        *,
        correlation_id: str | None = None,
    ) -> "AgentRuntimeContext":
        """Validate and return an ``AgentRuntimeContext``, raising on failure."""
        from agent_runtime.execution.contracts import (
            AgentRuntimeContext,
            RuntimeErrorCode,
        )
        from agent_runtime.execution.errors import AgentRuntimeError

        if isinstance(context, AgentRuntimeContext):
            return context
        try:
            from pydantic import ValidationError as _ValidationError

            return AgentRuntimeContext.model_validate(context)
        except _ValidationError as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context is invalid.",
                retryable=False,
                correlation_id=correlation_id,
            ) from exc
