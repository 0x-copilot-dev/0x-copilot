"""Pydantic models for the tier-2 render-adapter generator capability."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, PositiveInt, ValidationInfo, field_validator

from agent_runtime.execution.contracts import RuntimeContract


SampleStateScalar = str | int | float | bool | None
# ``SampleStateValue`` is structurally recursive (scalar | list[value] |
# dict[str, value]). Pydantic v2 cannot build a core schema from a
# forward-referenced recursive type alias, so the model stores ``object`` and
# ``_SampleStateInspector`` enforces the shape in a ``before`` validator.
# Public consumers narrow via the inspector helpers. We deliberately do not
# use ``Any`` so the model contract still rejects unknown shapes at validate
# time rather than leaking opaque domain state.
SampleStateValue = object


class _Limits:
    """Bounds applied to untrusted codegen inputs."""

    SCHEME_MAX = 64
    FIELD_NAME_MAX = 80
    STRING_VALUE_MAX = 512
    MAX_FIELDS = 32
    MAX_LIST_ITEMS = 32
    MAX_NESTED_DEPTH = 4
    MAX_TOTAL_NODES = 256
    ADAPTER_SOURCE_MAX = 64 * 1024


class _Messages:
    """Safe public messages returned through ``AdapterCodegenError`` and validators."""

    UNKNOWN_LAYOUT = (
        "layout_template must be one of: form, table, kanban, definition-list"
    )
    SCHEME_INVALID = "scheme must be a non-empty slug"
    SAMPLE_STATE_NOT_MAPPING = "sample_state must be a JSON object of field/value pairs"
    SAMPLE_STATE_TOO_DEEP = "sample_state nesting exceeds the supported depth"
    SAMPLE_STATE_TOO_LARGE = "sample_state field/value count exceeds the supported size"
    SAMPLE_STATE_FIELD_NAME = "sample_state field names must be non-empty strings"
    SAMPLE_STATE_VALUE_TYPE = (
        "sample_state values must be JSON-serialisable scalars, lists, or objects"
    )
    SAMPLE_STATE_STRING_TOO_LONG = (
        "sample_state contains a value that exceeds the size limit"
    )


class LayoutTemplate(StrEnum):
    """Constrained set of layouts the generator can emit (Q5 from PRD 9.5.1)."""

    FORM = "form"
    TABLE = "table"
    KANBAN = "kanban"
    DEFINITION_LIST = "definition-list"


class _Patterns:
    """Pre-compiled regexes for codegen input validation."""

    SCHEME: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
    FIELD_NAME: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SampleState(RuntimeContract):
    """Validated wrapper around the untrusted sample-state dict the agent supplies.

    ``fields`` carries a JSON-shaped tree (scalar / list / nested dict). The
    type is declared ``dict[str, object]`` rather than a recursive alias so
    Pydantic can build a schema; ``_SampleStateInspector`` is the canonical
    contract enforcer and runs in the ``before`` validator with strict typing,
    depth, and size limits.
    """

    fields: dict[str, object] = Field(default_factory=dict)

    @field_validator("fields", mode="before")
    @classmethod
    def _coerce_fields(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(_Messages.SAMPLE_STATE_NOT_MAPPING)
        if len(value) > _Limits.MAX_FIELDS:
            raise ValueError(_Messages.SAMPLE_STATE_TOO_LARGE)
        node_count = _SampleStateInspector.count_nodes(value)
        if node_count > _Limits.MAX_TOTAL_NODES:
            raise ValueError(_Messages.SAMPLE_STATE_TOO_LARGE)
        coerced: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ValueError(_Messages.SAMPLE_STATE_FIELD_NAME)
            normalized_key = raw_key.strip()
            if len(normalized_key) > _Limits.FIELD_NAME_MAX:
                raise ValueError(_Messages.SAMPLE_STATE_FIELD_NAME)
            coerced[normalized_key] = _SampleStateInspector.normalize(
                raw_value, depth=1
            )
        return coerced

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object] | None) -> SampleState:
        """Build a ``SampleState`` from an untrusted mapping; ``None`` yields empty."""
        if mapping is None:
            return cls(fields={})
        return cls(fields=mapping)  # type: ignore[arg-type]


class _SampleStateInspector:
    """Helpers that enforce the depth/size/type rules on untrusted sample-state trees."""

    @classmethod
    def count_nodes(cls, value: object) -> int:
        total = 1
        if isinstance(value, Mapping):
            for inner in value.values():
                total += cls.count_nodes(inner)
        elif isinstance(value, list | tuple):
            for inner in value:
                total += cls.count_nodes(inner)
        return total

    @classmethod
    def normalize(cls, value: object, *, depth: int) -> object:
        if depth > _Limits.MAX_NESTED_DEPTH:
            raise ValueError(_Messages.SAMPLE_STATE_TOO_DEEP)
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return value
        if isinstance(value, str):
            if len(value) > _Limits.STRING_VALUE_MAX:
                raise ValueError(_Messages.SAMPLE_STATE_STRING_TOO_LONG)
            return value
        if isinstance(value, list | tuple):
            if len(value) > _Limits.MAX_LIST_ITEMS:
                raise ValueError(_Messages.SAMPLE_STATE_TOO_LARGE)
            return [cls.normalize(item, depth=depth + 1) for item in value]
        if isinstance(value, Mapping):
            if len(value) > _Limits.MAX_FIELDS:
                raise ValueError(_Messages.SAMPLE_STATE_TOO_LARGE)
            normalized: dict[str, object] = {}
            for raw_key, raw_value in value.items():
                if not isinstance(raw_key, str) or not raw_key.strip():
                    raise ValueError(_Messages.SAMPLE_STATE_FIELD_NAME)
                key = raw_key.strip()
                if len(key) > _Limits.FIELD_NAME_MAX:
                    raise ValueError(_Messages.SAMPLE_STATE_FIELD_NAME)
                normalized[key] = cls.normalize(raw_value, depth=depth + 1)
            return normalized
        raise ValueError(_Messages.SAMPLE_STATE_VALUE_TYPE)


class AdapterCodegenRequest(RuntimeContract):
    """Validated request envelope for one tier-2 adapter generation."""

    scheme: str = Field(min_length=1, max_length=_Limits.SCHEME_MAX)
    layout: LayoutTemplate
    sample_state: SampleState = Field(default_factory=SampleState)

    @field_validator("scheme", mode="before")
    @classmethod
    def _normalize_scheme(cls, value: object, info: ValidationInfo) -> str:
        if not isinstance(value, str):
            raise ValueError(_Messages.SCHEME_INVALID)
        normalized = value.strip().lower()
        if not normalized or not _Patterns.SCHEME.fullmatch(normalized):
            raise ValueError(_Messages.SCHEME_INVALID)
        return normalized

    @field_validator("layout", mode="before")
    @classmethod
    def _normalize_layout(cls, value: object) -> LayoutTemplate:
        if isinstance(value, LayoutTemplate):
            return value
        if not isinstance(value, str):
            raise ValueError(_Messages.UNKNOWN_LAYOUT)
        normalized = value.strip().lower()
        for candidate in LayoutTemplate:
            if candidate.value == normalized:
                return candidate
        raise ValueError(_Messages.UNKNOWN_LAYOUT)


class AdapterCodegenResult(RuntimeContract):
    """Return value of ``RenderAdapterGenerator.generate``."""

    scheme: str = Field(min_length=1, max_length=_Limits.SCHEME_MAX)
    layout: LayoutTemplate
    schema_version: PositiveInt
    adapter_source: str = Field(min_length=1, max_length=_Limits.ADAPTER_SOURCE_MAX)
    generated_at: str = Field(min_length=1, max_length=64)
    generator_model: str = Field(min_length=1, max_length=64)

    def payload(self) -> dict[str, str | int]:
        """Return the wire-shaped payload for the ``adapter_generated`` event."""
        return {
            "scheme": self.scheme,
            "layout": self.layout.value,
            "schema_version": int(self.schema_version),
            "adapter_source": self.adapter_source,
            "generated_at": self.generated_at,
            "generator_model": self.generator_model,
        }


__all__ = [
    "AdapterCodegenRequest",
    "AdapterCodegenResult",
    "LayoutTemplate",
    "SampleState",
]
