"""Liveness service — aggregates project-liveness reads (Phase 6.5 §3).

Single public method: ``LivenessService.is_project_alive(...)``. Everything
else (cache, upstream clients, ports) is an internal implementation detail
of the same module, and is exposed only for adapter injection in tests.

Aggregation strategy (§3.6):

  1. Read four sources in parallel via ``asyncio.gather(return_exceptions=True)``:
       * ai-backend.runs       — runs in ``{queued, running}``
       * ai-backend.approvals  — approvals in ``{pending}``
       * backend.routines      — routines in ``{active}``
       * backend.inbox         — inbox items in ``{unread, snoozed}``
  2. Build one ``LivenessDetail`` per source. A source that errored has
     ``error != None``, ``is_alive=False``, and is EXCLUDED from the
     top-level ``is_alive`` OR (§3.8 fail-open trade-off — documented).
  3. Cache for 2 seconds keyed on ``(tenant_id, project_id)``. ``cache_hit``
     is the cache-status pill; a true hit echoes the cached payload.

All upstream clients are injected via the constructor so tests can stub
them with deterministic adapters. The default adapters are:

  * ``AiBackendLivenessClient`` — httpx call to ``/v1/agent/runs`` and
    ``/v1/agent/approvals`` on the ai-backend internal surface.
  * ``RoutinesLivenessReader`` — wraps the in-process routines store.
  * ``InboxLivenessReader``    — wraps the in-process inbox store.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field


LivenessSourceName = Literal[
    "ai_backend.runs",
    "ai_backend.approvals",
    "backend.routines",
    "backend.inbox",
]


_DEFAULT_CACHE_TTL_SECONDS = 2.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LivenessDetail(BaseModel):
    """One row per upstream source the aggregator queried."""

    model_config = ConfigDict(extra="forbid")

    source: LivenessSourceName
    count: int = Field(ge=0)
    is_alive: bool
    error: str | None = None
    fetched_at: str


class LivenessReport(BaseModel):
    """Aggregated liveness across destinations, per-project."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    tenant_id: str
    is_alive: bool
    active_runs: int = Field(ge=0)
    pending_approvals: int = Field(ge=0)
    active_routines: int = Field(ge=0)
    in_flight_inbox: int = Field(ge=0)
    details: list[LivenessDetail]
    computed_at: str
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Upstream port shapes (Protocol = duck typing).
# ---------------------------------------------------------------------------


class _Counter(Protocol):
    """Anything that exposes ``async (tenant_id, project_id) -> int``."""

    async def __call__(self, tenant_id: str, project_id: str) -> int: ...


@dataclass
class AiBackendLivenessClient:
    """Counts active runs + pending approvals against ai-backend.

    The runtime exposes ``GET /v1/agent/runs?status=running,queued&project_id=…``
    on its internal surface. We treat 4xx/5xx + transport errors uniformly
    and let the service layer surface them in ``details[].error``.
    """

    base_url: str
    service_token: str
    transport_factory: Callable[[], httpx.AsyncClient] | None = None
    timeout_seconds: float = 5.0

    async def count_active_runs(self, tenant_id: str, project_id: str) -> int:
        return await self._count(
            "/v1/agent/runs",
            {"status": "running,queued", "project_id": project_id},
            tenant_id=tenant_id,
            count_field="active_runs",
        )

    async def count_pending_approvals(self, tenant_id: str, project_id: str) -> int:
        return await self._count(
            "/v1/agent/approvals",
            {"status": "pending", "project_id": project_id},
            tenant_id=tenant_id,
            count_field="pending_approvals",
        )

    async def _count(
        self,
        path: str,
        params: dict[str, str],
        *,
        tenant_id: str,
        count_field: str,
    ) -> int:
        headers = {
            "x-enterprise-service-token": self.service_token,
            "x-enterprise-org-id": tenant_id,
            # ai-backend requires a user header on the internal surface; we
            # use a sentinel that's tenant-scoped (no per-user filter here —
            # liveness is project-level per §3.6).
            "x-enterprise-user-id": "system.liveness",
        }
        client = (
            self.transport_factory()
            if self.transport_factory is not None
            else httpx.AsyncClient(timeout=self.timeout_seconds)
        )
        async with client as c:
            response = await c.get(
                f"{self.base_url}{path}", params=params, headers=headers
            )
        if response.status_code >= 400:
            raise RuntimeError(f"ai-backend {path} -> {response.status_code}")
        payload = response.json()
        # Accept both ``{items: [...]}`` and ``{count: N}`` for forward compat.
        if isinstance(payload, dict):
            if count_field in payload and isinstance(payload[count_field], int):
                return payload[count_field]
            if "count" in payload and isinstance(payload["count"], int):
                return payload["count"]
            items = payload.get("items")
            if isinstance(items, list):
                return len(items)
        return 0


@dataclass
class RoutinesLivenessReader:
    """Counts routines in ``status='active'`` for the project.

    Reads through the in-process routines store directly (zero network
    hop — backend owns this table; §3.2). Tenant-first filter.
    """

    routines_store: object  # InMemoryRoutinesStore or postgres adapter

    async def __call__(self, tenant_id: str, project_id: str) -> int:
        # Use list_routines with the project filter the store already
        # supports (see routines/store.py list_routines signature).
        rows, _ = self.routines_store.list_routines(
            tenant_id=tenant_id,
            project_ids=(project_id,),
            statuses=("active",),
            limit=1000,
        )
        return len(rows)


