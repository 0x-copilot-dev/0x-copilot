"""Home destination (Phase 2) — morning-briefing aggregator.

The aggregator composes sections from disparate stores (conversations,
runs, todos, approvals, inbox, calendar connectors). Each section
returns a ``SectionResult`` so a partial outage degrades gracefully
instead of blanking the whole page.

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

from backend_app.home.routes import register_home_routes

__all__ = ["register_home_routes"]
