"""HTTP route for the ⌘K palette (team-memory-cmdk-prd §4.3).

One route:

* ``GET /v1/palette/search?q=…&top_k=…&current_route=…`` —
  returns :class:`PaletteSearchResponse` (api-types/palette.ts).

Auth: identity comes from
:meth:`BackendServiceAuthenticator.scoped_identity`. ACL filtering is
inside :class:`PaletteService` (it calls the canonical
:func:`backend_app.projects.acl.is_member`) — there is no parallel ACL
here.

The route is the **only** place we measure wall-clock; ``took_ms`` is
the route's responsibility. The 200ms p95 budget (sub-PRD §4.3) is
held by:

* In-memory tokenization + BM25-lite over the denormalized index.
* No external embedding call on the GET path — vector recall lands on
  the Postgres adapter with IVFFLAT and falls back to BM25 if the
  embed leg times out.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, Query, Request

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.palette.service import PaletteService, hit_to_dict


logger = logging.getLogger(__name__)


class Constants:
    """Class-namespaced constants for the palette route."""

    class Query:
        MIN_Q_LEN = 0  # empty q is allowed → recency fallback
        MAX_Q_LEN = 200
        DEFAULT_TOP_K = 20
        MAX_TOP_K = 50


def register_palette_routes(app: FastAPI, *, service: PaletteService) -> None:
    """Attach the palette search route onto a backend FastAPI app.

    Mounted from ``backend_app.app.create_app`` after the destinations
    are constructed so their refresh dispatcher has been injected.
    """

    @app.get(
        "/v1/palette/search",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def search_palette(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        q: str = Query(default="", max_length=Constants.Query.MAX_Q_LEN),
        top_k: int = Query(
            default=Constants.Query.DEFAULT_TOP_K,
            ge=1,
            le=Constants.Query.MAX_TOP_K,
        ),
        current_route: str | None = Query(default=None, max_length=200),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        wall_clock_start = time.perf_counter()
        hits = service.search(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            query=q,
            top_k=top_k,
            current_route=current_route,
        )
        took_ms = int((time.perf_counter() - wall_clock_start) * 1000)
        return {
            "hits": [hit_to_dict(hit) for hit in hits],
            "took_ms": took_ms,
        }
