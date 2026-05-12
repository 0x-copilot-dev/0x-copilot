"""Seed (or refresh) the runtime pricing catalog from composed sources.

Single deploy-time entry point for pushing ``ModelPricingRecord`` rows
into the runtime persistence layer. Thin CLI shell around the library
logic in :mod:`agent_runtime.pricing.composer` (which records to seed)
and :mod:`agent_runtime.pricing.upsert_planner` (idempotent diff +
apply against the existing rows).

Designed to be idempotent. Re-running with the same composed inputs is
a no-op per ``(provider, model_name, region)`` key: the planner
compares each incoming record against the currently-active row before
calling :meth:`PersistencePort.upsert_pricing` and skips when every
rate field matches.

Defaults to **preview mode**. Pass ``--apply`` to actually write.

Usage::

    # Preview what would change (default; never writes).
    python services/ai-backend/scripts/usage/seed_pricing.py

    # Actually upsert.
    python services/ai-backend/scripts/usage/seed_pricing.py --apply

    # Air-gapped (read seeds/ instead of LiteLLM).
    python services/ai-backend/scripts/usage/seed_pricing.py --source yaml
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from agent_runtime.deployment.profile import DeploymentProfileLoader
from agent_runtime.pricing.composer import PricingComposer, PrimarySource
from agent_runtime.pricing.upsert_planner import (
    Disposition,
    PlannedAction,
    apply_actions,
    plan_actions,
    summary_counts,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory


def _format_plan(plan: Iterable[PlannedAction]) -> str:
    lines: list[str] = []
    for action in plan:
        prefix = f"[{action.disposition:>18s}]"
        key = (
            f"{action.record.provider:>10s} / "
            f"{action.record.model_name:<30s} ({action.record.region})"
        )
        if action.disposition == Disposition.NO_CHANGE:
            lines.append(f"{prefix} {key}  (already current)")
            continue
        if action.disposition == Disposition.INSERT_NEW:
            lines.append(
                f"{prefix} {key}"
                f"  in={action.record.input_per_1m_micro_usd}"
                f" out={action.record.output_per_1m_micro_usd}"
                f" cached={action.record.cached_input_per_1m_micro_usd}"
                f" src={action.record.pricing_source}"
            )
            continue
        # CLOSE_AND_INSERT — show the diff per changed field.
        lines.append(f"{prefix} {key}")
        existing = action.existing
        assert existing is not None  # disposition implies present
        for field in (
            "input_per_1m_micro_usd",
            "output_per_1m_micro_usd",
            "cached_input_per_1m_micro_usd",
            "context_window_tokens",
            "pricing_source",
            "pricing_version",
        ):
            old = getattr(existing, field)
            new = getattr(action.record, field)
            if old != new:
                lines.append(f"    {field}: {old!r} -> {new!r}")
    return "\n".join(lines)


async def _run(
    *,
    primary_source: PrimarySource | None,
    overrides_path: Path | None,
    litellm_data_path: Path | None,
    apply: bool,
) -> int:
    # When ``--source`` isn't passed, defer to the deployment profile
    # (single source of truth: air-gapped profiles set "yaml"; everywhere
    # else defaults to "litellm").
    resolved_source: PrimarySource = primary_source or _resolve_primary_source()
    records = PricingComposer.load(
        primary_source=resolved_source,
        overrides_path=overrides_path,
        litellm_data_path=litellm_data_path,
        effective_from=datetime.now(timezone.utc),
    )

    settings = RuntimeSettings.load()
    ports = RuntimeAdapterFactory.from_settings(settings, role="seed_pricing")
    await ports.lifecycle.open()
    try:
        plan = await plan_actions(ports.persistence, records)
        print(_format_plan(plan))
        counts = summary_counts(plan)
        print()
        print(
            f"Summary: {counts[Disposition.INSERT_NEW]} insert, "
            f"{counts[Disposition.CLOSE_AND_INSERT]} close+insert, "
            f"{counts[Disposition.NO_CHANGE]} no-change"
        )
        if not apply:
            print()
            print("Preview mode — no rows written. Pass --apply to commit.")
            return 0
        if (
            counts[Disposition.INSERT_NEW] == 0
            and counts[Disposition.CLOSE_AND_INSERT] == 0
        ):
            print()
            print("Nothing to write — every row is already current.")
            return 0
        await apply_actions(ports.persistence, plan)
        print()
        print(
            f"Applied: {counts[Disposition.INSERT_NEW]} new, "
            f"{counts[Disposition.CLOSE_AND_INSERT]} replaced."
        )
        return 0
    finally:
        await ports.lifecycle.close()


def _resolve_primary_source() -> PrimarySource:
    """Read the configured primary source from the deployment profile.

    Falls back to ``"litellm"`` if the profile lookup fails (e.g. in a
    bare test environment with no ``ENTERPRISE_DEPLOYMENT_PROFILE``).
    """

    try:
        toggles = DeploymentProfileLoader.load().toggles
    except Exception:
        return PricingComposer.PRIMARY_LITELLM
    raw = toggles.pricing_primary_source
    if raw not in (PricingComposer.PRIMARY_LITELLM, PricingComposer.PRIMARY_YAML):
        return PricingComposer.PRIMARY_LITELLM
    return raw  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_pricing",
        description=(
            "Compose and upsert the runtime pricing catalog. Default is "
            "preview mode (no writes); pass --apply to commit."
        ),
    )
    parser.add_argument(
        "--source",
        choices=(PricingComposer.PRIMARY_LITELLM, PricingComposer.PRIMARY_YAML),
        default=None,
        help="Primary source: litellm or yaml (air-gapped). Defaults to the deployment profile.",
    )
    parser.add_argument(
        "--overrides-path",
        type=Path,
        default=None,
        help="Override YAML path (default: services/ai-backend/config/pricing_overrides.yaml).",
    )
    parser.add_argument(
        "--litellm-data-path",
        type=Path,
        default=None,
        help="Vendored LiteLLM JSON path (default: pricing/litellm_data/model_prices.json).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the plan. Without this flag the script is read-only.",
    )
    args = parser.parse_args(argv)

    return asyncio.run(
        _run(
            primary_source=args.source,
            overrides_path=args.overrides_path,
            litellm_data_path=args.litellm_data_path,
            apply=args.apply,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
