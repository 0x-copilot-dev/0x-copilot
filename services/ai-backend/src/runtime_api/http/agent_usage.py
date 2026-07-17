"""Per-agent usage aggregation route (P8-A4, Agents PRD §4.9).

Read-only projection over the canonical ``runtime_model_call_usage``
table joined to ``agent_runs.runtime_context_json -> trace_metadata
-> agent_id`` (see :meth:`PersistencePort.list_run_ids_for_agent`).

Per cross-audit §5.5 the **single-tracker invariant** stands: this
route does not write to any usage table, does not introduce a parallel
tracker, and does not extend the ``Purpose`` enumeration. Every
returned figure is summed at read time from the canonical per-call
rows.

Mounted under the existing ``/v1/usage`` router so the same
``RUNTIME_USE`` scope guard applies and the response shape lives next
to its siblings (``/v1/usage/me``, ``/v1/usage/org/subagents``, etc.).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from agent_runtime.api.constants import Keys
from agent_runtime.api.usage_service import UsageQueryService
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.usage import AgentUsageResponse, UsagePeriodWindow

from copilot_service_contracts.scopes import RUNTIME_USE


class AgentUsageRoutes:
    """Handlers for ``/v1/usage/org/agent/{agent_id}``.

    Aggregation arithmetic is deliberately co-located with the route
    handler — the math is small (sum a handful of integer columns
    bucketed by a single string key) and there is no other caller
    inside ``ai-backend``. If a second caller materialises later,
    promote :meth:`_aggregate` to ``UsageQueryService``.
    """

    # Cold-start cap mirrors ``UsageApiRoutes._COLD_START_CAP_DAYS``;
    # the live scan is bounded so a misconfigured ``since`` cannot
    # trigger a full-table walk on the canonical usage row table.
    _COLD_START_CAP_DAYS = 30

    @classmethod
    async def usage_org_agent(
        cls,
        request: Request,
        agent_id: str,
        period: Literal["today", "7d", "30d", "month"] = Query("7d"),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> AgentUsageResponse:
        """Aggregate per-agent token + cost totals over ``period``.

        Tenant-first: ``org_id`` is resolved from the trusted service
        token when present, otherwise from the query param (mirroring
        every other ``/v1/usage`` endpoint). Cross-tenant agent IDs
        return an empty totals payload because
        :meth:`PersistencePort.list_run_ids_for_agent` filters on
        ``org_id`` before any usage row is touched.
        """

        # Reuse the standard scoped_identity flow so headers > query
        # params and missing identity returns 400.
        from runtime_api.http.routes import RuntimeApiRoutes

        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id or "__agent_usage__"
        )
        start, end = UsageQueryService.parse_period(period)
        persistence = request.app.state.runtime_persistence
        run_ids = await persistence.list_run_ids_for_agent(
            org_id=org_id,
            agent_id=agent_id,
            start=start,
            end=end,
        )
        if not run_ids:
            return AgentUsageResponse(
                agent_id=agent_id,
                period=UsagePeriodWindow(start=start, end=end),
            )
        run_id_set = frozenset(run_ids)
        # Scan canonical per-call usage rows within the same window
        # then filter to the matched run IDs in-memory. Window is the
        # same one used by the rollup so semantics match across the
        # ``/v1/usage`` family.
        call_rows = await persistence.query_model_call_usage_for_range(
            org_id=org_id,
            start=start,
            end=end,
        )
        agent_rows: tuple[RuntimeModelCallUsageRecord, ...] = tuple(
            row for row in call_rows if row.run_id in run_id_set
        )
        return cls._aggregate(
            agent_id=agent_id,
            window=UsagePeriodWindow(start=start, end=end),
            rows=agent_rows,
        )

    @classmethod
    def _aggregate(
        cls,
        *,
        agent_id: str,
        window: UsagePeriodWindow,
        rows: tuple[RuntimeModelCallUsageRecord, ...],
    ) -> AgentUsageResponse:
        """Sum tokens + cost from per-call rows and bucket cost by purpose.

        Pure function: every input is an already-fetched record so
        the aggregation is trivially testable without I/O. ``Purpose``
        keys come straight off the record column — no extension, no
        new enum value introduced (TU-1 single-tracker invariant).
        """

        token_in = 0
        token_out = 0
        cost_total = 0
        cost_by_purpose: dict[str, int] = {}
        seen_runs: set[str] = set()
        for row in rows:
            token_in += int(row.input_tokens)
            token_out += int(row.output_tokens)
            seen_runs.add(row.run_id)
            cost_micro = row.cost_micro_usd
            if cost_micro is None:
                continue
            cost_total += int(cost_micro)
            purpose_key = row.purpose or "main"
            cost_by_purpose[purpose_key] = cost_by_purpose.get(purpose_key, 0) + int(
                cost_micro
            )
        return AgentUsageResponse(
            agent_id=agent_id,
            period=window,
            run_count=len(seen_runs),
            token_in=token_in,
            token_out=token_out,
            cost_usd_micro=cost_total,
            cost_breakdown_by_purpose=cost_by_purpose,
        )


class AgentUsageApiRouter:
    """Build the ``/v1/usage/org/agent/{agent_id}`` router.

    Mounted as a sibling under the existing ``/v1/usage`` router
    family so the same ``RUNTIME_USE`` scope guard applies. Per the
    Agents PRD §4.9 ACL: any caller able to read the agent may query
    its usage; aggregates are tenant-scoped. The agent-readability
    check itself lives in the backend facade — this route is the
    raw aggregation surface consumed by the facade after that check.
    """

    @classmethod
    def create_router(cls) -> APIRouter:
        """Return the router carrying the per-agent usage route."""

        router = APIRouter(
            prefix="/v1/usage",
            tags=["usage", "agents"],
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        router.add_api_route(
            "/org/agent/{agent_id}",
            AgentUsageRoutes.usage_org_agent,
            methods=["GET"],
            response_model=AgentUsageResponse,
            name=Keys.RouteName.USAGE_ORG_AGENT,
        )
        return router
