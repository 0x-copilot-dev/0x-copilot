"""Usage query service: shared period parsing + rollup math (B4).

Used by both ``UsageApiRoutes`` (synchronous reads) and the rollup loop
(``runtime_worker/usage_rollup_loop.py``). Period semantics live in one
place so ``today`` means the same thing in the API response window and
in the rollup table refresh window.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from agent_runtime.persistence.records import (
    CompressionEventRecord,
    ModelPricingRecord,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    UsageDailyConnectorRow,
    UsageDailyOrgRow,
    UsageDailyUserRow,
)
from runtime_api.schemas.conversations import (
    ContextBreakdown,
    ContextCallRow,
    ContextCompressionRow,
    ContextCurrentSlice,
    ContextSubagentRow,
    ContextWindowSummary,
    ConversationContextResponse,
)


UsagePeriod = Literal["today", "7d", "30d", "month"]


class UsageQueryService:
    """Period-to-window translation + rollup arithmetic helpers.

    Stateless: every method is a classmethod or staticmethod. Lives in
    ``agent_runtime.api`` so both HTTP routes and the rollup worker can
    import it without crossing into runtime_worker (which would create a
    layering inversion: api shouldn't depend on worker).
    """

    @classmethod
    def parse_period(
        cls, period: UsagePeriod, *, now: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Return ``(start, end)`` UTC range for ``period``.

        ``start`` is inclusive, ``end`` is exclusive. ``now`` is overridable
        for tests so a frozen-time fixture produces deterministic output.
        """

        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        end = now
        if period == "today":
            start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        elif period == "7d":
            start = datetime.combine(
                (now - timedelta(days=6)).date(), time.min, tzinfo=timezone.utc
            )
        elif period == "30d":
            start = datetime.combine(
                (now - timedelta(days=29)).date(), time.min, tzinfo=timezone.utc
            )
        elif period == "month":
            start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        else:
            raise ValueError(f"unsupported usage period: {period}")
        return start, end

    @classmethod
    def days_between(cls, start: datetime, end: datetime) -> tuple[date, ...]:
        """Return every UTC date covered by ``[start, end]`` inclusive."""

        if start.date() > end.date():
            return ()
        days: list[date] = []
        cursor = start.date()
        while cursor <= end.date():
            days.append(cursor)
            cursor += timedelta(days=1)
        return tuple(days)

    @classmethod
    def rollup_user_rows(
        cls,
        rows: Iterable[RuntimeRunUsageRecord],
        *,
        refreshed_at: datetime,
    ) -> tuple[UsageDailyUserRow, ...]:
        """Aggregate per-run rows into per-user-per-model-per-day rollups.

        Excludes rows with ``pii_purged_at IS NOT NULL`` from per-user
        aggregates so a deleted user's history doesn't leak into their
        own /usage view (B4 spec §3.2 unit-test bullet).
        """

        buckets: dict[
            tuple[str, str, date, str, str],
            _RollupBucket,
        ] = defaultdict(_RollupBucket)
        for row in rows:
            if row.pii_purged_at is not None:
                continue
            day = row.completed_at.date()
            key = (
                row.org_id,
                row.user_id,
                day,
                row.model_provider,
                row.model_name,
            )
            buckets[key].add(row)
        return tuple(
            bucket.to_user_row(
                org_id=key[0],
                user_id=key[1],
                day=key[2],
                model_provider=key[3],
                model_name=key[4],
                refreshed_at=refreshed_at,
            )
            for key, bucket in buckets.items()
        )

    @classmethod
    def rollup_connector_rows(
        cls,
        rows: Iterable[RuntimeModelCallUsageRecord],
        *,
        run_user_lookup: Mapping[str, str],
        refreshed_at: datetime,
    ) -> tuple[UsageDailyConnectorRow, ...]:
        """Aggregate per-LLM-call rows into per-org-per-connector-per-day
        rollups (PR 7.2).

        ``run_user_lookup`` maps ``run_id -> user_id`` for the rows in
        scope so the rollup can compute ``distinct_users`` without
        denormalising user_id onto every per-call row. ``connector_slug``
        coalesces ``None`` to the empty string for the "(unattributed)"
        bucket — the natural-key PK does not allow ``NULL``.
        """

        buckets: dict[
            tuple[str, date, str],
            _ConnectorRollupBucket,
        ] = defaultdict(_ConnectorRollupBucket)
        run_counts: dict[tuple[str, date, str], set[str]] = defaultdict(set)
        for row in rows:
            day = row.created_at.date()
            slug = row.connector_slug or ""
            key = (row.org_id, day, slug)
            buckets[key].add(row, run_id=row.run_id)
            run_counts[key].add(row.run_id)
        result: list[UsageDailyConnectorRow] = []
        for key, bucket in buckets.items():
            user_ids = {
                run_user_lookup[run_id]
                for run_id in run_counts[key]
                if run_id in run_user_lookup
            }
            result.append(
                bucket.to_connector_row(
                    org_id=key[0],
                    day=key[1],
                    connector_slug=key[2],
                    distinct_users=len(user_ids),
                    refreshed_at=refreshed_at,
                )
            )
        return tuple(result)

    @classmethod
    def rollup_org_rows(
        cls,
        rows: Iterable[RuntimeRunUsageRecord],
        *,
        refreshed_at: datetime,
    ) -> tuple[UsageDailyOrgRow, ...]:
        """Aggregate per-run rows into per-org-per-model-per-day rollups.

        Per-org includes PII-purged rows in totals (so billing aggregates
        survive deletion); the per-user table excludes them.
        """

        buckets: dict[
            tuple[str, date, str, str],
            _OrgRollupBucket,
        ] = defaultdict(_OrgRollupBucket)
        for row in rows:
            day = row.completed_at.date()
            key = (row.org_id, day, row.model_provider, row.model_name)
            buckets[key].add(row)
        return tuple(
            bucket.to_org_row(
                org_id=key[0],
                day=key[1],
                model_provider=key[2],
                model_name=key[3],
                refreshed_at=refreshed_at,
            )
            for key, bucket in buckets.items()
        )

    @classmethod
    def sum_totals(
        cls,
        rows: Sequence[Mapping[str, object]],
        *,
        keys: tuple[str, ...] = (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "total_tokens",
        ),
    ) -> dict[str, int]:
        """Sum named integer columns across a sequence of rollup rows."""

        totals = {key: 0 for key in keys}
        for row in rows:
            for key in keys:
                value = row.get(key)
                if isinstance(value, int):
                    totals[key] += value
        return totals


class ConversationContextBuilder:
    """Pure builder for the ``/context`` slash-command response (B5).

    Stateless: given the latest run-usage row, the per-call rows, the
    compression events, and the model's pricing snapshot, returns the
    fully-populated :class:`ConversationContextResponse`. Every input is
    an already-fetched record, so the builder is trivially testable
    without any I/O.

    Headroom is computed server-side as an integer percent so the UI
    never re-derives floats. ``available_tokens`` and ``headroom_pct``
    are ``None`` whenever the model has no pricing entry (signaling
    "context window unknown" — the UI renders an "unknown" gauge).
    """

    @classmethod
    def build(
        cls,
        *,
        provider: str,
        model_name: str,
        latest_run: RuntimeRunUsageRecord | None,
        per_call_rows: Sequence[RuntimeModelCallUsageRecord],
        compression_events: Sequence[CompressionEventRecord],
        pricing: ModelPricingRecord | None,
    ) -> ConversationContextResponse:
        context_window = (
            pricing.context_window_tokens
            if pricing is not None and pricing.context_window_tokens is not None
            else None
        )
        model_summary = ContextWindowSummary(
            provider=provider,
            name=model_name,
            context_window_tokens=context_window,
        )
        if latest_run is None:
            return ConversationContextResponse(
                model=model_summary,
                current=ContextCurrentSlice(),
                breakdown=ContextBreakdown(),
            )

        used = latest_run.input_tokens + latest_run.cached_input_tokens
        if context_window is None:
            available = None
            headroom = None
        else:
            available = max(0, context_window - used)
            headroom = (
                int(available * 100 // context_window) if context_window > 0 else None
            )
            # Clamp to the public schema's [0, 100] range — over-window
            # cases (estimator drift) report 0.
            if headroom is not None:
                headroom = max(0, min(100, headroom))

        current = ContextCurrentSlice(
            last_run_id=latest_run.run_id,
            input_tokens=latest_run.input_tokens,
            output_tokens=latest_run.output_tokens,
            cached_input_tokens=latest_run.cached_input_tokens,
            available_tokens=available,
            headroom_pct=headroom,
        )

        by_call = tuple(
            ContextCallRow(
                event_id=row.id,
                model_name=row.model_name,
                input=row.input_tokens,
                output=row.output_tokens,
                cached_input=row.cached_input_tokens,
                task_id=row.task_id,
            )
            for row in per_call_rows
        )
        by_subagent = cls._collapse_by_subagent(per_call_rows)
        compression = tuple(
            ContextCompressionRow(
                before=event.before_tokens,
                after=event.after_tokens,
                strategy=event.strategy,
                at=event.created_at,
            )
            for event in compression_events
        )
        return ConversationContextResponse(
            model=model_summary,
            current=current,
            breakdown=ContextBreakdown(
                by_call=by_call,
                by_subagent=by_subagent,
                compression_events=compression,
            ),
        )

    @classmethod
    def _collapse_by_subagent(
        cls, rows: Sequence[RuntimeModelCallUsageRecord]
    ) -> tuple[ContextSubagentRow, ...]:
        buckets: dict[str, dict[str, int]] = {}
        for row in rows:
            subagent_id = row.subagent_id
            if not subagent_id:
                continue
            bucket = buckets.setdefault(subagent_id, {"total": 0, "call_count": 0})
            bucket["total"] += row.total_tokens
            bucket["call_count"] += 1
        return tuple(
            ContextSubagentRow(
                subagent_id=subagent_id,
                # No subagent registry yet — use the id as the display
                # name. When the registry lands the builder takes a
                # ``names: dict[str, str]`` dependency and looks up.
                name=subagent_id,
                total=bucket["total"],
                call_count=bucket["call_count"],
            )
            for subagent_id, bucket in sorted(
                buckets.items(),
                key=lambda item: item[1]["total"],
                reverse=True,
            )
        )


class _RollupBucket:
    """Mutable accumulator for per-user-per-model-per-day rollups."""

    def __init__(self) -> None:
        self.runs_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_input_tokens = 0
        self.total_tokens = 0
        self.cost_micro_usd: int | None = None

    def add(self, row: RuntimeRunUsageRecord) -> None:
        self.runs_count += 1
        self.input_tokens += row.input_tokens
        self.output_tokens += row.output_tokens
        self.cached_input_tokens += row.cached_input_tokens
        self.total_tokens += row.total_tokens
        if row.cost_micro_usd is not None:
            self.cost_micro_usd = (self.cost_micro_usd or 0) + row.cost_micro_usd

    def to_user_row(
        self,
        *,
        org_id: str,
        user_id: str,
        day: date,
        model_provider: str,
        model_name: str,
        refreshed_at: datetime,
    ) -> UsageDailyUserRow:
        return UsageDailyUserRow(
            org_id=org_id,
            user_id=user_id,
            day=datetime.combine(day, time.min, tzinfo=timezone.utc),
            model_provider=model_provider,
            model_name=model_name,
            runs_count=self.runs_count,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            total_tokens=self.total_tokens,
            cost_micro_usd=self.cost_micro_usd,
            refreshed_at=refreshed_at,
        )


class _ConnectorRollupBucket:
    """Accumulator for per-org-per-connector-per-day rollups (PR 7.2).

    Mirrors ``_RollupBucket`` but consumes per-LLM-call rows directly
    (the run-level rollup buckets only see run-aggregates, which can't
    be split by connector since one run typically spans connectors).
    ``runs_count`` counts distinct runs in this bucket.
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_input_tokens = 0
        self.total_tokens = 0
        self.cost_micro_usd: int | None = None
        self._run_ids: set[str] = set()

    def add(self, row: RuntimeModelCallUsageRecord, *, run_id: str) -> None:
        self.input_tokens += row.input_tokens
        self.output_tokens += row.output_tokens
        self.cached_input_tokens += row.cached_input_tokens
        self.total_tokens += row.total_tokens
        if row.cost_micro_usd is not None:
            self.cost_micro_usd = (self.cost_micro_usd or 0) + row.cost_micro_usd
        self._run_ids.add(run_id)

    def to_connector_row(
        self,
        *,
        org_id: str,
        day: date,
        connector_slug: str,
        distinct_users: int,
        refreshed_at: datetime,
    ) -> UsageDailyConnectorRow:
        return UsageDailyConnectorRow(
            org_id=org_id,
            day=datetime.combine(day, time.min, tzinfo=timezone.utc),
            connector_slug=connector_slug,
            runs_count=len(self._run_ids),
            distinct_users=distinct_users,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            total_tokens=self.total_tokens,
            cost_micro_usd=self.cost_micro_usd,
            refreshed_at=refreshed_at,
        )


class _OrgRollupBucket(_RollupBucket):
    """Mutable accumulator that also tracks distinct user count."""

    def __init__(self) -> None:
        super().__init__()
        self._user_ids: set[str] = set()

    def add(self, row: RuntimeRunUsageRecord) -> None:  # type: ignore[override]
        super().add(row)
        self._user_ids.add(row.user_id)

    def to_org_row(
        self,
        *,
        org_id: str,
        day: date,
        model_provider: str,
        model_name: str,
        refreshed_at: datetime,
    ) -> UsageDailyOrgRow:
        return UsageDailyOrgRow(
            org_id=org_id,
            day=datetime.combine(day, time.min, tzinfo=timezone.utc),
            model_provider=model_provider,
            model_name=model_name,
            runs_count=self.runs_count,
            distinct_users=len(self._user_ids),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            total_tokens=self.total_tokens,
            cost_micro_usd=self.cost_micro_usd,
            refreshed_at=refreshed_at,
        )
