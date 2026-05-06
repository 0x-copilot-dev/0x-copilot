"""Unit tests for the PR 7.2 connector-attribution resolver."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.observability.usage_attribution import UsageAttributionResolver


class _FakePersistence:
    """Minimal fake exposing only the method the resolver uses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.return_value: str | None = None
        self.raise_exc: Exception | None = None

    async def query_last_completed_tool_connector_slug(
        self,
        *,
        org_id: str,
        run_id: str,
        before: datetime,
    ) -> str | None:
        self.calls.append({"org_id": org_id, "run_id": run_id, "before": before})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.return_value


class TestUsageAttributionResolver:
    @pytest.mark.asyncio
    async def test_returns_persistence_value(self) -> None:
        fake = _FakePersistence()
        fake.return_value = "slack"
        resolver = UsageAttributionResolver(fake)  # type: ignore[arg-type]
        slug = await resolver.resolve(
            org_id="org_a",
            run_id="run-1",
            before=datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc),
        )
        assert slug == "slack"
        assert len(fake.calls) == 1
        assert fake.calls[0]["org_id"] == "org_a"
        assert fake.calls[0]["run_id"] == "run-1"

    @pytest.mark.asyncio
    async def test_none_when_no_completed_tool(self) -> None:
        fake = _FakePersistence()
        fake.return_value = None
        resolver = UsageAttributionResolver(fake)  # type: ignore[arg-type]
        slug = await resolver.resolve(
            org_id="org_a",
            run_id="run-1",
            before=datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc),
        )
        assert slug is None

    @pytest.mark.asyncio
    async def test_swallows_exceptions_and_returns_none(self) -> None:
        # Best-effort: a transient lookup failure must never break a run.
        fake = _FakePersistence()
        fake.raise_exc = RuntimeError("connection lost")
        resolver = UsageAttributionResolver(fake)  # type: ignore[arg-type]
        slug = await resolver.resolve(
            org_id="org_a",
            run_id="run-1",
            before=datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc),
        )
        assert slug is None


class TestInMemoryAttributionLookup:
    """The in-memory adapter's ``query_last_completed_tool_connector_slug``."""

    def _store(self):  # type: ignore[no-untyped-def]
        from runtime_adapters.in_memory.runtime_api_store import (
            InMemoryRuntimeApiStore,
        )

        return InMemoryRuntimeApiStore()

    def test_returns_most_recent_completion_before_cutoff(self) -> None:
        store = self._store()
        t0 = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
        store.tool_invocation_completions.append(("org_a", "run-1", "slack", t0))
        store.tool_invocation_completions.append(
            ("org_a", "run-1", "notion", t0 + timedelta(seconds=5))
        )
        slug = store.query_last_completed_tool_connector_slug(
            org_id="org_a",
            run_id="run-1",
            before=t0 + timedelta(seconds=10),
        )
        assert slug == "notion"

    def test_strict_before_filter(self) -> None:
        # The cutoff is strict — equal completed_at does NOT attribute.
        store = self._store()
        t0 = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
        store.tool_invocation_completions.append(("org_a", "run-1", "slack", t0))
        slug = store.query_last_completed_tool_connector_slug(
            org_id="org_a", run_id="run-1", before=t0
        )
        assert slug is None

    def test_other_run_excluded(self) -> None:
        store = self._store()
        t0 = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
        store.tool_invocation_completions.append(("org_a", "other-run", "slack", t0))
        slug = store.query_last_completed_tool_connector_slug(
            org_id="org_a", run_id="run-1", before=t0 + timedelta(minutes=1)
        )
        assert slug is None

    def test_other_org_excluded(self) -> None:
        store = self._store()
        t0 = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
        store.tool_invocation_completions.append(("other_org", "run-1", "slack", t0))
        slug = store.query_last_completed_tool_connector_slug(
            org_id="org_a", run_id="run-1", before=t0 + timedelta(minutes=1)
        )
        assert slug is None

    def test_empty_slug_treated_as_none(self) -> None:
        # An empty connector_slug ('') should not pretend to attribute —
        # the cold-turn (unattributed) sentinel is filtered out so the
        # lookup returns None.
        store = self._store()
        t0 = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
        store.tool_invocation_completions.append(("org_a", "run-1", "", t0))
        slug = store.query_last_completed_tool_connector_slug(
            org_id="org_a", run_id="run-1", before=t0 + timedelta(seconds=1)
        )
        assert slug is None
