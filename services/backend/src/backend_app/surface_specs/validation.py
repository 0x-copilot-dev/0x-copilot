"""Schema re-validation for stored SurfaceSpecs (generative-UI PRD-08).

The backend is the persistence authority for generated SurfaceSpecs, so it
must re-validate every spec on write against the **single source of truth**
JSON Schema shipped in ``copilot_service_contracts`` (the same
``surface_spec.schema.json`` the ai-backend pydantic model and the api-types
guards mirror — shared across services via PYTHONPATH, exactly like
``adapter_allowlist``). A cross-service Python import is a hard repo boundary;
loading the shared JSON contract is not — it is a constants-only package.

We deliberately do **not** pull in a third-party ``jsonschema`` validator: it
is not a declared dependency of this service (reproducible, scoped Docker
builds), and the effort's guardrail prefers hand-rolled checks. Instead this
module walks the loaded schema itself, so editing the schema file genuinely
changes what is accepted here. It supports precisely the JSON-Schema subset the
SurfaceSpec contract uses: ``type`` / ``const`` / ``enum`` / ``required`` /
``additionalProperties`` / ``properties`` / ``items`` / ``$ref`` (local
``#/$defs/*``) / ``minLength`` / ``maxLength`` / ``pattern``.
"""

from __future__ import annotations

import re
from typing import Any, Final

from copilot_service_contracts.surface_spec import load_surface_spec_schema

# Load + parse the shared schema once at import (the ``adapter_allowlist``
# precedent: the JSON is a const after process start).
_SCHEMA: Final[dict[str, Any]] = load_surface_spec_schema()
_DEFS: Final[dict[str, Any]] = (
    _SCHEMA.get("$defs", {}) if isinstance(_SCHEMA.get("$defs"), dict) else {}
)

# Compiled-regex cache for ``pattern`` keywords (patterns are small + fixed).
_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}

_REF_PREFIX: Final = "#/$defs/"
_MAX_DEPTH: Final = 32  # defence-in-depth against a pathological $ref cycle


class SurfaceSpecSchemaError(ValueError):
    """Raised when a spec dict does not satisfy ``surface_spec.schema.json``.

    Carries only a short, safe, actionable message (a JSON-pointer-ish path
    plus the reason) — never internal traceback content — so it is safe to
    surface as an HTTP 422 detail.
    """


def validate_surface_spec_dict(spec: object) -> None:
    """Validate ``spec`` against the shared SurfaceSpec JSON Schema.

    Raises :class:`SurfaceSpecSchemaError` on the first violation; returns
    ``None`` when the spec is valid.
    """

    _validate(spec, _SCHEMA, path="spec", depth=0)


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve a one-hop local ``$ref`` (``#/$defs/<name>``) to its definition."""

    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    if not ref.startswith(_REF_PREFIX):
        raise SurfaceSpecSchemaError(f"unsupported schema $ref: {ref!r}")
    name = ref[len(_REF_PREFIX) :]
    target = _DEFS.get(name)
    if not isinstance(target, dict):
        raise SurfaceSpecSchemaError(f"unknown schema $def: {name!r}")
    return target


def _validate(
    instance: object, schema: dict[str, Any], *, path: str, depth: int
) -> None:
    if depth > _MAX_DEPTH:  # pragma: no cover - guardrail, unreachable for this schema
        raise SurfaceSpecSchemaError(f"{path}: schema nesting too deep")
    schema = _resolve(schema)

    if "const" in schema and instance != schema["const"]:
        raise SurfaceSpecSchemaError(
            f"{path}: must equal {schema['const']!r}, got {_short(instance)}"
        )

    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        allowed = ", ".join(repr(item) for item in enum)
        raise SurfaceSpecSchemaError(
            f"{path}: must be one of [{allowed}], got {_short(instance)}"
        )

    declared_type = schema.get("type")
    if isinstance(declared_type, str):
        _check_type(instance, declared_type, path=path)
        if declared_type == "object":
            _validate_object(instance, schema, path=path, depth=depth)
        elif declared_type == "array":
            _validate_array(instance, schema, path=path, depth=depth)
        elif declared_type == "string":
            _validate_string(instance, schema, path=path)


def _validate_object(
    instance: object, schema: dict[str, Any], *, path: str, depth: int
) -> None:
    assert isinstance(instance, dict)  # guaranteed by _check_type
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if name not in instance:
                raise SurfaceSpecSchemaError(f"{path}: missing required field {name!r}")

    if schema.get("additionalProperties") is False:
        for name in instance:
            if name not in properties:
                raise SurfaceSpecSchemaError(f"{path}: unknown field {name!r}")

    for name, subschema in properties.items():
        if name in instance and isinstance(subschema, dict):
            _validate(
                instance[name],
                subschema,
                path=f"{path}.{name}",
                depth=depth + 1,
            )


def _validate_array(
    instance: object, schema: dict[str, Any], *, path: str, depth: int
) -> None:
    assert isinstance(instance, list)  # guaranteed by _check_type
    items = schema.get("items")
    if isinstance(items, dict):
        for index, element in enumerate(instance):
            _validate(element, items, path=f"{path}.{index}", depth=depth + 1)


def _validate_string(instance: object, schema: dict[str, Any], *, path: str) -> None:
    assert isinstance(instance, str)  # guaranteed by _check_type
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(instance) < min_length:
        raise SurfaceSpecSchemaError(f"{path}: shorter than minLength {min_length}")
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(instance) > max_length:
        raise SurfaceSpecSchemaError(f"{path}: longer than maxLength {max_length}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and not _compiled(pattern).search(instance):
        raise SurfaceSpecSchemaError(f"{path}: does not match required pattern")


def _check_type(instance: object, declared: str, *, path: str) -> None:
    if declared == "object":
        if not isinstance(instance, dict):
            raise SurfaceSpecSchemaError(f"{path}: must be an object")
    elif declared == "array":
        if not isinstance(instance, list):
            raise SurfaceSpecSchemaError(f"{path}: must be an array")
    elif declared == "string":
        if not isinstance(instance, str):
            raise SurfaceSpecSchemaError(f"{path}: must be a string")
    elif declared == "integer":
        # bool is an int subclass in Python; JSON integers are not booleans.
        if isinstance(instance, bool) or not isinstance(instance, int):
            raise SurfaceSpecSchemaError(f"{path}: must be an integer")
    elif declared == "number":  # pragma: no cover - unused by this schema today
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            raise SurfaceSpecSchemaError(f"{path}: must be a number")
    elif declared == "boolean":  # pragma: no cover - unused by this schema today
        if not isinstance(instance, bool):
            raise SurfaceSpecSchemaError(f"{path}: must be a boolean")


def _compiled(pattern: str) -> re.Pattern[str]:
    cached = _PATTERN_CACHE.get(pattern)
    if cached is None:
        cached = re.compile(pattern)
        _PATTERN_CACHE[pattern] = cached
    return cached


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


__all__ = ["SurfaceSpecSchemaError", "validate_surface_spec_dict"]
