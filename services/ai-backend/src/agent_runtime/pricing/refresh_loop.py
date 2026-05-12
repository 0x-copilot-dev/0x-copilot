"""Background loop that periodically re-ingests pricing data from the configured primary source.

Runs every ``PRICING_REFRESH_INTERVAL_SECONDS`` (default 24 h). Per record: if no active
row exists, insert; if the active row matches, skip; if it differs and ``auto_apply`` is
enabled and the change is within the sanity bound (default ±25%), close and insert; if the
change exceeds the sanity bound, log ``action_taken="refused_sanity"`` and skip regardless
of ``auto_apply``. History rows are never modified. Opt-in via ``PRICING_REFRESH_ENABLED=true``.
When the deployment profile uses YAML as the primary source, the loop self-disables per tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

from agent_runtime.api.ports import PersistencePort
from agent_runtime.deployment.profile import (
    DeploymentProfile,
    DeploymentProfileLoader,
)
from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.composer import PricingComposer, PrimarySource
from agent_runtime.pricing.upsert_planner import (
    Disposition,
    PlannedAction,
    apply_actions,
    plan_actions,
    records_equivalent,
)


_LOGGER = logging.getLogger("agent_runtime.pricing.refresh_loop")


class PricingRefreshLoopEnv:
    """Env-var keys + defaults for the refresh loop."""

    ENABLED: Final[str] = "PRICING_REFRESH_ENABLED"
    INTERVAL_SECONDS: Final[str] = "PRICING_REFRESH_INTERVAL_SECONDS"
    AUTO_APPLY: Final[str] = "PRICING_REFRESH_AUTO_APPLY"
    SANITY_THRESHOLD: Final[str] = "PRICING_REFRESH_SANITY_THRESHOLD"

    DEFAULT_INTERVAL_SECONDS: Final[float] = 86_400.0  # 24h
    DEFAULT_AUTO_APPLY: Final[bool] = False
    DEFAULT_SANITY_THRESHOLD: Final[Decimal] = Decimal("0.25")  # ±25%

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def env_decimal(cls, name: str, default: Decimal) -> Decimal:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return Decimal(raw)
        except Exception:
            return default


# Action taken per record on a refresh tick — surfaced via the
# ``action_taken`` field on the ``pricing.upstream_changed`` log line.
ActionTaken = Literal["applied", "inserted_new", "dry_run", "refused_sanity"]


class _RefreshOutcome:
    """One record's outcome on a refresh tick (in-memory, observable by tests)."""

    __slots__ = (
        "record",
        "existing",
        "max_fractional_change",
        "action_taken",
    )

    def __init__(
        self,
        *,
        record: ModelPricingRecord,
        existing: ModelPricingRecord | None,
        max_fractional_change: Decimal | None,
        action_taken: ActionTaken,
    ) -> None:
        self.record = record
        self.existing = existing
        self.max_fractional_change = max_fractional_change
        self.action_taken = action_taken

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.record.provider, self.record.model_name, self.record.region)


