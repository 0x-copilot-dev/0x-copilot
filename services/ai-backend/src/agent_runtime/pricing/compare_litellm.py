"""Parity-diff CLI: ``python -m agent_runtime.pricing.compare_litellm`` (P12 Step 1).

Loads the YAML seeds and the vendored LiteLLM catalog, then prints a
diff for every ``(provider, model_name, region)`` triple the YAML
seeds currently ship. The output is human-readable lines, one per row,
plus an exit code: ``0`` if every YAML seed matches LiteLLM within
tolerance, ``1`` if any seed diverges.

CI can run this to surface drift between the hand-curated seeds and
the LiteLLM upstream. It does not modify the catalog or the DB; it is
observation-only — the contract Step 1 of the P12 PRD commits to.

Usage::

    python -m agent_runtime.pricing.compare_litellm
    python -m agent_runtime.pricing.compare_litellm --tolerance 0.005
    python -m agent_runtime.pricing.compare_litellm --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from decimal import Decimal
from typing import Final

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.litellm_source import LiteLLMPricingSource
from agent_runtime.pricing.seed_loader import PricingSeedLoader


# Default tolerance: 0.1% per the PRD §9 "Tolerance bands per provider"
# discussion. The CLI flag overrides per invocation.
_DEFAULT_TOLERANCE: Final[Decimal] = Decimal("0.001")


class _Field:
    INPUT = "input_per_1m_micro_usd"
    OUTPUT = "output_per_1m_micro_usd"
    CACHED = "cached_input_per_1m_micro_usd"
    CONTEXT = "context_window_tokens"


_COMPARED_FIELDS: Final[tuple[str, ...]] = (
    _Field.INPUT,
    _Field.OUTPUT,
    _Field.CACHED,
    _Field.CONTEXT,
)


class _DiffRow:
    """One row in the comparison report — public via :func:`compare`."""

    __slots__ = (
        "provider",
        "model_name",
        "region",
        "status",
        "differences",
        "litellm_present",
    )

    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        status: str,
        differences: dict[str, dict[str, object]],
        litellm_present: bool,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.region = region
        self.status = status
        self.differences = differences
        self.litellm_present = litellm_present

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model_name": self.model_name,
            "region": self.region,
            "status": self.status,
            "differences": self.differences,
            "litellm_present": self.litellm_present,
        }


def compare(
    *,
    seeds: Iterable[ModelPricingRecord] | None = None,
    litellm_records: Iterable[ModelPricingRecord] | None = None,
    tolerance: Decimal = _DEFAULT_TOLERANCE,
) -> tuple[_DiffRow, ...]:
    """Compare each YAML seed row against the LiteLLM row at the same key.

    Args:
        seeds: override the YAML seed source (tests).
        litellm_records: override the LiteLLM source (tests).
        tolerance: fractional tolerance for numeric fields. A LiteLLM
            value within ``tolerance`` of the seed is reported as
            ``match``; otherwise ``divergent``. ``context_window_tokens``
            is compared exactly (no tolerance).

    Returns one row per YAML seed key. Rows where LiteLLM has no
    matching key are reported as ``missing_in_litellm`` so the
    operator can decide whether to add an override or wait for
    upstream to add the model.
    """

    seed_list = list(seeds) if seeds is not None else list(PricingSeedLoader.load_all())
    litellm_list = (
        list(litellm_records)
        if litellm_records is not None
        else list(LiteLLMPricingSource.load_all())
    )
    litellm_index = LiteLLMPricingSource.by_key(litellm_list)

    rows: list[_DiffRow] = []
    for seed in seed_list:
        key = (seed.provider, seed.model_name, seed.region)
        litellm_row = litellm_index.get(key)
        if litellm_row is None:
            rows.append(
                _DiffRow(
                    provider=seed.provider,
                    model_name=seed.model_name,
                    region=seed.region,
                    status="missing_in_litellm",
                    differences={},
                    litellm_present=False,
                )
            )
            continue

        differences = _diff_record(seed=seed, litellm=litellm_row, tolerance=tolerance)
        status = "match" if not differences else "divergent"
        rows.append(
            _DiffRow(
                provider=seed.provider,
                model_name=seed.model_name,
                region=seed.region,
                status=status,
                differences=differences,
                litellm_present=True,
            )
        )
    return tuple(rows)


def _diff_record(
    *,
    seed: ModelPricingRecord,
    litellm: ModelPricingRecord,
    tolerance: Decimal,
) -> dict[str, dict[str, object]]:
    diffs: dict[str, dict[str, object]] = {}
    for field in _COMPARED_FIELDS:
        seed_value = getattr(seed, field)
        litellm_value = getattr(litellm, field)
        if not _values_within_tolerance(
            field=field,
            seed_value=seed_value,
            litellm_value=litellm_value,
            tolerance=tolerance,
        ):
            diffs[field] = {"seed": seed_value, "litellm": litellm_value}
    return diffs


def _values_within_tolerance(
    *,
    field: str,
    seed_value: object,
    litellm_value: object,
    tolerance: Decimal,
) -> bool:
    if seed_value == litellm_value:
        return True
    if seed_value is None or litellm_value is None:
        return False
    # context_window_tokens uses exact comparison only; provider rate
    # rounding makes tolerance meaningful for the rate fields.
    if field == _Field.CONTEXT:
        return False
    if not isinstance(seed_value, int) or not isinstance(litellm_value, int):
        return False
    if seed_value == 0:
        return litellm_value == 0
    rel = abs(Decimal(litellm_value) - Decimal(seed_value)) / Decimal(abs(seed_value))
    return rel <= tolerance


def _format_human(rows: Iterable[_DiffRow]) -> str:
    lines: list[str] = []
    for row in rows:
        prefix = f"[{row.status:>20s}] {row.provider:>10s} / {row.model_name:<30s} ({row.region})"
        if row.status == "match":
            lines.append(prefix)
            continue
        if row.status == "missing_in_litellm":
            lines.append(f"{prefix}  (LiteLLM has no row for this key)")
            continue
        # divergent
        lines.append(prefix)
        for field, values in row.differences.items():
            lines.append(
                f"    {field}: seed={values['seed']!r}  litellm={values['litellm']!r}"
            )
    return "\n".join(lines)


def _summary(rows: Iterable[_DiffRow]) -> dict[str, int]:
    counts: dict[str, int] = {"match": 0, "divergent": 0, "missing_in_litellm": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_runtime.pricing.compare_litellm",
        description=(
            "Compare hand-curated YAML pricing seeds against the vendored "
            "LiteLLM catalog. Exit code 0 if every seed matches; 1 if any "
            "seed diverges (or is missing upstream)."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=Decimal,
        default=_DEFAULT_TOLERANCE,
        help="Fractional tolerance for rate fields (default: 0.001 = 0.1%%)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the human-readable table.",
    )
    args = parser.parse_args(argv)

    rows = compare(tolerance=args.tolerance)
    if args.json:
        print(json.dumps([row.to_dict() for row in rows], default=str, indent=2))
    else:
        print(_format_human(rows))
        print()
        summary = _summary(rows)
        print(
            f"Summary: {summary['match']} match, "
            f"{summary['divergent']} divergent, "
            f"{summary['missing_in_litellm']} missing in LiteLLM"
        )
    has_problem = any(row.status != "match" for row in rows)
    return 1 if has_problem else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
