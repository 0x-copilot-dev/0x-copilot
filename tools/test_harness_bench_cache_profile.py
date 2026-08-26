"""Guard the cold-prompt profiler that answers FINDINGS.md §4's open question.

Everything here is OFFLINE — no app, no model, no paid run. The profiler reads
run stores already on disk, so the risk it carries is not cost, it is a
confident wrong number: every check below corresponds to a way this tool could
report a cold rate that means something other than what it says.

Four classes of check:

1. **The store is read the way it is written.** ``run_usage.jsonl`` is an
   append-only log with more than one ``put`` per run — one before pricing
   resolves and one after — so first-write-wins silently scores a
   partially-populated row. A torn last line on a store the app is still writing
   must not abandon the file.
2. **The corpora stay apart.** A journey boot is a harness run, not a person.
   Merging them produces a "user cold-start rate" computed mostly from a test
   harness, which is the headline number this whole file exists to protect.
3. **Cold means the provider read nothing**, never an inference — the mistake
   FINDINGS.md §1 documents. Trivial and failed runs are excluded rather than
   counted as cheap, because a broken or empty run is not evidence about the
   cost of a resident prefix.
4. **The write-observability audit can actually fire.** It exists to say "reads
   recorded, writes never" — a claim about the instrument. A version that cannot
   distinguish that from a healthy store would be worse than none.

Run with the repo-gates set:

    PYTHONPATH=tools python -m pytest -q tools/test_harness_bench_cache_profile.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "harness-bench" / "cache_profile.py"


def _load():
    """Import the profiler by path — ``harness-bench`` is not an identifier."""

    spec = importlib.util.spec_from_file_location("cache_profile", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["cache_profile"] = module
    spec.loader.exec_module(module)
    return module


cache_profile = _load()


def _run_row(
    run_id: str,
    *,
    started: str,
    completed: str | None = None,
    cached: int = 0,
    creation: int = 0,
    inp: int = 20_000,
    status: str = "completed",
    provider: str = "anthropic",
) -> str:
    return json.dumps(
        {
            "op": "put",
            "record": {
                "run_id": run_id,
                "model_provider": provider,
                "model_name": "claude-sonnet-5",
                "status": status,
                "input_tokens": inp,
                "cached_input_tokens": cached,
                "cache_creation_input_tokens": creation,
                "started_at": started,
                "completed_at": completed if completed is not None else started,
            },
        }
    )


def _store(root: Path, name: str, rows: list[str]) -> Path:
    path = root / name / "agent-data" / "v1" / "state" / "run_usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class TestStoreReading:
    def test_last_write_per_run_id_wins(self, tmp_path: Path) -> None:
        """The pre-pricing row must never be the one scored.

        Both rows carry the same ``run_id``; only the second knows the run read
        23k tokens from cache. Taking the first scores a real warm run as cold.
        """

        path = _store(
            tmp_path,
            "interactive-a",
            [
                _run_row("r1", started="2026-08-06T06:00:00Z", cached=0),
                _run_row("r1", started="2026-08-06T06:00:00Z", cached=23_000),
            ],
        )
        runs = list(cache_profile.iter_store_runs(path, tmp_path))
        assert len(runs) == 1
        assert runs[0].cached_input_tokens == 23_000
        assert runs[0].cold is False

    def test_a_torn_line_does_not_abandon_the_file(self, tmp_path: Path) -> None:
        """A store the app is mid-write on is expected, not a parse failure."""

        path = _store(
            tmp_path,
            "interactive-a",
            [
                _run_row("r1", started="2026-08-06T06:00:00Z"),
                '{"op":"put","record":{"run_id":"r2","inp',
                _run_row("r3", started="2026-08-06T06:02:00Z"),
            ],
        )
        assert {
            run.run_id for run in cache_profile.iter_store_runs(path, tmp_path)
        } == {
            "r1",
            "r3",
        }

    def test_a_row_without_a_run_id_is_dropped_not_zero_filled(
        self, tmp_path: Path
    ) -> None:
        """A run_id-less row scored as zeros is an invented cold run."""

        path = _store(
            tmp_path,
            "interactive-a",
            [json.dumps({"op": "put", "record": {"input_tokens": 20_000}})],
        )
        assert list(cache_profile.iter_store_runs(path, tmp_path)) == []


class TestCorpusSeparation:
    def test_journey_stores_are_harness_not_interactive(self) -> None:
        assert cache_profile.corpus_of("journey-first-run-anthropic-178") == "harness"
        assert cache_profile.corpus_of("agent-data") == "interactive"

    def test_the_two_corpora_are_reported_separately(self, tmp_path: Path) -> None:
        """Merging them computes a user rate mostly out of a test harness."""

        _store(tmp_path, "agent-data", [_run_row("i1", started="2026-08-06T06:00:00Z")])
        _store(
            tmp_path,
            "journey-x-1",
            [
                _run_row("h1", started="2026-08-06T07:00:00Z", cached=19_000),
                _run_row("h2", started="2026-08-06T07:01:00Z", cached=19_000),
            ],
        )
        rows = cache_profile.corpus_profile(
            cache_profile.load_runs(tmp_path), min_input=5_000
        )
        by_corpus = {row["corpus"]: row for row in rows}
        assert by_corpus["interactive"]["n"] == 1
        assert by_corpus["interactive"]["cold_rate"] == 1.0
        assert by_corpus["harness"]["n"] == 2
        assert by_corpus["harness"]["cold_rate"] == 0.0


class TestColdIsReadNeverInferred:
    def test_cold_is_the_provider_reading_nothing(self) -> None:
        make = lambda cached: cache_profile.Run(  # noqa: E731
            store="s",
            corpus="harness",
            run_id="r",
            provider="anthropic",
            model="m",
            status="completed",
            input_tokens=20_000,
            cached_input_tokens=cached,
            cache_creation_input_tokens=0,
            started_at=None,
            completed_at=None,
        )
        assert make(0).cold is True
        assert make(1).cold is False

    @pytest.mark.parametrize(
        ("status", "inp"),
        [("failed", 20_000), ("completed", 10), ("cancelled", 20_000)],
    )
    def test_failed_and_trivial_runs_are_excluded_not_counted_cheap(
        self, tmp_path: Path, status: str, inp: int
    ) -> None:
        """A run that died or never had a prefix says nothing about the prefix."""

        _store(
            tmp_path,
            "agent-data",
            [_run_row("r1", started="2026-08-06T06:00:00Z", status=status, inp=inp)],
        )
        rows = cache_profile.corpus_profile(
            cache_profile.load_runs(tmp_path), min_input=5_000
        )
        assert rows == []


class TestPositionProfile:
    def test_first_in_store_is_split_from_later_in_store(self, tmp_path: Path) -> None:
        """The comparison the §7 result rests on."""

        _store(
            tmp_path,
            "journey-a-1",
            [
                _run_row("a1", started="2026-08-06T06:00:00Z", cached=0),
                _run_row("a2", started="2026-08-06T06:01:00Z", cached=19_000),
                _run_row("a3", started="2026-08-06T06:02:00Z", cached=19_000),
            ],
        )
        rows = {
            row["label"]: row
            for row in cache_profile.position_profile(
                cache_profile.load_runs(tmp_path), min_input=5_000
            )
        }
        assert rows["first in store"]["n"] == 1
        assert rows["first in store"]["cold_rate"] == 1.0
        assert rows["later in store"]["n"] == 2
        assert rows["later in store"]["cold_rate"] == 0.0


class TestWriteObservabilityAudit:
    def test_it_reports_reads_without_writes(self, tmp_path: Path) -> None:
        """The claim it exists to make: a read with no possible write."""

        _store(
            tmp_path,
            "agent-data",
            [_run_row("r1", started="2026-08-06T06:00:00Z", cached=19_000)],
        )
        audit = cache_profile.audit_cache_write_observability(
            cache_profile.load_runs(tmp_path), tmp_path
        )
        assert audit["runs_with_cache_read"] == 1
        assert audit["runs_with_cache_write"] == 0
        assert audit["cache_read_tokens"] == 19_000

    def test_a_healthy_store_is_distinguishable_from_a_blind_one(
        self, tmp_path: Path
    ) -> None:
        """Without this the audit could never clear, which makes it worthless."""

        _store(
            tmp_path,
            "agent-data",
            [
                _run_row(
                    "r1",
                    started="2026-08-06T06:00:00Z",
                    cached=0,
                    creation=19_000,
                )
            ],
        )
        audit = cache_profile.audit_cache_write_observability(
            cache_profile.load_runs(tmp_path), tmp_path
        )
        assert audit["runs_with_cache_write"] == 1
        assert audit["cache_write_tokens"] == 19_000
