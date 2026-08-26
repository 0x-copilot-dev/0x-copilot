#!/usr/bin/env python3
"""How often does a run pay the full cold prompt? Read it off the run store.

``FINDINGS.md`` §4 measured that 97% of warm input is a cache read, so the
~20.5k resident prompt is nearly free when warm and full price on every cold
start. It then left one question open, and named it as the next thing worth
measuring:

    "Unmeasured and worth measuring next: what fraction of real runs hit a
    cold cache."

That fraction is the multiplier on every prompt-trimming change we make. If
runs are warm, trimming the resident prefix buys almost nothing; if they are
cold, trimming is the whole game. This file answers it **offline**, from
``run_usage.jsonl`` records already on disk — the same discipline as
``rescore.py``: a measurement mistake here never costs another paid run.

    python tools/harness-bench/cache_profile.py
    python tools/harness-bench/cache_profile.py --root ~/some/other/COPILOT_HOME
    python tools/harness-bench/cache_profile.py --json      # machine-readable

What "cold" means here, and what it does not
--------------------------------------------
A run is scored COLD when the provider reported ``cached_input_tokens == 0``.
That is ground truth from the provider, not an inference — the mistake §1 of
FINDINGS.md documents at length.

Three blind spots, stated up front because a metric whose limits are not
written down gets over-read:

1. **``run_usage`` is a rollup over every model call in the run.** A run whose
   first call was cold and whose remaining calls were warm reports
   ``cached > 0`` and scores WARM. The cold count is therefore a **lower
   bound** on runs that paid a cold prefix. The per-call ledger that could
   settle it — ``context_occupancy.jsonl`` — records ``cached_input_tokens``
   and ``provider_input_tokens`` but has never had them populated on any run
   in this corpus (see :func:`audit_cache_write_observability`).
2. **A store is a COPILOT_HOME, not a user.** Journey stores are harness runs
   with heterogeneous configs. "First run in a store" therefore conflates a
   process start with a prompt-prefix change, and this data cannot separate
   them.
3. **Trivial and failed runs are not evidence** about a resident prefix, so
   runs under ``--min-input`` (default 5,000) and non-``completed`` runs are
   excluded rather than counted as cheap.

The cache lives at the provider, not on our disk
------------------------------------------------
Worth stating because it makes one reading tempting and wrong: a prompt cache
is keyed on the prefix at the provider, so a fresh COPILOT_HOME can in
principle still hit a warm cache if an identical prefix went out recently from
any process. :func:`global_gap_profile` tests exactly that, and in this corpus
it **fails to hold** — see FINDINGS.md §7.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

DEFAULT_ROOT: Final = Path.home() / "Library" / "Application Support" / "0xCopilot"
STORE_GLOB: Final = "**/state/run_usage.jsonl"
OCCUPANCY_GLOB: Final = "**/state/context_occupancy.jsonl"

#: Below this, a run says nothing about the cost of a resident prefix.
DEFAULT_MIN_INPUT: Final = 5_000

#: A journey store is a harness boot, not a person using the app.
HARNESS_STORE_PREFIX: Final = "journey-"

#: Ordered, half-open, seconds. The first bucket that fits wins.
GAP_BUCKETS: Final[tuple[tuple[float, float, str], ...]] = (
    (0, 60, "< 1 min"),
    (60, 300, "1-5 min"),
    (300, 900, "5-15 min"),
    (900, 3_600, "15-60 min"),
    (3_600, 86_400, "1-24 h"),
    (86_400, float("inf"), "> 1 day"),
)


@dataclass(frozen=True, slots=True)
class Run:
    """One run, reduced to the fields that bear on cache state."""

    store: str
    corpus: str
    run_id: str
    provider: str
    model: str
    status: str
    input_tokens: int
    cached_input_tokens: int
    cache_creation_input_tokens: int
    started_at: datetime | None
    completed_at: datetime | None

    @property
    def cold(self) -> bool:
        """The provider read nothing from cache for this run.

        A lower bound on "paid a cold prefix" — see the module docstring.
        """

        return self.cached_input_tokens == 0

    @property
    def scorable(self) -> bool:
        """Completed, and large enough to say something about a prefix."""

        return self.status == "completed"


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def corpus_of(store: str) -> str:
    """``harness`` for a journey boot, ``interactive`` for real desktop use."""

    return "harness" if store.startswith(HARNESS_STORE_PREFIX) else "interactive"


def iter_store_runs(path: Path, root: Path) -> Iterator[Run]:
    """Yield each distinct run in one ``run_usage.jsonl``, last write winning.

    The file is an append-only log of ``put`` ops and a run is written more
    than once — once before pricing resolves and again after — so the last row
    per ``run_id`` is the authoritative one. Taking the first would drop
    ``cost_micro_usd`` and, more importantly, read a partially-populated row.
    """

    store = os.path.relpath(path, root).split(os.sep)[0]
    latest: dict[str, Mapping[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                # A torn last line on a store the app is still writing is
                # expected; it is not a reason to abandon the whole file.
                continue
            record = envelope.get("record")
            if not isinstance(record, Mapping):
                continue
            run_id = record.get("run_id")
            if isinstance(run_id, str) and run_id:
                latest[run_id] = record

    for run_id, record in latest.items():
        yield Run(
            store=store,
            corpus=corpus_of(store),
            run_id=run_id,
            provider=str(record.get("model_provider") or "unknown"),
            model=str(record.get("model_name") or "unknown"),
            status=str(record.get("status") or "unknown"),
            input_tokens=_as_int(record.get("input_tokens")),
            cached_input_tokens=_as_int(record.get("cached_input_tokens")),
            cache_creation_input_tokens=_as_int(
                record.get("cache_creation_input_tokens")
            ),
            started_at=_parse_ts(record.get("started_at")),
            completed_at=_parse_ts(record.get("completed_at")),
        )


def load_runs(root: Path) -> list[Run]:
    """Every run under ``root``, ordered by start time."""

    runs = [
        run
        for path in sorted(root.glob(STORE_GLOB))
        for run in iter_store_runs(path, root)
    ]
    runs.sort(key=lambda run: (run.started_at or datetime.min.replace(tzinfo=None),))
    return runs


def _bucket(seconds: float) -> str:
    for low, high, name in GAP_BUCKETS:
        if low <= seconds < high:
            return name
    return GAP_BUCKETS[-1][2]


def _rate_rows(pairs: Sequence[tuple[str, bool]]) -> list[dict[str, Any]]:
    """Cold rate per label, in ``GAP_BUCKETS`` order then alphabetical."""

    grouped: dict[str, list[bool]] = defaultdict(list)
    for label, cold in pairs:
        grouped[label].append(cold)
    order = {name: index for index, (_, _, name) in enumerate(GAP_BUCKETS)}
    return [
        {
            "label": label,
            "n": len(values),
            "cold": sum(values),
            "cold_rate": sum(values) / len(values),
        }
        for label, values in sorted(
            grouped.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0])
        )
    ]


def corpus_profile(runs: Iterable[Run], *, min_input: int) -> list[dict[str, Any]]:
    """Cold rate per corpus — the headline, kept honest by keeping them apart.

    Blind spot: ``interactive`` is one machine's own desktop use. Treat its
    rate as a sample size to be grown, never as a population rate.
    """

    grouped: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        if run.scorable and run.input_tokens > min_input:
            grouped[run.corpus].append(run)

    rows = []
    for corpus, group in sorted(grouped.items()):
        total_input = sum(run.input_tokens for run in group)
        rows.append(
            {
                "corpus": corpus,
                "n": len(group),
                "cold": sum(run.cold for run in group),
                "cold_rate": sum(run.cold for run in group) / len(group),
                "cached_share_of_input": (
                    sum(run.cached_input_tokens for run in group) / total_input
                    if total_input
                    else 0.0
                ),
                "median_input": statistics.median([run.input_tokens for run in group]),
            }
        )
    return rows


def position_profile(runs: Iterable[Run], *, min_input: int) -> list[dict[str, Any]]:
    """Cold rate for the first run in a store versus every later one.

    This is the comparison that carries the result. It holds the time scale
    roughly fixed — a store's runs are minutes apart either way — and varies
    only whether a process boundary sits in front of the run.
    """

    first_seen: set[str] = set()
    pairs: list[tuple[str, bool]] = []
    for run in runs:
        position = "first in store" if run.store not in first_seen else "later in store"
        first_seen.add(run.store)
        if run.scorable and run.input_tokens > min_input:
            pairs.append((position, run.cold))
    return _rate_rows(pairs)


def local_gap_profile(runs: Iterable[Run], *, min_input: int) -> list[dict[str, Any]]:
    """Cold rate by gap since the previous run **in the same store**.

    Blind spot: same store is not the same prompt prefix. A model switch or a
    tool-set change invalidates the prefix without any time passing, and this
    profile cannot see that happen.
    """

    previous_end: dict[str, datetime] = {}
    pairs: list[tuple[str, bool]] = []
    for run in runs:
        prior = previous_end.get(run.store)
        if run.completed_at:
            previous_end[run.store] = run.completed_at
        if not (run.scorable and run.input_tokens > min_input):
            continue
        if prior is None or run.started_at is None:
            pairs.append(("no prior run in store", run.cold))
            continue
        pairs.append((_bucket((run.started_at - prior).total_seconds()), run.cold))
    return _rate_rows(pairs)


def global_gap_profile(runs: Sequence[Run], *, min_input: int) -> list[dict[str, Any]]:
    """For each store's first run: gap to the nearest earlier run **anywhere**.

    The falsifiable test of the provider-side-cache reading. A prompt cache is
    keyed on the prefix at the provider, so if our stores shared a prefix, a
    fresh store whose predecessor finished seconds ago should be warm and one
    whose predecessor finished a day ago should be cold — a monotone curve.

    A flat curve refutes it: whatever makes a first run cold is then not the
    passage of time, and the process boundary is doing the work.
    """

    ordered = [run for run in runs if run.started_at is not None]
    firsts: dict[str, Run] = {}
    for run in ordered:
        firsts.setdefault(run.store, run)

    pairs: list[tuple[str, bool]] = []
    for run in firsts.values():
        if not (run.scorable and run.input_tokens > min_input):
            continue
        assert run.started_at is not None
        prior_ends = [
            other.completed_at
            for other in ordered
            if other.provider == run.provider
            and other.completed_at is not None
            and other.completed_at < run.started_at
        ]
        if not prior_ends:
            pairs.append(("no prior run anywhere", run.cold))
            continue
        pairs.append(
            (_bucket((run.started_at - max(prior_ends)).total_seconds()), run.cold)
        )
    return _rate_rows(pairs)


def audit_cache_write_observability(runs: Sequence[Run], root: Path) -> dict[str, Any]:
    """Can this corpus observe a cache **write** at all?

    A cache read is impossible without a preceding write, so a corpus with
    millions of read tokens and not one recorded write is telling us something
    about the instrument rather than about the cache.

    Two records are checked because they are meant to agree:

    * ``run_usage.jsonl`` — the billing rollup, which prices a write at 1.25x.
    * ``context_occupancy.jsonl`` — the per-call ledger whose own docstring
      says its cache fields are "what makes the report correct rather than
      merely large", because a reader without them "would recommend trimming
      the stable prefix, which is exactly backwards".
    """

    occupancy_calls = 0
    occupancy_with_cache = 0
    occupancy_with_provider_tokens = 0
    for path in sorted(root.glob(OCCUPANCY_GLOB)):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    envelope = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = envelope.get("record")
                if not isinstance(record, Mapping) or "model_call_id" not in record:
                    continue
                occupancy_calls += 1
                if _as_int(record.get("cached_input_tokens")) or _as_int(
                    record.get("cache_creation_input_tokens")
                ):
                    occupancy_with_cache += 1
                if record.get("provider_input_tokens") is not None:
                    occupancy_with_provider_tokens += 1

    return {
        "runs": len(runs),
        "runs_with_cache_read": sum(1 for r in runs if r.cached_input_tokens),
        "runs_with_cache_write": sum(1 for r in runs if r.cache_creation_input_tokens),
        "cache_read_tokens": sum(r.cached_input_tokens for r in runs),
        "cache_write_tokens": sum(r.cache_creation_input_tokens for r in runs),
        "occupancy_model_calls": occupancy_calls,
        "occupancy_calls_with_cache_fields": occupancy_with_cache,
        "occupancy_calls_with_provider_tokens": occupancy_with_provider_tokens,
    }


def _print_rate_table(title: str, rows: Sequence[Mapping[str, Any]], note: str) -> None:
    print(f"\n{title}")
    print(f"  {note}")
    print(f"  {'':24s} {'n':>5s} {'cold':>6s} {'cold rate':>10s}")
    for row in rows:
        print(
            f"  {row['label']:24s} {row['n']:5d} {row['cold']:6d} "
            f"{row['cold_rate']:9.1%}"
        )


def render(report: Mapping[str, Any]) -> None:
    print("=" * 72)
    print("cold-prompt profile — how often a run pays the resident prefix in full")
    print("=" * 72)
    print(f"root   : {report['root']}")
    print(f"stores : {report['stores']}   runs: {report['total_runs']}")
    print(f"scored : completed runs with input > {report['min_input']:,} tokens")

    print("\ncold rate by corpus")
    print(
        f"  {'corpus':14s} {'n':>5s} {'cold':>6s} {'cold rate':>10s} "
        f"{'cached/input':>13s} {'median input':>13s}"
    )
    for row in report["corpus"]:
        print(
            f"  {row['corpus']:14s} {row['n']:5d} {row['cold']:6d} "
            f"{row['cold_rate']:9.1%} {row['cached_share_of_input']:12.1%} "
            f"{row['median_input']:13,.0f}"
        )

    _print_rate_table(
        "cold rate by position in store",
        report["position"],
        "holds the time scale roughly fixed; varies the process boundary",
    )
    _print_rate_table(
        "cold rate by gap since previous run IN THE SAME STORE",
        report["local_gap"],
        "blind spot: same store is not the same prompt prefix",
    )
    _print_rate_table(
        "first-run-in-store: cold rate by gap to nearest earlier run ANYWHERE",
        report["global_gap"],
        "a flat curve refutes the provider-side-TTL reading",
    )

    audit = report["cache_write_audit"]
    print("\ncache-write observability")
    print(
        f"  runs reading cache : {audit['runs_with_cache_read']:5d} of "
        f"{audit['runs']}   ({audit['cache_read_tokens']:,} tokens)"
    )
    print(
        f"  runs writing cache : {audit['runs_with_cache_write']:5d} of "
        f"{audit['runs']}   ({audit['cache_write_tokens']:,} tokens)"
    )
    print(
        f"  occupancy calls carrying any cache field : "
        f"{audit['occupancy_calls_with_cache_fields']} of "
        f"{audit['occupancy_model_calls']}"
    )
    print(
        f"  occupancy calls carrying provider totals : "
        f"{audit['occupancy_calls_with_provider_tokens']} of "
        f"{audit['occupancy_model_calls']}"
    )
    if audit["cache_read_tokens"] and not audit["cache_write_tokens"]:
        print(
            "\n  A cache read is impossible without a preceding write. Reads "
            "recorded,\n  writes never — so a write is not observable here. "
            "That is a statement\n  about the instrument, not about the cache. "
            "See FINDINGS.md §7."
        )


def build_report(root: Path, *, min_input: int) -> dict[str, Any]:
    runs = load_runs(root)
    return {
        "root": str(root),
        "stores": len({run.store for run in runs}),
        "total_runs": len(runs),
        "min_input": min_input,
        "corpus": corpus_profile(runs, min_input=min_input),
        "position": position_profile(runs, min_input=min_input),
        "local_gap": local_gap_profile(runs, min_input=min_input),
        "global_gap": global_gap_profile(runs, min_input=min_input),
        "cache_write_audit": audit_cache_write_observability(runs, root),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="a COPILOT_HOME to scan (default: the packaged app's)",
    )
    parser.add_argument(
        "--min-input",
        type=int,
        default=DEFAULT_MIN_INPUT,
        help="ignore runs below this input size (default: %(default)s)",
    )
    parser.add_argument("--json", action="store_true", help="emit the raw report")
    args = parser.parse_args(argv)

    root = args.root.expanduser()
    if not root.is_dir():
        print(f"no such COPILOT_HOME: {root}", file=sys.stderr)
        return 2

    report = build_report(root, min_input=args.min_input)
    if report["total_runs"] == 0:
        print(f"no run_usage.jsonl records under {root}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
