"""Section composers for the Home destination.

Every composer returns a dict matching ``SectionResult`` from the
TypeScript wire (``{status, data?, error?, retry_after_ms?}``) so the
route layer can stitch them into the HomeResponse without per-section
schema work.

Greeting is the only section that runs real logic in this PR. Every
other composer ships a stub returning ``data=[]`` with a comment
pointing at the destination phase that unlocks the real wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Greeting — real implementation
# ---------------------------------------------------------------------------


def compose_greeting(
    *,
    now: datetime,
    user: Any,
) -> dict[str, Any]:
    """Build the greeting payload from the verified session identity.

    Fallback chain per cross-audit §9.5: IdP given_name → first-token of
    IdP name → ``None`` (FE renders "Good morning." generic). We never
    fall back to the local-part of the email — emails leak signal a UI
    greeting shouldn't surface (job title, role, internal usernames).

    ``user`` is an identity-store UserRecord; ``metadata`` carries IdP
    claims when the SSO path mints the user (OIDC stores given_name
    there). For dev-IdP / SCIM-imported users, ``metadata`` is empty
    and the fallback uses ``display_name`` directly.
    """

    return {
        "display_name": _resolve_greeting_name(user),
        "time_segment": _time_segment(now),
    }


def _resolve_greeting_name(user: Any) -> str | None:
    """IdP given_name → first-token of display_name → None."""

    metadata = getattr(user, "metadata", None)
    if isinstance(metadata, dict):
        idp_given = metadata.get("given_name")
        if isinstance(idp_given, str):
            trimmed = idp_given.strip()
            if trimmed:
                return trimmed

    display_name = getattr(user, "display_name", None)
    if isinstance(display_name, str):
        trimmed = display_name.strip()
        if trimmed:
            # First whitespace-delimited token; "Sarah Chen" → "Sarah".
            first_token = trimmed.split()[0]
            if first_token:
                return first_token

    return None


def _time_segment(now: datetime) -> str:
    """Bucket the server clock into morning/afternoon/evening.

    Boundaries are conventional, not localised — Phase 2 ships a single
    tenant-clock cut. When per-user timezone lands (Phase 3 settings
    surface), pass a localised ``now`` here instead of widening the
    function.
    """

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hour = now.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


# ---------------------------------------------------------------------------
# Stub composers — each returns a SectionResult{status:"ok", data: []}.
# TODOs point at the destination phase that unlocks real wiring.
# ---------------------------------------------------------------------------


def compose_pinned_chats_stub() -> dict[str, Any]:
    # TODO(Phase 2 follow-up): wire to conversations store when pinning
    # lands (chat-surface adds a `pinned_at` column or a separate
    # `chat_pins` join table; both shapes already on the roadmap).
    return {"status": "ok", "data": []}


def compose_recent_runs_stub() -> dict[str, Any]:
    # TODO: wire to ai-backend /v1/agent/runs via the internal
    # service-token path once the runs-list route lands. The backend
    # facade is the only legal cross-service caller; this composer
    # will call the ai-backend over HTTP (no Python imports).
    return {"status": "ok", "data": []}


def compose_favorite_tools_stub() -> dict[str, Any]:
    # TODO: wire to MCP catalog + skills store with use-count when the
    # Phase 8 Tools destination ships. The use-count column doesn't
    # exist yet — Phase 8 adds the per-user tool-invocation table.
    return {"status": "ok", "data": []}


def compose_todays_focus_stub() -> dict[str, Any]:
    # TODO: wire to todos + approvals + inbox after Phases 3/4 land.
    # The route composes inputs from three stores then scores them via
    # ``scoring.compute_focus_score``; the scoring helper is already
    # shipped (pure-function, unit-tested) so the wiring is the only
    # missing piece.
    return {"status": "ok", "data": []}


def compose_upcoming_meetings_stub() -> dict[str, Any]:
    # TODO: wire when calendar connector adapter lands.
    # Returning ``status="unavailable"`` (not "ok" with empty data) so
    # the FE renders the "Connect a calendar" CTA instead of an empty
    # list. The error code is a stable identifier the FE switches on.
    return {
        "status": "unavailable",
        "error": "no_calendar_connector",
    }


def compose_activity_stub() -> dict[str, Any]:
    # TODO: wire to a unified activity log or compose from disparate
    # stores in a follow-up. The shape supports many kinds (run,
    # approval, chat, todo, inbox, routine_fire, library_change,
    # member_action) so the composer reads from N stores and merges
    # on occurred_at.
    return {"status": "ok", "data": []}


__all__ = [
    "compose_activity_stub",
    "compose_favorite_tools_stub",
    "compose_greeting",
    "compose_pinned_chats_stub",
    "compose_recent_runs_stub",
    "compose_todays_focus_stub",
    "compose_upcoming_meetings_stub",
]