class PricingRefreshLoop:
    """Worker-hosted refresh of the LiteLLM-sourced pricing rows.

    Pattern parallel to :class:`runtime_worker.usage_rollup_loop.UsageRollupLoop`:
    constructed by the worker, ``start()`` kicks off the recurring task,
    ``stop()`` cancels and awaits cleanly. Best-effort: failures are
    logged and the loop continues to its next tick.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        deployment_profile: DeploymentProfile | None = None,
        interval_seconds: float | None = None,
        auto_apply: bool | None = None,
        sanity_threshold: Decimal | None = None,
        overrides_path: Path | None = None,
        litellm_data_path: Path | None = None,
    ) -> None:
        self._persistence = persistence
        self._profile = deployment_profile or _safe_load_profile()
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else PricingRefreshLoopEnv.env_float(
                PricingRefreshLoopEnv.INTERVAL_SECONDS,
                PricingRefreshLoopEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._auto_apply = (
            auto_apply
            if auto_apply is not None
            else PricingRefreshLoopEnv.env_bool(
                PricingRefreshLoopEnv.AUTO_APPLY,
                PricingRefreshLoopEnv.DEFAULT_AUTO_APPLY,
            )
        )
        self._sanity_threshold = (
            sanity_threshold
            if sanity_threshold is not None
            else PricingRefreshLoopEnv.env_decimal(
                PricingRefreshLoopEnv.SANITY_THRESHOLD,
                PricingRefreshLoopEnv.DEFAULT_SANITY_THRESHOLD,
            )
        )
        self._overrides_path = overrides_path
        self._litellm_data_path = litellm_data_path
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Kick off the loop. Returns immediately."""

        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="pricing-refresh-loop")

    async def stop(self) -> None:
        """Signal the loop to exit and wait for it."""

        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return  # stop requested
            except TimeoutError:
                pass
            try:
                await self.refresh()
            except Exception:
                _LOGGER.warning("pricing.refresh_failed", exc_info=True)

    async def refresh(
        self,
        *,
        effective_from: datetime | None = None,
    ) -> tuple[_RefreshOutcome, ...]:
        """Run one tick of the refresh and return what happened per record.

        Public for tests. The worker entrypoint never calls this directly.

        ``effective_from`` overrides the stamp used for newly-composed
        records (default: ``datetime.now(timezone.utc)``). Tests inject
        a deterministic value so the ``lookup_pricing(at=...)`` window
        is stable.
        """

        primary_source = self._resolve_primary_source()
        if primary_source != PricingComposer.PRIMARY_LITELLM:
            _LOGGER.info(
                "pricing.refresh_skipped_air_gapped",
                extra={"primary_source": primary_source},
            )
            return ()

        composed = PricingComposer.load(
            primary_source=primary_source,
            overrides_path=self._overrides_path,
            litellm_data_path=self._litellm_data_path,
            effective_from=effective_from or datetime.now(timezone.utc),
        )
        outcomes = await self._evaluate(composed)
        appliable = tuple(
            self._action_to_planned(o)
            for o in outcomes
            if o.action_taken in ("applied", "inserted_new")
        )
        if appliable:
            await apply_actions(self._persistence, appliable)
        return outcomes

    def _resolve_primary_source(self) -> PrimarySource:
        raw = self._profile.toggles.pricing_primary_source
        if raw not in (PricingComposer.PRIMARY_LITELLM, PricingComposer.PRIMARY_YAML):
            return PricingComposer.PRIMARY_LITELLM
        return raw  # type: ignore[return-value]

    async def _evaluate(
        self,
        composed: Iterable[ModelPricingRecord],
    ) -> tuple[_RefreshOutcome, ...]:
        # Use the same plan-stage the seed_pricing script uses so the
        # "no row" / "matches" / "differs" decision is in one place.
        plan = await plan_actions(self._persistence, composed)
        outcomes: list[_RefreshOutcome] = []
        for action in plan:
            outcomes.append(self._decide(action))
        return tuple(outcomes)

    def _decide(self, action: PlannedAction) -> _RefreshOutcome:
        if action.disposition == Disposition.NO_CHANGE:
            # Skip silently — stable rows produce no log noise.
            return _RefreshOutcome(
                record=action.record,
                existing=action.existing,
                max_fractional_change=Decimal(0),
                action_taken="dry_run",  # nothing to apply; not surfaced as a change
            )
        if action.disposition == Disposition.INSERT_NEW:
            _LOGGER.info(
                "pricing.upstream_added",
                extra={
                    "provider": action.record.provider,
                    "model_name": action.record.model_name,
                    "region": action.record.region,
                    "pricing_source": action.record.pricing_source,
                    "auto_apply": self._auto_apply,
                    "action_taken": "inserted_new" if self._auto_apply else "dry_run",
                },
            )
            return _RefreshOutcome(
                record=action.record,
                existing=None,
                max_fractional_change=None,
                action_taken="inserted_new" if self._auto_apply else "dry_run",
            )

        # CLOSE_AND_INSERT — compute per-rate-field magnitude and decide.
        assert action.existing is not None
        max_change = _max_fractional_change(
            existing=action.existing,
            new=action.record,
        )
        sanity_exceeded = max_change is not None and max_change > self._sanity_threshold
        if sanity_exceeded:
            action_taken: ActionTaken = "refused_sanity"
        elif self._auto_apply:
            action_taken = "applied"
        else:
            action_taken = "dry_run"

        _LOGGER.info(
            "pricing.upstream_changed",
            extra={
                "provider": action.record.provider,
                "model_name": action.record.model_name,
                "region": action.record.region,
                "old_input_per_1m_micro_usd": action.existing.input_per_1m_micro_usd,
                "new_input_per_1m_micro_usd": action.record.input_per_1m_micro_usd,
                "old_output_per_1m_micro_usd": action.existing.output_per_1m_micro_usd,
                "new_output_per_1m_micro_usd": action.record.output_per_1m_micro_usd,
                "old_cached_input_per_1m_micro_usd": action.existing.cached_input_per_1m_micro_usd,
                "new_cached_input_per_1m_micro_usd": action.record.cached_input_per_1m_micro_usd,
                "max_fractional_change": (
                    str(max_change) if max_change is not None else None
                ),
                "sanity_threshold": str(self._sanity_threshold),
                "auto_apply": self._auto_apply,
                "action_taken": action_taken,
            },
        )
        return _RefreshOutcome(
            record=action.record,
            existing=action.existing,
            max_fractional_change=max_change,
            action_taken=action_taken,
        )

    @staticmethod
    def _action_to_planned(outcome: _RefreshOutcome) -> PlannedAction:
        # Map the outcome back into a PlannedAction the applier consumes.
        if outcome.existing is None:
            return PlannedAction(
                record=outcome.record,
                existing=None,
                disposition=Disposition.INSERT_NEW,
            )
        return PlannedAction(
            record=outcome.record,
            existing=outcome.existing,
            disposition=Disposition.CLOSE_AND_INSERT,
        )


