"""Team destination (Phase 12) — read-projection over existing identity.

The Team destination wraps the existing ``users`` +
``organization_members`` + ``role_assignments`` tables — no new identity
provider, no parallel invite store, no parallel audit chain. Mutations
delegate to :class:`backend_app.identity.invitations.InvitationsService`
(invite) and the canonical :class:`IdentityStore` (role change /
offboarding cascade).

Sub-PRD: ``docs/atlas-new-design/destinations/team-memory-cmdk-prd.md``
§3.1 (wire), §4.1 (endpoints), §6.1 (ACL).

Modules:

* :mod:`backend_app.team.store` — read projection + presence KV +
  asset-count Protocols, plus in-memory adapters.
* :mod:`backend_app.team.service` — ACL, invariants (sole-owner /
  demote-self / admin-required), offboarding cascade orchestration.
* :mod:`backend_app.team.routes` — ``/v1/team/*`` HTTP surface.
* :mod:`backend_app.team.sse` — ``GET /v1/team/stream`` SSE.

Authorization (sub-PRD §6.1):

* Read list / detail — tenant member.
* Admin tab on detail (``recent_activity``) — tenant admin only.
* Invite — admin (delegates to identity invite path).
* Role change — admin; cannot demote self; cannot demote sole owner.
* Offboarding — admin; per-asset reassignment cascade; NO force-transfer
  endpoint (cross-audit §9.8 Q1 — Routines §9.7 Q12 STAYS DEFERRED).
"""

from __future__ import annotations

from backend_app.team.routes import register_team_routes
from backend_app.team.service import (
    OffboardingAssetOutcome,
    OffboardingResult,
    TeamConflict,
    TeamError,
    TeamForbidden,
    TeamInvalidRequest,
    TeamNotFound,
    TeamService,
)
from backend_app.team.sse import (
    InMemoryTeamActivityBus,
    TeamActivityBus,
    TeamStreamEnvelope,
    register_team_sse_routes,
)
from backend_app.team.store import (
    InMemoryPresenceKv,
    InMemoryTeamStore,
    PersonRow,
    Presence,
    PresenceKv,
    PresenceState,
    StoreBackedAssetCounts,
    TeamRole,
    TeamStore,
    ZeroAssetCounts,
    team_role_to_system_role,
)

__all__ = [
    "InMemoryPresenceKv",
    "InMemoryTeamActivityBus",
    "InMemoryTeamStore",
    "OffboardingAssetOutcome",
    "OffboardingResult",
    "PersonRow",
    "Presence",
    "PresenceKv",
    "PresenceState",
    "StoreBackedAssetCounts",
    "TeamActivityBus",
    "TeamConflict",
    "TeamError",
    "TeamForbidden",
    "TeamInvalidRequest",
    "TeamNotFound",
    "TeamRole",
    "TeamService",
    "TeamStore",
    "TeamStreamEnvelope",
    "ZeroAssetCounts",
    "register_team_routes",
    "register_team_sse_routes",
    "team_role_to_system_role",
]
