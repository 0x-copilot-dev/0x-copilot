"""Response schema for ``GET /v1/agent/runs/{run_id}/surfaces`` (PRD-A3 D7 + B2).

The surfaces endpoint serves the SurfaceStore projection — a fold over the run's
Work Ledger (``agent_runtime.surfaces_v2.projection``). This module only names
the HTTP response; the fold contracts (``SurfaceSnapshot`` / ``SurfaceViewState``)
live with the projection and are re-exported here so route wiring imports one
place. The api-types mirror is ``RunSurfacesResponse`` in
``packages/api-types/src/ledger.ts``.

PRD-B2 adds **content hydration**: the metadata-only ``SurfaceSnapshot`` the pure
fold produces is extended at the HTTP layer with the surface's materialized
``state`` (``{spec?, data}``), resolved from the same run's persisted events by
``SurfaceContentProjection``. The extension lives here — NOT on the projection's
``SurfaceSnapshot`` — so the cross-language parity snapshot
(``SurfaceStoreState.model_dump``) stays byte-identical; ``state`` is additive and
optional (``None`` when a surface has no content event yet — an honest
"not hydrated", never a fabricated body).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import ViewBasis, ViewKeep, ViewTier
from agent_runtime.surfaces_v2.projection import (
    SurfaceSnapshot,
    SurfaceViewState,
)


class HydratedSurfaceSnapshot(SurfaceSnapshot):
    """A folded surface snapshot enriched with its resolved v1 content (B2).

    ``state`` carries the v1 surface envelope's ``{spec?, data}`` — the exact
    shape the surface renderers consume — resolved from the run's persisted
    events. ``None`` until a content-bearing event has landed for the surface, so
    the canvas degrades to its honest skeleton / raw fallback rather than showing
    a fabricated body.
    """

    state: dict[str, object] | None = None


class RunSurfacesResponse(RuntimeContract):
    """The projected surfaces for one run (SurfaceStore fold + B2 content)."""

    run_id: str
    surfaces: tuple[HydratedSurfaceSnapshot, ...]
    latest_sequence_no: int


# ---------------------------------------------------------------------------
# PRD-B3 — per-surface view-lifecycle mutating endpoints
# ---------------------------------------------------------------------------


class SurfaceViewPreferenceRequest(RuntimeContract):
    """Body for ``POST /v1/agent/surfaces/{surface_id}/view-preference``.

    ``keep`` is the durable tier the user is pinning (``generic`` | ``shaped``).
    ``actor`` is server-stamped to ``user`` on the appended ledger event — never
    caller-supplied — so the request carries only the choice.
    """

    keep: ViewKeep


class SurfaceViewActionResponse(RuntimeContract):
    """200 body for ``POST .../regenerate`` — the re-derived view + its ledger id."""

    surface_id: str
    tier: ViewTier
    basis: ViewBasis
    ledger_id: str


class SurfaceViewPreferenceResponse(RuntimeContract):
    """200 body for ``POST .../view-preference`` — the pinned tier + its ledger id."""

    surface_id: str
    keep: ViewKeep
    ledger_id: str


# ---------------------------------------------------------------------------
# PRD-B4 — user-invited "Suggest a shape"
# ---------------------------------------------------------------------------


class ShapeRequestForwardBody(BaseModel):
    """Body the facade forwards to ``POST .../shape-request`` (PRD-B4).

    The facade stamps ``org_id`` / ``user_id`` into the body via
    ``scoped_payload`` (and also as trusted headers, which ``scoped_identity``
    treats as authoritative), so this model reads only ``run_id`` and **ignores**
    the stamped identity keys (``extra="ignore"`` — a ``RuntimeContract``'s
    ``extra="forbid"`` would reject them). ``run_id`` is required: the canvas is
    per-run (FR-A2) and the client always knows it.
    """

    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(min_length=1)


class ShapeRequestAccepted(RuntimeContract):
    """``202 Accepted`` body for ``POST .../shape-request`` (PRD-B4).

    The invited attempt is scheduled; the outcome arrives over the run SSE stream
    as ``shape.requested`` → ``shape.resolved`` ledger events (no polling). The
    api-types mirror is ``ShapeRequestAccepted`` in ``packages/api-types``.
    """

    surface_id: str
    # Pinned to the constant ``"requested"`` — the accept beat, never the outcome.
    status: Literal["requested"] = "requested"


__all__ = [
    "HydratedSurfaceSnapshot",
    "RunSurfacesResponse",
    "ShapeRequestAccepted",
    "ShapeRequestForwardBody",
    "SurfaceSnapshot",
    "SurfaceViewActionResponse",
    "SurfaceViewPreferenceRequest",
    "SurfaceViewPreferenceResponse",
    "SurfaceViewState",
]
