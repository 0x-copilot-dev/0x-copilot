"""Tests for :mod:`backend_app.liveness.service` — Phase 6.5 §3 / §10.1.

Covers:

* Aggregation correctness — counts honored across all 4 sources.
* All-clear — every source returns 0; ``is_alive=False``.
* Cross-tenant — a project in tenant T1 queried with tenant T2 yields
  zero counts (in-process API; the route layer adds the 404 wrapper).
* Partial failure — one source raises → ``details[].error`` populated,
  ``is_alive`` reflects the others; report STILL returns 200.
* All sources error → ``is_alive=False``, every detail has ``error``.
* 2s cache — two calls share ``computed_at``; second has ``cache_hit=True``;
  expired entries refresh.
* ``force_refresh=True`` bypasses the cache.
* Read-only — calling N times never mutates the upstream state snapshot.

This service is `async`; we drive it through ``asyncio.run`` per the
in-repo convention (``test_home_sse.py``). No ``pytest-asyncio`` dep.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from backend_app.liveness.service import (
    LivenessReport,
    LivenessService,
)


# ---------------------------------------------------------------------------
# Test doubles — minimal stand-ins for the upstream ports.
# ---------------------------------------------------------------------------


@dataclass
class _FakeAiClient:
    runs_by_pair: dict[tuple[str, str], int] = field(default_factory=dict)
    approvals_by_pair: dict[tuple[str, str], int] = field(default_factory=dict)
    raise_for_runs: bool = False
    raise_for_approvals: bool = False
    call_count: int = 0

    async def count_active_runs(self, tenant_id: str, project_id: str) -> int:
        self.call_count += 1
        if self.raise_for_runs:
            raise RuntimeError("simulated runs upstream error")
        return self.runs_by_pair.get((tenant_id, project_id), 0)

    async def count_pending_approvals(self, tenant_id: str, project_id: str) -> int:
        self.call_count += 1
        if self.raise_for_approvals:
            raise RuntimeError("simulated approvals upstream error")
        return self.approvals_by_pair.get((tenant_id, project_id), 0)


@dataclass
class _FakeCounter:
    """Async callable matching the ``_Counter`` Protocol."""

    by_pair: dict[tuple[str, str], int] = field(default_factory=dict)
    raise_error: bool = False

    async def __call__(self, tenant_id: str, project_id: str) -> int:
        if self.raise_error:
            raise RuntimeError("simulated counter error")
        return self.by_pair.get((tenant_id, project_id), 0)


def _build_service(
    **overrides: Any,
) -> tuple[LivenessService, _FakeAiClient, _FakeCounter, _FakeCounter]:
    ai = overrides.get("ai") or _FakeAiClient()
    routines = overrides.get("routines") or _FakeCounter()
    inbox = overrides.get("inbox") or _FakeCounter()
    ttl = overrides.get("cache_ttl_seconds", 2.0)
    svc = LivenessService(
        ai_backend_client=ai,
        routines_reader=routines,
        inbox_reader=inbox,
        cache_ttl_seconds=ttl,
    )
    return svc, ai, routines, inbox


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_happy_path_counts_propagated(self) -> None:
        ai = _FakeAiClient(
            runs_by_pair={("org_a", "prj_1"): 2},
            approvals_by_pair={("org_a", "prj_1"): 1},
        )
        routines = _FakeCounter(by_pair={("org_a", "prj_1"): 3})
        inbox = _FakeCounter(by_pair={("org_a", "prj_1"): 5})
        svc, *_ = _build_service(ai=ai, routines=routines, inbox=inbox)

        report = _run(svc.is_project_alive(tenant_id="org_a", project_id="prj_1"))
        assert report.is_alive is True
        assert report.active_runs == 2
        assert report.pending_approvals == 1
        assert report.active_routines == 3
        assert report.in_flight_inbox == 5
        assert {d.source for d in report.details} == {
            "ai_backend.runs",
            "ai_backend.approvals",
            "backend.routines",
            "backend.inbox",
        }
        assert all(d.error is None for d in report.details)

    def test_all_clear(self) -> None:
        svc, *_ = _build_service()
        report = _run(svc.is_project_alive(tenant_id="org_a", project_id="prj_1"))
        assert report.is_alive is False
        assert report.active_runs == 0
        assert report.pending_approvals == 0
        assert report.active_routines == 0
        assert report.in_flight_inbox == 0


class TestTenantIsolation:
    def test_cross_tenant_returns_zero(self) -> None:
        ai = _FakeAiClient(runs_by_pair={("org_a", "prj_1"): 5})
        routines = _FakeCounter(by_pair={("org_a", "prj_1"): 4})
        inbox = _FakeCounter(by_pair={("org_a", "prj_1"): 3})
        svc, *_ = _build_service(ai=ai, routines=routines, inbox=inbox)

        # Querying with the wrong tenant yields zero counts (the upstream
        # fakes are keyed by (tenant, project), mirroring how the real
        # adapters filter on tenant_id).
        report = _run(svc.is_project_alive(tenant_id="org_b", project_id="prj_1"))
        assert report.is_alive is False
        assert report.active_runs == 0
        assert report.active_routines == 0
        assert report.in_flight_inbox == 0


class TestPartialFailure:
    def test_one_source_errors_others_honored(self) -> None:
        ai = _FakeAiClient(
            runs_by_pair={("org_a", "prj_1"): 2},
            approvals_by_pair={("org_a", "prj_1"): 1},
            raise_for_runs=True,
        )
        routines = _FakeCounter(by_pair={("org_a", "prj_1"): 3})
        inbox = _FakeCounter(by_pair={("org_a", "prj_1"): 5})
        svc, *_ = _build_service(ai=ai, routines=routines, inbox=inbox)

        report = _run(svc.is_project_alive(tenant_id="org_a", project_id="prj_1"))
        runs_detail = next(d for d in report.details if d.source == "ai_backend.runs")
        assert runs_detail.error is not None
        assert runs_detail.is_alive is False
        assert runs_detail.count == 0
        # Others survived.
        assert report.active_routines == 3
        assert report.in_flight_inbox == 5
        assert report.is_alive is True

    def test_all_sources_error_returns_200_with_is_alive_false(self) -> None:
        ai = _FakeAiClient(raise_for_runs=True, raise_for_approvals=True)
        routines = _FakeCounter(raise_error=True)
        inbox = _FakeCounter(raise_error=True)
        svc, *_ = _build_service(ai=ai, routines=routines, inbox=inbox)

        report = _run(svc.is_project_alive(tenant_id="org_a", project_id="prj_1"))
        assert all(d.error is not None for d in report.details)
        assert report.is_alive is False


class TestCache:
    def test_2s_cache_hit_within_ttl(self) -> None:
        ai = _FakeAiClient(runs_by_pair={("org_a", "prj_1"): 2})
        svc, ai_ref, *_ = _build_service(ai=ai, cache_ttl_seconds=2.0)

        async def exercise() -> tuple[LivenessReport, LivenessReport, int]:
            first = await svc.is_project_alive(tenant_id="org_a", project_id="prj_1")
            second = await svc.is_project_alive(tenant_id="org_a", project_id="prj_1")
            return first, second, ai_ref.call_count

        first, second, call_count = _run(exercise())
        assert second.cache_hit is True
        assert first.computed_at == second.computed_at
        # Fan-out happened exactly once (2 ai calls = runs + approvals).
        assert call_count == 2

    def test_force_refresh_bypasses_cache(self) -> None:
        ai = _FakeAiClient(runs_by_pair={("org_a", "prj_1"): 2})
        svc, ai_ref, *_ = _build_service(ai=ai, cache_ttl_seconds=10.0)

        async def exercise() -> tuple[LivenessReport, int, int]:
            await svc.is_project_alive(tenant_id="org_a", project_id="prj_1")
            prev = ai_ref.call_count
            report = await svc.is_project_alive(
                tenant_id="org_a", project_id="prj_1", force_refresh=True
            )
            return report, prev, ai_ref.call_count

        report, prev, after = _run(exercise())
        assert report.cache_hit is False
        assert after > prev

    def test_cache_expires_after_ttl(self) -> None:
        ai = _FakeAiClient(runs_by_pair={("org_a", "prj_1"): 2})
        svc, *_ = _build_service(ai=ai, cache_ttl_seconds=0.05)

        async def exercise() -> LivenessReport:
            await svc.is_project_alive(tenant_id="org_a", project_id="prj_1")
            await asyncio.sleep(0.1)
            return await svc.is_project_alive(tenant_id="org_a", project_id="prj_1")

        report = _run(exercise())
        assert report.cache_hit is False


class TestReadOnly:
    def test_calls_do_not_mutate_upstream_counters(self) -> None:
        ai = _FakeAiClient(runs_by_pair={("org_a", "prj_1"): 2})
        routines = _FakeCounter(by_pair={("org_a", "prj_1"): 3})
        inbox = _FakeCounter(by_pair={("org_a", "prj_1"): 5})
        snapshot_runs = dict(ai.runs_by_pair)
        snapshot_routines = dict(routines.by_pair)
        snapshot_inbox = dict(inbox.by_pair)
        svc, *_ = _build_service(ai=ai, routines=routines, inbox=inbox)

        async def exercise() -> None:
            for _ in range(5):
                await svc.is_project_alive(
                    tenant_id="org_a",
                    project_id="prj_1",
                    force_refresh=True,
                )

        _run(exercise())

        assert ai.runs_by_pair == snapshot_runs
        assert routines.by_pair == snapshot_routines
        assert inbox.by_pair == snapshot_inbox


class TestReportShape:
    def test_report_is_pydantic_model(self) -> None:
        svc, *_ = _build_service()
        report = _run(svc.is_project_alive(tenant_id="org_a", project_id="prj_1"))
        assert isinstance(report, LivenessReport)
        dumped = report.model_dump()
        # Wire shape mirrors api-types LivenessReport.
        assert set(dumped.keys()) >= {
            "project_id",
            "tenant_id",
            "is_alive",
            "active_runs",
            "pending_approvals",
            "active_routines",
            "in_flight_inbox",
            "details",
            "computed_at",
            "cache_hit",
        }
