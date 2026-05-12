"""Plan + apply pricing-record upserts idempotently (B3 / P12 Step 2).

The composer ([`composer.py`](composer.py)) decides which records *should* be in
the catalog. This module decides what actually changes when those records meet
the existing pricing-port state, so re-running the seed script is a no-op
whenever the catalog is already current.

Why this lives next to the composer rather than inside the script: the
script is a thin CLI shell; the planning logic is library code that wants
unit tests + reuse (e.g. by a future Phase 3 ``PricingRefreshLoop``).

The Postgres adapter's ``upsert_pricing`` requires the new record's
``effective_from`` to be strictly greater than the existing active row's.
The planner enforces that — bumping by one minute when the composer's
minute-floored timestamp collides with the existing row exactly.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Final

from agent_runtime.api.ports import PersistencePort
from agent_runtime.persistence.records import ModelPricingRecord


class Disposition:
    """What the planner decided to do with one composed record."""

    INSERT_NEW: Final[str] = "insert"
    CLOSE_AND_INSERT: Final[str] = "close_and_insert"
    NO_CHANGE: Final[str] = "no_change"


class PlannedAction:
    """One row of the planned upsert plan — public so tests can build it."""

    __slots__ = ("record", "existing", "disposition")

    def __init__(
        self,
        *,
        record: ModelPricingRecord,
        existing: ModelPricingRecord | None,
        disposition: str,
    ) -> None:
        self.record = record
        self.existing = existing
        self.disposition = disposition

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.record.provider, self.record.model_name, self.record.region)


def records_equivalent(a: ModelPricingRecord, b: ModelPricingRecord) -> bool:
    """True when ``a`` and ``b`` carry the same rate-bearing values.

    ``effective_from``, ``effective_until``, ``id``, and ``created_at``
    are ignored — they're time-keying / identity fields that aren't
    part of "what is the current price".
    """

    return (
        a.input_per_1m_micro_usd == b.input_per_1m_micro_usd
        and a.output_per_1m_micro_usd == b.output_per_1m_micro_usd
        and a.cached_input_per_1m_micro_usd == b.cached_input_per_1m_micro_usd
        and a.context_window_tokens == b.context_window_tokens
        and a.pricing_source == b.pricing_source
        and a.pricing_version == b.pricing_version
    )


async def plan_actions(
    persistence: PersistencePort,
    records: Iterable[ModelPricingRecord],
) -> list[PlannedAction]:
    """For each composed record, decide ``insert`` / ``close_and_insert`` / ``no_change``."""

    plan: list[PlannedAction] = []
    for record in records:
        existing = await persistence.lookup_pricing(
            provider=record.provider,
            model_name=record.model_name,
            region=record.region,
            at=record.effective_from,
        )
        if existing is None:
            plan.append(
                PlannedAction(
                    record=record,
                    existing=None,
                    disposition=Disposition.INSERT_NEW,
                )
            )
            continue
        if records_equivalent(existing, record):
            plan.append(
                PlannedAction(
                    record=record,
                    existing=existing,
                    disposition=Disposition.NO_CHANGE,
                )
            )
            continue
        # Values differ. The upsert path requires the new record's
        # ``effective_from`` to be strictly greater than the existing
        # active row's; otherwise the close-step won't fire and the
        # Postgres partial unique index rejects the insert. Bump if
        # needed (composer floors to minute, so a same-minute re-run
        # collides exactly).
        if record.effective_from <= existing.effective_from:
            record = record.model_copy(
                update={
                    "effective_from": existing.effective_from + timedelta(minutes=1)
                }
            )
        plan.append(
            PlannedAction(
                record=record,
                existing=existing,
                disposition=Disposition.CLOSE_AND_INSERT,
            )
        )
    return plan


async def apply_actions(
    persistence: PersistencePort,
    plan: Iterable[PlannedAction],
) -> None:
    for action in plan:
        if action.disposition == Disposition.NO_CHANGE:
            continue
        await persistence.upsert_pricing(action.record)


def summary_counts(plan: Iterable[PlannedAction]) -> dict[str, int]:
    counts: dict[str, int] = {
        Disposition.INSERT_NEW: 0,
        Disposition.CLOSE_AND_INSERT: 0,
        Disposition.NO_CHANGE: 0,
    }
    for action in plan:
        counts[action.disposition] = counts.get(action.disposition, 0) + 1
    return counts
