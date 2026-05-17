"""Home destination (Phase 2) — morning-briefing aggregator + SSE stream.

The aggregator (``GET /v1/home``) composes sections from disparate
stores (conversations, runs, todos, approvals, inbox, calendar
connectors). Each section returns a ``SectionResult`` so a partial
outage degrades gracefully instead of blanking the whole page.

The SSE stream (``GET /v1/home/stream``) pushes live activity-feed
events to clients with ``Last-Event-ID`` resume.

Wire shape lives in ``packages/api-types/src/home.ts``; the Python
mirror is the response_model on ``home.routes.register_home_routes``.

Current state (Phase 2 redispatch, scope narrowed):

* ``greeting`` — real (IdP given_name → display_name first-token → null)
* ``pinned_chats`` — stub (no pinning store exists yet)
* ``recent_runs`` — stub (ai-backend boundary; deferred to follow-up)
* ``favorite_tools`` — stub (Phase 8 Tools destination)
* ``todays_focus`` — stub (Phases 3/4 todos+approvals+inbox)
* ``upcoming_meetings`` — stub (calendar connector adapter)
* ``activity`` — stub (unified activity log)
"""

from __future__ import annotations

from backend_app.home.routes import register_home_routes
from backend_app.home.sse import register_home_sse_routes

__all__ = ["register_home_routes", "register_home_sse_routes"]
