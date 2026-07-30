"""Read API for the Context Occupancy Ledger (context-attribution design §7).

Two endpoints over the rows the capture seam writes, both read-only:

``GET /v1/agent/runs/{run_id}/context/occupancy``
    The per-turn series for one run, oldest-first, optionally filtered to a
    single ``graph_scope``.

``GET /v1/agent/conversations/{conversation_id}/context/occupancy``
    The newest **root-scope** snapshot across the conversation's runs — "what is
    in context right now".

**Why not the paths §7 names.** The design writes them as ``.../context``, but
``GET /v1/agent/conversations/{conversation_id}/context`` already exists and
serves a different shape (``ConversationContextResponse`` — the token-window
summary). FastAPI resolves same-path routes in registration order, so a second
registration there would be dead code that never runs. Occupancy is the
decomposition *of* that window, so ``/context/occupancy`` is both free and
accurate, and the run endpoint mirrors it so one family keeps one shape.

**Guarded exactly like ``/v1/usage/*``.** Router-level ``RequireScopes``
(``runtime:use``) plus ``RuntimeApiRoutes.scoped_identity``, which prefers the
trusted service-token headers and falls back to query params — the identical
posture ``runtime_api/http/agent_usage.py`` uses. Nothing here is admin-only:
occupancy describes the caller's own runs.

**Absence is one answer, on purpose.** An unknown run, a run in another tenant, a
run belonging to another user in the same tenant, and a real run measured before
this ledger existed all return ``200`` with an empty series. They are
deliberately indistinguishable: a ``404`` for the cross-tenant case and a ``200``
for the not-yet-measured case would turn this endpoint into an existence oracle
for run ids in other organizations. Tenant isolation is enforced twice — the run
or conversation must resolve inside the caller's scope *before* any occupancy row
is touched, and the store query is itself ``org_id``-scoped.

**This surface writes nothing.** Occupancy is an observation lane, never the
money tracker: no usage row, no ``Purpose`` extension, no mutation of any kind
(§6.1).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from agent_runtime.api.constants import Keys
from agent_runtime.api.ports import PersistencePort
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.context_occupancy import (
    ContextOccupancyResponse,
    ContextOccupancySnapshotPayload,
    ConversationContextOccupancyResponse,
)

from copilot_service_contracts.scopes import RUNTIME_USE


class ContextOccupancyRoutes:
    """Handlers for the two occupancy read endpoints.

    Both are thin: resolve identity, prove the subject belongs to the caller,
    read tenant-scoped rows, project. The interesting decisions are the two
    guards documented on the module and the root-scope rule below — there is no
    aggregation to own, because a run's occupancy series *is* the answer and
    summing it would be wrong (§6.2).
    """

    #: How many of a conversation's runs the "right now" endpoint will inspect
    #: before giving up. Runs arrive newest-first and the newest run that has any
    #: root-scope occupancy wins, so this only matters for a conversation whose
    #: most recent runs were never measured — a bounded walk is preferable to
    #: scanning an arbitrarily long history to answer a question about *now*.
    _LATEST_RUN_SCAN_LIMIT = 10

    @classmethod
    async def run_context_occupancy(
        cls,
        request: Request,
        run_id: str,
        graph_scope: RuntimeContextGraphScope | None = Query(None),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ContextOccupancyResponse:
        """Return one run's per-turn occupancy series, oldest-first.

        ``graph_scope`` is echoed back on the response because the series is
        summable only *within* a scope: root and subagent snapshots describe
        different windows, and a client that adds an unfiltered series together
        reports utilization no model ever saw (§6.2).
        """

        from runtime_api.http.routes import RuntimeApiRoutes

        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = request.app.state.runtime_persistence
        if not await cls._run_is_readable(
            persistence,
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
        ):
            return ContextOccupancyResponse(run_id=run_id, graph_scope=graph_scope)
        rows = await persistence.list_context_occupancy(
            org_id=org_id,
            run_id=run_id,
            graph_scope=graph_scope,
        )
        return ContextOccupancyResponse(
            run_id=run_id,
            graph_scope=graph_scope,
            snapshots=tuple(
                ContextOccupancySnapshotPayload.from_record(row) for row in rows
            ),
        )

    @classmethod
    async def conversation_context_occupancy(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationContextOccupancyResponse:
        """Return the newest root-scope snapshot for a conversation, or nothing.

        Root scope only, and not by accident. A subagent's last model call is
        frequently the most recent measurement in wall-clock terms, but it
        describes a child window that no longer exists — returning it as "what is
        in context" would be a confident answer to a different question (§6.2).
        """

        from runtime_api.http.routes import RuntimeApiRoutes

        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        empty = ConversationContextOccupancyResponse(conversation_id=conversation_id)
        persistence = request.app.state.runtime_persistence
        conversation = await persistence.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return empty
        runs = await persistence.list_runs_for_conversation(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=cls._LATEST_RUN_SCAN_LIMIT,
        )
        for run in runs:
            latest = await cls._latest_root_snapshot(
                persistence,
                org_id=org_id,
                run_id=run.run_id,
            )
            if latest is not None:
                return ConversationContextOccupancyResponse(
                    conversation_id=conversation_id,
                    run_id=run.run_id,
                    snapshot=ContextOccupancySnapshotPayload.from_record(latest),
                )
        return empty

    @classmethod
    async def _run_is_readable(
        cls,
        persistence: PersistencePort,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
    ) -> bool:
        """Whether this caller may read ``run_id``'s occupancy at all.

        The first of two tenant gates. ``list_context_occupancy`` is already
        ``org_id``-scoped, so this is defence in depth against a future adapter
        that forgets — but it is also the *user* gate, which the store query does
        not have and ``/v1/usage/runs/{run_id}`` does enforce. Occupancy is
        strictly less sensitive than the usage rows it reconciles against, so
        matching that endpoint's owner scoping rather than loosening it is the
        conservative default.
        """

        run = await persistence.get_run(org_id=org_id, run_id=run_id)
        return run is not None and run.user_id == user_id

    @classmethod
    async def _latest_root_snapshot(
        cls,
        persistence: PersistencePort,
        *,
        org_id: str,
        run_id: str,
    ) -> RuntimeContextOccupancyRecord | None:
        """Return the last root-scope row for one run, or ``None``.

        ``list_context_occupancy`` returns oldest-first, so the newest snapshot
        is the final element. Taking the last element rather than re-sorting
        keeps this reader on exactly the ordering the port promises, which is the
        same ordering the series endpoint hands to clients.
        """

        rows = await persistence.list_context_occupancy(
            org_id=org_id,
            run_id=run_id,
            graph_scope=RuntimeContextGraphScope.ROOT,
        )
        if not rows:
            return None
        return rows[-1]


class ContextOccupancyApiRouter:
    """Build the ``/v1/agent`` router carrying the two occupancy read routes.

    A sibling router rather than routes appended to ``RuntimeApiRouter``, for the
    same reason ``AgentUsageApiRouter`` is one: the occupancy family owns its own
    module, and a separate router keeps that boundary visible in the mount list
    instead of burying two routes in a 100k-line file.

    Neither path can be shadowed by an existing route. ``/runs/{run_id}`` and
    ``/conversations/{conversation_id}/context`` are shorter path shapes, and
    FastAPI's ordering hazard applies only to same-shape patterns, so mount order
    relative to ``RuntimeApiRouter`` does not matter here.
    """

    @classmethod
    def create_router(cls) -> APIRouter:
        """Return the router with both occupancy routes under ``runtime:use``."""

        router = APIRouter(
            prefix="/v1/agent",
            tags=["agent-runtime", "context-occupancy"],
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        router.add_api_route(
            "/runs/{run_id}/context/occupancy",
            ContextOccupancyRoutes.run_context_occupancy,
            methods=["GET"],
            response_model=ContextOccupancyResponse,
            name=Keys.RouteName.RUN_CONTEXT_OCCUPANCY,
        )
        router.add_api_route(
            "/conversations/{conversation_id}/context/occupancy",
            ContextOccupancyRoutes.conversation_context_occupancy,
            methods=["GET"],
            response_model=ConversationContextOccupancyResponse,
            name=Keys.RouteName.CONVERSATION_CONTEXT_OCCUPANCY,
        )
        return router


__all__ = ("ContextOccupancyApiRouter", "ContextOccupancyRoutes")