def _max_fractional_change(
    *,
    existing: ModelPricingRecord,
    new: ModelPricingRecord,
) -> Decimal | None:
    """Largest fractional rate change across the three rate fields.

    Returns ``None`` if no rate field has a meaningful comparison
    (i.e. every existing-vs-new pair is either equal, or the existing
    value is zero/None so a fraction can't be computed).
    """

    fields = (
        "input_per_1m_micro_usd",
        "output_per_1m_micro_usd",
        "cached_input_per_1m_micro_usd",
    )
    biggest: Decimal | None = None
    for field in fields:
        existing_value = getattr(existing, field)
        new_value = getattr(new, field)
        if existing_value is None or new_value is None:
            continue
        if existing_value == 0:
            continue
        change = abs(Decimal(new_value) - Decimal(existing_value)) / Decimal(
            abs(existing_value)
        )
        if biggest is None or change > biggest:
            biggest = change
    return biggest


def _safe_load_profile() -> DeploymentProfile:
    """Load deployment profile or synthesise a litellm default for tests."""

    try:
        return DeploymentProfileLoader.load()
    except Exception:
        # In test contexts the env-var-driven loader may not have a
        # profile set; the refresh loop should still be constructible.
        from agent_runtime.deployment.profile import DeploymentFeatureToggles

        return DeploymentProfile(
            name="development",
            toggles=DeploymentFeatureToggles(
                allow_embedded_provider_keys=True,
                allow_self_signup=True,
                allow_vendor_telemetry=True,
                default_retention_days=365,
                dev_auth_bypass_allowed=True,
                enforce_rls=False,
                require_field_level_encryption=False,
                require_kms_token_vault=False,
                siem_export_required=False,
                pricing_primary_source="litellm",
            ),
        )


# Used by the test where checking equivalence on a row's "active vs
# proposed" is convenient to express in test code.
def is_equivalent(
    a: ModelPricingRecord, b: ModelPricingRecord
) -> bool:  # pragma: no cover — re-export for tests
    return records_equivalent(a, b)
