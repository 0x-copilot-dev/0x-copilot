"""Palette search service — fans out a query across destinations, ACL-filters.

Pipeline (sub-PRD §3.3 / §4.3):

1. The route resolves the caller's identity (org_id + user_id + roles).
2. ``PaletteService.search`` calls ``store.bulk_query`` to pull the
   tenant-scoped top-K BM25 hits across all entity_kinds.
3. Each hit is ACL-checked against the entity's per-kind rule:
   * ``project`` / ``library_item`` / ``routine`` / ``tool`` /
     ``agent`` — project-scoped: caller must be owner OR
     :func:`backend_app.projects.acl.is_member` OR tenant admin.
   * ``memory`` — owner_user_id-only for user-scope rows; tenant
     member for workspace-scope (signalled by ``project_id IS NULL``
     AND ``owner_user_id IS NULL``).
   * ``person`` / ``chat`` / ``connector`` — tenant-member; owner-only
     for ``chat`` private rows (``owner_user_id`` set).
4. Hits that pass ACL are projected into ``PaletteHit`` wire shapes.
5. If zero entity hits remain, the service returns up to N "quick
   action" hits (sub-PRD §3.4 / Routines §9.7 Q10 — "Make this a
   routine?") biased by the caller-supplied context.

ACL discipline: a row that fails ACL is **silently dropped** — never
404 / 403 on the palette surface. The palette never leaks an item the
caller can't open (sub-PRD §6.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend_app.palette.store import (
    EntityKind,
    PaletteEntry,
    PaletteSearchHit,
    PaletteStorePort,
)
from backend_app.projects.acl import ProjectMembershipPort, is_member


_LOGGER = logging.getLogger(__name__)

_ADMIN_ROLES = frozenset({"admin", "owner"})
_DEFAULT_TOP_K = 20
_MAX_TOP_K = 50


# ---------------------------------------------------------------------------
# Wire-shape projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaletteHitWire:
    """One row of the palette result list — mirrors PaletteHit in
    packages/api-types/src/palette.ts.

    The route layer dataclasses->dict marshals this for the wire.
    Exactly one of ``route`` / ``target`` / ``action_token`` is set;
    keyed on ``kind``.
    """

    id: str
    kind: str  # "navigation" | "entity" | "action" | "command"
    title: str
    score: float
    subtitle: str | None = None
    icon_hint: str | None = None
    route: str | None = None
    target: dict[str, str] | None = None
    action_token: str | None = None


@dataclass(frozen=True)
class PaletteSearchResultWire:
    """Wire-shape mirror of PaletteSearchResponse."""

    hits: tuple[PaletteHitWire, ...]
    took_ms: int


# ---------------------------------------------------------------------------
# Quick-action library
# ---------------------------------------------------------------------------


class QuickActions:
    """Hardcoded starter actions (sub-PRD §3.4 / Routines §9.7 Q10).

    v1 ships 4 actions. They surface when no entity matches OR (per
    context) get ranked into the entity list when the caller is on a
    relevant route — e.g. "Make this a routine?" when the user opens
    ⌘K from a chat.
    """

    @dataclass(frozen=True)
    class Action:
        action_token: str
        title: str
        subtitle: str
        icon_hint: str
        # Predicate: returns True iff this action is appropriate for the
        # caller-supplied context. ``None`` means "always show".
        context_filter: tuple[str, ...] | None = None  # current_route prefixes

    ALL: tuple["QuickActions.Action", ...] = (
        Action(
            action_token="atlas.routine.create_from_chat",
            title="Make this a routine",
            subtitle="Schedule this chat to run on a cadence",
            icon_hint="routine",
            context_filter=("/chat",),
        ),
        Action(
            action_token="atlas.memory.create",
            title="Save as memory",
            subtitle="Add a fact or preference",
            icon_hint="memory",
            context_filter=None,
        ),
        Action(
            action_token="atlas.connector.onboard",
            title="Onboard a connector",
            subtitle="Connect a tool or calendar",
            icon_hint="connector",
            context_filter=None,
        ),
        Action(
            action_token="atlas.library.upload",
            title="Upload to library",
            subtitle="Add a file, page, or dataset",
            icon_hint="library",
            context_filter=None,
        ),
    )

    @classmethod
    def applicable(
        cls, *, current_route: str | None
    ) -> tuple["QuickActions.Action", ...]:
        """Return actions whose ``context_filter`` matches ``current_route``."""
        route = current_route or ""
        return tuple(
            action
            for action in cls.ALL
            if action.context_filter is None
            or any(route.startswith(prefix) for prefix in action.context_filter)
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PaletteService:
    """Orchestrates one ⌘K palette search.

    Construction-time dependencies:

    * ``store`` — palette index port (in-memory adapter in dev).
    * ``membership_port`` — the canonical
      :class:`ProjectMembershipPort` shared with Library / Projects /
      Routines / Inbox.
    """

    def __init__(
        self,
        *,
        store: PaletteStorePort,
        membership_port: ProjectMembershipPort,
    ) -> None:
        self._store = store
        self._membership_port = membership_port

    def search(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: tuple[str, ...],
        query: str,
        top_k: int = _DEFAULT_TOP_K,
        current_route: str | None = None,
    ) -> tuple[PaletteHitWire, ...]:
        """Run the fan-out + ACL filter + quick-action fallback pipeline.

        Returns a tuple of wire hits in display order. ``top_k`` is
        clamped to ``_MAX_TOP_K`` server-side.
        """

        clamped_top_k = max(1, min(int(top_k), _MAX_TOP_K))
        is_admin = any(role in _ADMIN_ROLES for role in caller_roles)

        # Over-fetch to absorb ACL filtering — we may drop a few rows.
        raw_hits = self._store.bulk_query(
            tenant_id=tenant_id,
            query=query,
            entity_kinds=None,
            top_k=clamped_top_k * 3,
        )

        wire_hits: list[PaletteHitWire] = []
        for hit in raw_hits:
            if not self._is_visible(
                hit.entry,
                caller_user_id=caller_user_id,
                tenant_id=tenant_id,
                is_admin=is_admin,
            ):
                continue
            wire_hits.append(self._project_to_wire(hit))
            if len(wire_hits) >= clamped_top_k:
                break

        if wire_hits:
            return tuple(wire_hits)

        # No entity matches — surface up to N quick actions.
        actions = QuickActions.applicable(current_route=current_route)
        return tuple(
            PaletteHitWire(
                id=f"hit_action_{idx}",
                kind="action",
                title=action.title,
                subtitle=action.subtitle,
                icon_hint=action.icon_hint,
                action_token=action.action_token,
                score=1.0 - (idx * 0.01),
            )
            for idx, action in enumerate(actions[:clamped_top_k])
        )

    # -- ACL ---------------------------------------------------------------

    def _is_visible(
        self,
        entry: PaletteEntry,
        *,
        caller_user_id: str,
        tenant_id: str,
        is_admin: bool,
    ) -> bool:
        """Per-kind ACL gate. Hit is dropped silently when False."""

        # Tenant isolation is the first filter; the store already
        # enforces it via the tenant_id query, but we double-check
        # here in case a future store implementation widens the scan.
        if entry.tenant_id != tenant_id:
            return False

        if is_admin:
            return True

        # Owner-only entities.
        if entry.entity_kind == EntityKind.MEMORY:
            # User-scope memory: owner-only. Workspace-scope memory has
            # owner_user_id IS NULL — visible to tenant member.
            if entry.owner_user_id is None:
                return True
            return entry.owner_user_id == caller_user_id

        if entry.entity_kind == EntityKind.CHAT:
            # Private chats carry owner_user_id; shared chats don't.
            if entry.owner_user_id is None:
                return True
            return entry.owner_user_id == caller_user_id

        # Project-scoped entities — caller must own OR be a member.
        if entry.entity_kind in {
            EntityKind.PROJECT,
            EntityKind.LIBRARY_ITEM,
            EntityKind.ROUTINE,
            EntityKind.TOOL,
            EntityKind.AGENT,
        }:
            if entry.owner_user_id == caller_user_id:
                return True
            if entry.project_id is None:
                # Personal scope — owner-only.
                return entry.owner_user_id == caller_user_id
            return is_member(
                self._membership_port,
                tenant_id=tenant_id,
                project_id=entry.project_id,
                user_id=caller_user_id,
            )

        # Tenant-member visible: person, connector, anything else.
        return True

    # -- projection --------------------------------------------------------

    @staticmethod
    def _project_to_wire(hit: PaletteSearchHit) -> PaletteHitWire:
        """Map a store hit to the wire shape.

        Entity kind drives the ``kind`` discriminator:

        * Everything destination-owned is ``entity`` with a synthesised
          ``target`` ItemRef (``{kind, id}``).
        """
        entry = hit.entry
        return PaletteHitWire(
            id=f"hit_{entry.entity_kind}_{entry.entity_id}",
            kind="entity",
            title=entry.title,
            subtitle=entry.body[:120] if entry.body else None,
            icon_hint=entry.entity_kind,
            target={"kind": entry.entity_kind, "id": entry.entity_id},
            score=hit.score,
        )

    # -- introspection (test seam) -----------------------------------------

    @property
    def store(self) -> PaletteStorePort:
        return self._store


def hit_to_dict(hit: PaletteHitWire) -> dict[str, Any]:
    """Marshal a :class:`PaletteHitWire` into the JSON wire object.

    Only fields appropriate for the hit's ``kind`` are emitted (we drop
    ``None`` rather than encoding ``null`` for absent disjunct legs).
    """
    body: dict[str, Any] = {
        "id": hit.id,
        "kind": hit.kind,
        "title": hit.title,
        "score": hit.score,
    }
    if hit.subtitle is not None:
        body["subtitle"] = hit.subtitle
    if hit.icon_hint is not None:
        body["icon_hint"] = hit.icon_hint
    if hit.kind == "navigation" and hit.route is not None:
        body["route"] = hit.route
    if hit.kind == "entity" and hit.target is not None:
        body["target"] = hit.target
    if hit.kind in {"action", "command"} and hit.action_token is not None:
        body["action_token"] = hit.action_token
    return body
