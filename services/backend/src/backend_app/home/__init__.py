"""Home destination (Phase 9) — morning-briefing aggregator + SSE stream.

The Phase 9 redesign supersedes the Phase 2 seven-section model. The
aggregator (``GET /v1/home``) composes:

* ``greeting``            — IdP given_name → display_name first-token →
                            ``None``; plus tenant_local_date / tenant_local_iso.
* ``triage``              — TriageCounts (approvals_waiting, runs_failed_24h,
                            todos_overdue, todos_due_today).
* ``today_timeline``      — merged meetings / routine_fires / todo_dues /
                            run_scheduled; SectionResult-wrapped.
* ``whats_new``           — rows since ``users.home_last_visit_at``; carries
                            ``since_iso`` cutoff.
* ``in_flight_projects``  — top-N projects with ``last_activity_at > now-7d``.
* ``live_activity``       — initial backfill for the LiveActivityRail.
* ``quick_actions``       — server-driven defaults (admin filter).

Wire shape lives in ``packages/api-types/src/home.ts``; the Python
mirror is the response_model on ``home.routes.register_home_routes``.

The SSE stream (``GET /v1/home/stream``) pushes live activity-feed +
triage / timeline / whats-new "refetch hint" envelopes to clients with
``Last-Event-ID`` resume.
"""

from __future__ import annotations

from backend_app.home.routes import register_home_routes
from backend_app.home.sse import register_home_sse_routes

__all__ = ["register_home_routes", "register_home_sse_routes"]
