"""Response schema for ``GET /v1/agent/runs/{run_id}/surfaces`` (PRD-A3 D7).

The surfaces endpoint serves the SurfaceStore projection — a fold over the run's
Work Ledger (``agent_runtime.surfaces_v2.projection``). This module only names
the HTTP response; the fold contracts (``SurfaceSnapshot`` / ``SurfaceViewState``)
live with the projection and are re-exported here so route wiring imports one
place. The api-types mirror is ``RunSurfacesResponse`` in
``packages/api-types/src/ledger.ts``.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.projection import (
    SurfaceSnapshot,
    SurfaceViewState,
)


class RunSurfacesResponse(RuntimeContract):
    """The projected surfaces for one run (SurfaceStore fold output)."""

    run_id: str
    surfaces: tuple[SurfaceSnapshot, ...]
    latest_sequence_no: int


__all__ = [
    "RunSurfacesResponse",
    "SurfaceSnapshot",
    "SurfaceViewState",
]
