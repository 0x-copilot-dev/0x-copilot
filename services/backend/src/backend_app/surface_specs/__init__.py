"""SurfaceSpec registry (generative-UI PRD-08).

Durable, org-scoped persistence for generated ``SurfaceSpec`` documents that
back the generative-UI Tier-1.5 archetype renderers. Internal-only endpoints on
this core backend (beside :mod:`backend_app.adapter_registry`); the ai-backend
``BackendHttpSurfaceSpecStore`` is the sole client. There are **no facade /
app-facing routes** — specs are runtime infrastructure.

Module layout mirrors :mod:`backend_app.adapter_registry`:

* ``contracts``   — Pydantic v2 wire + record types.
* ``validation``  — re-validation against the shared ``surface_spec.schema.json``.
* ``store``       — In-memory + Postgres adapters for the ``surface_specs`` rows.
* ``service``     — org-scoped domain orchestration.
* ``router``      — FastAPI router (mounted under ``/internal/v1/surfaces/specs``).
"""

from __future__ import annotations

from backend_app.surface_specs.contracts import (
    SurfaceSpecOrigin,
    SurfaceSpecRecord,
    SurfaceSpecResponse,
    SurfaceSpecUpsert,
    SurfaceSpecView,
)
from backend_app.surface_specs.router import register_surface_specs_routes
from backend_app.surface_specs.service import SurfaceSpecService
from backend_app.surface_specs.store import (
    InMemorySurfaceSpecStore,
    PostgresSurfaceSpecStore,
    SurfaceSpecStore,
)
from backend_app.surface_specs.validation import (
    SurfaceSpecSchemaError,
    validate_surface_spec_dict,
)


__all__ = [
    "InMemorySurfaceSpecStore",
    "PostgresSurfaceSpecStore",
    "SurfaceSpecOrigin",
    "SurfaceSpecRecord",
    "SurfaceSpecResponse",
    "SurfaceSpecSchemaError",
    "SurfaceSpecService",
    "SurfaceSpecStore",
    "SurfaceSpecUpsert",
    "SurfaceSpecView",
    "register_surface_specs_routes",
    "validate_surface_spec_dict",
]