@dataclass
class InboxLivenessReader:
    """Counts inbox items in ``{unread, snoozed}`` for the project.

    Reads via the in-process inbox store. Note: this counts ACROSS all
    recipients in the tenant, because liveness is a project property
    (§3.6 cache key doesn't include user_id).
    """

    inbox_store: object

    async def __call__(self, tenant_id: str, project_id: str) -> int:
        rows = self.inbox_store.list_project_member_items(
            tenant_id=tenant_id, project_ids=(project_id,)
        )
        return sum(1 for r in rows if r.state in ("unread", "snoozed"))


# ---------------------------------------------------------------------------
# Cache (in-process, TTL-only — §3.7).
# ---------------------------------------------------------------------------


@dataclass
class _TtlCache:
    ttl_seconds: float
    _entries: dict[tuple[str, str], tuple[float, LivenessReport]] = field(
        default_factory=dict
    )

    def get(self, tenant_id: str, project_id: str) -> LivenessReport | None:
        key = (tenant_id, project_id)
        entry = self._entries.get(key)
        if entry is None:
            return None
        ts, report = entry
        if time.monotonic() - ts > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        return report

    def put(self, tenant_id: str, project_id: str, report: LivenessReport) -> None:
        self._entries[(tenant_id, project_id)] = (time.monotonic(), report)


# ---------------------------------------------------------------------------
# Service — single public method.
# ---------------------------------------------------------------------------


class LivenessService:
    """The single source of truth for "is anything running for project X?".

    Public surface (§3.5): one method, ``is_project_alive``. Any other
    public method here is a bug — anything beyond aggregation belongs
    elsewhere (§3.9).
    """

    _SOURCE_RUNS: LivenessSourceName = "ai_backend.runs"
    _SOURCE_APPROVALS: LivenessSourceName = "ai_backend.approvals"
    _SOURCE_ROUTINES: LivenessSourceName = "backend.routines"
    _SOURCE_INBOX: LivenessSourceName = "backend.inbox"

    def __init__(
        self,
        *,
        ai_backend_client: AiBackendLivenessClient,
        routines_reader: _Counter,
        inbox_reader: _Counter,
        cache_ttl_seconds: float | None = None,
    ) -> None:
        ttl = cache_ttl_seconds
        if ttl is None:
            raw = os.environ.get("LIVENESS_CACHE_TTL_SECONDS", "").strip()
            try:
                ttl = float(raw) if raw else _DEFAULT_CACHE_TTL_SECONDS
            except ValueError:
                ttl = _DEFAULT_CACHE_TTL_SECONDS
        self._ai = ai_backend_client
        self._routines = routines_reader
        self._inbox = inbox_reader
        self._cache = _TtlCache(ttl_seconds=ttl)

    async def is_project_alive(
        self,
        *,
        tenant_id: str,
        project_id: str,
        force_refresh: bool = False,
    ) -> LivenessReport:
        if not force_refresh:
            cached = self._cache.get(tenant_id, project_id)
            if cached is not None:
                return cached.model_copy(update={"cache_hit": True})

        tasks: list[Awaitable[int]] = [
            self._ai.count_active_runs(tenant_id, project_id),
            self._ai.count_pending_approvals(tenant_id, project_id),
            self._routines(tenant_id, project_id),
            self._inbox(tenant_id, project_id),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        sources = (
            self._SOURCE_RUNS,
            self._SOURCE_APPROVALS,
            self._SOURCE_ROUTINES,
            self._SOURCE_INBOX,
        )
        details = [_build_detail(src, res) for src, res in zip(sources, results)]
        report = LivenessReport(
            project_id=project_id,
            tenant_id=tenant_id,
            active_runs=details[0].count,
            pending_approvals=details[1].count,
            active_routines=details[2].count,
            in_flight_inbox=details[3].count,
            is_alive=any(d.is_alive for d in details if d.error is None),
            details=details,
            computed_at=_utcnow_iso(),
            cache_hit=False,
        )
        self._cache.put(tenant_id, project_id, report)
        return report


def _build_detail(source: LivenessSourceName, result: object) -> LivenessDetail:
    """Map ``asyncio.gather(return_exceptions=True)`` output to a detail row."""

    if isinstance(result, BaseException):
        return LivenessDetail(
            source=source,
            count=0,
            is_alive=False,
            error=type(result).__name__ + ": " + str(result),
            fetched_at=_utcnow_iso(),
        )
    count = int(result) if isinstance(result, int) else 0
    return LivenessDetail(
        source=source,
        count=count,
        is_alive=count > 0,
        error=None,
        fetched_at=_utcnow_iso(),
    )


__all__ = [
    "AiBackendLivenessClient",
    "InboxLivenessReader",
    "LivenessDetail",
    "LivenessReport",
    "LivenessService",
    "LivenessSourceName",
    "RoutinesLivenessReader",
]
