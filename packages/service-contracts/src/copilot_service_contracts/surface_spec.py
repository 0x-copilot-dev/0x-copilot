"""Loader + constants for the SurfaceSpec schema (the JSON sibling of this module).

Single source of truth shared across the generative-UI effort. The ai-backend
pydantic model (``agent_runtime.capabilities.surfaces.spec_models``) validates
against this schema; the TypeScript types + runtime guards in
``packages/api-types`` mirror it. A cross-language parity test pins the pydantic
model to this file so the two cannot drift.

Follows the ``adapter_allowlist`` precedent: the JSON is loaded at import time
and treated as const after the process starts.
"""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable

# Frozen schema contract version. A bump is an amendment to PRD-01
# (generative-UI surface contract), never a local edit.
SURFACE_SPEC_VERSION: int = 1

# The render families a SurfaceSpec may bind to (v1). A frontend may implement
# a subset; an unknown archetype falls back to the tier-3 generic renderer and
# is never an error. Order matches ``surface_spec.schema.json`` ``$defs.archetype``.
SURFACE_ARCHETYPES: tuple[str, ...] = (
    "record",
    "table",
    "message",
    "doc",
    "board",
    "event",
    "timeline",
    "dashboard",
    "file",
    "form",
)


class _SchemaResource:
    """Where the JSON sibling lives inside the installed package."""

    PACKAGE: str = "copilot_service_contracts"
    FILENAME: str = "surface_spec.schema.json"


# Traversable handle to the schema file, resolvable whether the package is
# installed on disk or imported from source via ``PYTHONPATH``.
SURFACE_SPEC_SCHEMA_PATH: Traversable = files(_SchemaResource.PACKAGE).joinpath(
    _SchemaResource.FILENAME
)


def load_surface_spec_schema() -> dict[str, object]:
    """Return the SurfaceSpec JSON Schema as a parsed dict."""
    raw = SURFACE_SPEC_SCHEMA_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


__all__ = [
    "SURFACE_SPEC_VERSION",
    "SURFACE_ARCHETYPES",
    "SURFACE_SPEC_SCHEMA_PATH",
    "load_surface_spec_schema",
]
