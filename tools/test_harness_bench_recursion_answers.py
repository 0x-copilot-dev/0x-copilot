"""Guard the correctness axis on the recursion bench, and the scorer that grades it.

Everything here is OFFLINE — no app, no model, no paid run.

The defect this file exists over is on disk and is quoted below. Both committed
arms of `recursion_ceiling_ab.py` scored TERMINATION only, and both hid a wrong
answer inside a `completed` row: at limit=500 `t4-long-chain` terminated
`completed` in one model call, and its ENTIRE final answer was one sentence
about the number 1. It counted toward `4/4`. Every published cost number in
FINDINGS.md was measured on that arm.

Three classes of check, and they are not interchangeable:

1. **The expected answers are derived, not typed.** A hand-typed answer is a
   second source of truth that drifts from the prompt; a hand-typed answer that
   drifts *toward* what a model happened to say is worse, because it turns the
   measurement into a decoration. Each expectation is recomputed here,
   independently of the module, and checked against the wrong answers a model is
   actually likely to give.
2. **NOT MEASURED never becomes WRONG.** The two arms on disk declared no
   expected answer, and the reader that supplies the text could itself be
   broken. Either case must surface as `?`. A scorer that rendered them `-`
   would report a regression that never happened — the fabricated-negative
   mirror of the zero-token instrument failure in FINDINGS.md method note 1, and
   worse than it, because a negative stops investigation.
3. **A counter that cannot observe its subject returns None.** `usage.recorded`
   does not fire on the ordinary run path at all, and the old `llm_calls` column
   published 0 for all eight committed rows because of it.

Run with the repo-gates set:

    PYTHONPATH=tools python -m pytest -q tools/test_harness_bench_recursion_answers.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH = REPO_ROOT / "tools" / "harness-bench"


def _load(name: str):
    """Import a `tools/harness-bench/*.py` module by path.

    The directory is not a package and its name is not an identifier, so a
    normal import cannot reach it.
    """

    spec = importlib.util.spec_from_file_location(
        f"_bench_{name}", BENCH / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rescore():
    return _load("rescore")


@pytest.fixture(scope="module")
def bench():
    # `recursion_ceiling_ab` imports the desktop-journey harness, which needs
    # 3.11+ for StrEnum and nothing else at import time — no app, no driver.
    return _load("recursion_ceiling_ab")


@pytest.fixture(scope="module")
def heavy():
    return _load("heavy_tasks_ab")


def _task(bench, task_id: str):
    return next(t for t in bench.TASKS if t.task_id == task_id)


# ── 1. the expectations are derived, and they reject the likely wrong answers ─
def test_every_task_declares_a_checkable_answer_and_a_claim(bench):
    """A task with no expected answer cannot report `outcome_ok`.

    The whole point of this change is that `status` was the only outcome column.
    An expectation that matches anything restores that state while looking like
    it fixed it.
    """

    assert bench.TASKS, "the task set is empty"
    for task in bench.TASKS:
        assert task.expect.pattern, f"{task.task_id} declares no expected answer"
        assert task.expect.pattern not in {"", ".", ".*", "(?i).*"}, (
            f"{task.task_id}'s expected answer matches anything"
        )
        assert task.claim, f"{task.task_id} does not say which claim it reaches"
        assert task.prompt.strip(), f"{task.task_id} has no prompt"


def test_the_expected_answers_are_derived_from_the_constants_not_typed(bench):
    """Recompute every answer here, independently, and grade the near-misses.

    Independently is load-bearing: importing `T2_ANSWER` and asserting it equals
    itself proves nothing. The wrong answers listed are the ones a model
    plausibly produces — a right list with one extra element, a list including 1
    as prime, a digit run that swallows the boundary.
    """

    # t1 — the literal word, and nothing wrapped around a sentence.
    t1 = _task(bench, "t1-trivial")
    assert t1.expect.search("ready.")
    assert t1.expect.search("**Ready**")
    assert not t1.expect.search("I am ready to help.")
    assert not t1.expect.search("readying")

    # t2 — (7 + 5) * 3 - 6, recomputed from the terms the prompt states.
    add, mul, sub = (
        bench.T2_TERMS[0] + bench.T2_TERMS[1],
        bench.T2_TERMS[2],
        bench.T2_TERMS[3],
    )
    assert add * mul - sub == bench.T2_ANSWER == 30
    t2 = _task(bench, "t2-three-steps")
    assert t2.expect.search("T2=30")
    assert t2.expect.search("Step 3: 36 - 6 = 30. T2 = 30")
    assert not t2.expect.search("T2=36"), "forgot the subtraction"
    assert not t2.expect.search("T2=12"), "stopped after step 1"
    assert not t2.expect.search("T2=300"), "a digit run must not satisfy 30"

    # t3 — descending by value, recomputed from the rows.
    order = tuple(name for name, _ in sorted(bench.T3_ROWS, key=lambda r: -r[1]))
    assert order == bench.T3_ORDER == ("beta", "gamma", "alpha")
    t3 = _task(bench, "t3-todo-driven")
    assert t3.expect.search("T3=beta,gamma,alpha")
    assert t3.expect.search("**T3=beta, gamma, alpha**")
    assert not t3.expect.search("T3=alpha,beta,gamma"), "sorted the wrong way"
    assert not t3.expect.search("T3=beta,gamma,alpha,delta"), "invented a row"

    # t4 — primes up to the limit, recomputed by a different method (a sieve).
    sieve = [True] * (bench.T4_LIMIT + 1)
    sieve[0] = sieve[1] = False
    for n in range(2, bench.T4_LIMIT + 1):
        if sieve[n]:
            for multiple in range(n * n, bench.T4_LIMIT + 1, n):
                sieve[multiple] = False
    assert tuple(n for n, prime in enumerate(sieve) if prime) == bench.T4_PRIMES
    assert bench.T4_PRIMES == (2, 3, 5, 7, 11)
    t4 = _task(bench, "t4-long-chain")
    assert t4.expect.search("T4=2,3,5,7,11")
    assert t4.expect.search("T4 = 2, 3, 5, 7, 11.")
    assert not t4.expect.search("T4=2,3,5,7,11,13"), "13 is past the limit"
    assert not t4.expect.search("T4=1,2,3,5,7,11"), "1 is not prime here"
    assert not t4.expect.search("T4=2,3,5,7,110"), "a digit run must not pass"

    # And the two answers the committed arms ACTUALLY gave, which `status`
    # recorded as `completed` and this column records as wrong.
    assert not t4.expect.search(
        "**1:** Not prime — 1 has only one divisor (itself), so it doesn't "
        "meet the definition of prime."
    )


def test_t3_values_are_strictly_ordered_so_exactly_one_ordering_is_right(bench):
    """A tie gives t3 two correct answers, and a task with two cannot be graded.

    Same guard as `h2-crossref`'s top-owner tie in the heavy set: it is the
    silent way a checkable task stops being checkable.
    """

    values = [value for _, value in bench.T3_ROWS]
    assert len(set(values)) == len(values), "two rows share a value; the order ties"
    assert len({name for name, _ in bench.T3_ROWS}) == len(bench.T3_ROWS)


def test_no_task_expectation_matches_its_own_prompt(bench):
    """A prompt containing its own answer lets a parroting model pass.

    Every prompt here states the ANSWER FORM (`T4=<the primes you found>`), and
    the expectation must key on the value rather than the form, or the task
    grades a model that echoed the instruction back.
    """

    for task in bench.TASKS:
        assert not task.expect.search(task.prompt), (
            f"{task.task_id}'s expected answer is present in its own prompt, so "
            "echoing the instruction would grade as correct"
        )


def test_the_recorded_pattern_string_grades_the_same_as_the_live_one(bench, heavy):
    """`re.Pattern.pattern` drops the flags — and the ROW records the string.

    `rescore.py` recompiles that bare string offline. If a task expressed
    case-insensitivity as an `re.IGNORECASE` argument instead of an inline
    `(?i)`, the live verdict and the offline verdict would differ silently, in
    the direction that invents a wrong answer.
    """

    probes = {
        "t1-trivial": "READY.",
        "t2-three-steps": "t2=30",
        "t3-todo-driven": "T3=Beta,Gamma,Alpha",
        "t4-long-chain": "t4 = 2, 3, 5, 7, 11",
    }
    for task in bench.TASKS:
        probe = probes[task.task_id]
        recompiled = re.compile(task.expect.pattern)
        assert bool(task.expect.search(probe)) == bool(recompiled.search(probe)), (
            f"{task.task_id}'s recorded pattern grades differently than the live "
            "one; express its flags inline as (?i)"
        )

    # Same invariant on the heavy set, which reaches it through a helper because
    # its patterns were written with the flags argument.
    for task in heavy.TASKS:
        recorded = re.compile(heavy.recorded_pattern(task.expect))
        for probe in ("CORPUS OK", "corpus ok", "TOTAL=33 TOP=Ada", "CHAIN DONE 12"):
            assert bool(task.expect.search(probe)) == bool(recorded.search(probe)), (
                f"{task.task_id}'s recorded pattern grades differently than live"
            )


# ── 2. NOT MEASURED is never WRONG ───────────────────────────────────────────
def test_a_row_with_no_expected_answer_is_unknown_not_wrong(rescore):
    """The eight rows already in `runs/` are this case, and they are not failures.

    A scorer that graded them would print `0/4 correct` for a set that never
    asked a question — a fabricated negative, and the kind of result that stops
    an investigation instead of starting one.
    """

    row = {"task": "t4-long-chain", "run_id": "r1"}
    verdict, reason = rescore.outcome_for(row, {"r1": "anything at all"}, {"r1"})
    assert verdict is None, "a row with no expectation must be UNKNOWN, not False"
    assert reason == rescore.NO_EXPECTATION
    assert rescore.ok_cell(verdict) == "?"
    assert rescore.ok_cell(True) == "Y"
    assert rescore.ok_cell(False) == "-"


def test_a_run_absent_from_the_event_log_is_unknown_not_wrong(rescore):
    """The broken-reader guard, and the one that must never be relaxed.

    If the glob, the session directory or the payload key were wrong,
    `final_answers` would return an empty map and every row would grade False.
    Keying on the SEEN set instead means a reader that found nothing reports
    nothing. Note the other run in the same log grades fine, so this is not a
    blanket bail-out — it is per run.
    """

    answers, seen = {"r1": "T4=2,3,5,7,11"}, {"r1"}
    graded = {"task": "t4", "run_id": "r1", "expected": r"(?i)T4\s*=\s*2,3,5,7,11"}
    missing = {"task": "t4b", "run_id": "r2", "expected": r"(?i)T4\s*=\s*2,3,5,7,11"}
    assert rescore.outcome_for(graded, answers, seen) == (True, None)
    verdict, reason = rescore.outcome_for(missing, answers, seen)
    assert verdict is None, "an unseen run must be UNKNOWN, never wrong"
    assert reason == rescore.NO_EVENT_LOG

    # And a reader that found NOTHING must not turn four rows into four losses.
    verdict, reason = rescore.outcome_for(graded, {}, set())
    assert verdict is None
    assert reason == rescore.NO_EVENT_LOG


def test_final_answers_reads_the_store_and_reports_what_it_saw(rescore, tmp_path):
    """Both on-disk shapes, and the seen-set contract, over a synthetic store."""

    session = tmp_path / "agent-data" / "v1" / "workspaces" / "w1" / "sessions" / "s1"
    session.mkdir(parents=True)
    lines = [
        # The flat shape the real store writes.
        {
            "event_type": "final_response",
            "run_id": "r1",
            "payload": {"message": "T4=2,3,5,7,11"},
        },
        # A delta must not be mistaken for the final answer.
        {"event_type": "model_delta", "run_id": "r1", "payload": {"text": "T4=1"}},
        # The nested shape `terminal_codes` also accepts.
        {
            "record": {
                "event_type": "message_completed",
                "run_id": "r2",
                "payload": {"text": "CORPUS OK"},
            }
        },
        # Seen, but produced no final answer — arm-25's ceiling-stopped t3.
        {"event_type": "run_failed", "run_id": "r3", "payload": {"code": "x"}},
        {"not": "json-shaped-but-parseable"},
    ]
    (session / "events.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\nnot json at all\n"
    )

    answers, seen = rescore.final_answers(tmp_path)
    assert answers == {"r1": "T4=2,3,5,7,11", "r2": "CORPUS OK"}
    assert seen == {"r1", "r2", "r3"}
    # r3 is SEEN with no answer: graded against an expectation it is genuinely
    # wrong (it produced nothing), which is different from not measured.
    row = {"run_id": "r3", "expected": r"(?i)T4\s*=\s*2,3,5,7,11"}
    assert rescore.outcome_for(row, answers, seen) == (False, None)


def test_the_completed_run_that_answered_one_number_of_twelve_grades_wrong(
    bench, rescore
):
    """The real defect, reproduced from the store rather than imagined.

    Run `733191036bb64fdb858006d0b5f8b934` in
    `journey-bench-recursion-500-1786697962716726000`: status `completed`,
    terminal_code `run_completed`, one model call, zero tool invocations, 94
    output tokens — and this as its entire final answer. It counted toward
    limit=500's `4/4`, and `4/4` is a line in FINDINGS.md §6.
    """

    answer = (
        "**1:** Not prime — 1 has only one divisor (itself), so it doesn't "
        "meet the definition of prime."
    )
    run_id = "733191036bb64fdb858006d0b5f8b934"
    task = _task(bench, "t4-long-chain")
    row = {
        "task": task.task_id,
        "run_id": run_id,
        "status": "completed",
        "terminal_code": "run_completed",
        "expected": task.expect.pattern,
    }
    verdict, reason = rescore.outcome_for(row, {run_id: answer}, {run_id})
    assert verdict is False, "a completed run with the wrong answer must read wrong"
    assert reason is None
    assert rescore.ok_cell(verdict) == "-"
    assert rescore.last_line(answer).startswith("**1:** Not prime")


def test_score_actually_reaches_the_grading_branch_end_to_end(
    rescore, tmp_path, monkeypatch, capsys
):
    """The whole path, not just its helpers — a correct fix on a dead branch is dead.

    `outcome_for` being right proves nothing if `score` never calls it, and this
    repository has shipped exactly that: a tested, green, correct change on an
    arm production never takes. So drive `score` over a synthetic store and read
    the report it writes back.

    Also pins the overwrite contract. A row carries a LIVE verdict from the arm
    and a store-derived one from here; when they disagree the file keeps only
    the later, so the disagreement has to be printed or it becomes a silent
    edit of committed evidence.
    """

    subdir = "journey-bench-recursion-v2-1"
    store = tmp_path / subdir
    session = store / "agent-data" / "v1" / "workspaces" / "w" / "sessions" / "s"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "event_type": "final_response",
                    "run_id": "graded",
                    "payload": {"message": "step one\n\nT4=2,3,5,7"},
                },
                {"event_type": "run_completed", "run_id": "graded", "payload": {}},
                {
                    "event_type": "final_response",
                    "run_id": "ungraded",
                    "payload": {"message": "whatever it likes"},
                },
                {"event_type": "run_completed", "run_id": "ungraded", "payload": {}},
            )
        )
        + "\n"
    )

    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "arm-v2.json").write_text(
        json.dumps(
            {
                "recursion_limit": "v2",
                "user_data_subdir": subdir,
                "tasks": [
                    {
                        "task": "t4-long-chain",
                        "run_id": "graded",
                        "status": "completed",
                        # The live pass said this was fine. The store disagrees.
                        "outcome_ok": True,
                        "expected": r"(?i)T4\s*=\s*2\s*,\s*3\s*,\s*5\s*,\s*7\s*,"
                        r"\s*11\b(?!\s*,\s*\w)",
                    },
                    {
                        "task": "t0-legacy",
                        "run_id": "ungraded",
                        "status": "completed",
                    },
                ],
            }
        )
    )
    monkeypatch.setattr(rescore, "RUNS", runs)
    monkeypatch.setattr(rescore, "APP_SUPPORT", tmp_path)

    report = rescore.score("arm-v2")
    assert report is not None
    graded, ungraded = report["tasks"]

    assert graded["outcome_ok"] is False, "a declared expectation must be graded"
    assert "outcome_reason" not in graded, "a graded row has nothing to explain"
    assert graded["answer_tail"] == "T4=2,3,5,7", "the sentinel line is the evidence"
    assert graded["status"] == "completed", "termination is reported side by side"
    assert "outcome_ok True -> False" in capsys.readouterr().out

    # The legacy row declares nothing, so it is UNKNOWN — not wrong, and not
    # quietly filled in from a pattern it never ran against.
    assert ungraded.get("outcome_ok") is None
    assert ungraded["outcome_reason"] == rescore.NO_EXPECTATION
    assert ungraded["answer_head"] == "whatever it likes"

    # And the write-back is what a later run of the scorer reads.
    on_disk = json.loads((runs / "arm-v2.json").read_text())
    assert on_disk["tasks"][0]["outcome_ok"] is False


def test_the_committed_arms_are_reported_unknown_and_never_graded(rescore):
    """The provenance gate, as a test rather than as a habit.

    `arm-25` and `arm-500` ran the PREVIOUS prompt set. Grading them against
    today's expectations would restate history; inventing an expectation for
    them would be worse. They carry no `expected`, so they must stay `?`.
    """

    for name in ("arm-25", "arm-500"):
        report = json.loads((BENCH / "runs" / f"{name}.json").read_text())
        for row in report["tasks"]:
            assert "expected" not in row, (
                f"{name}/{row['task']} has acquired an expectation it never ran "
                "against; an arm may only be graded against its own prompts"
            )
            assert row.get("outcome_ok") is None, (
                f"{name}/{row['task']} carries a true/false verdict it cannot support"
            )
            assert rescore.ok_cell(row.get("outcome_ok")) == "?"


def test_a_report_is_scored_against_its_own_session_never_the_newest(
    rescore, tmp_path, monkeypatch
):
    """The newest-match glob would quote ANOTHER run's answer as this row's.

    Three `journey-bench-recursion-500-*` directories exist on the box the
    committed arms were measured on. While every column was a token count that
    fallback only mislabelled numbers; now that a row carries the text a model
    produced, it would fabricate evidence. Precedence: `user_data_subdir` first,
    then the `session_dir` a previous rescore resolved, then the glob.
    """

    monkeypatch.setattr(rescore, "APP_SUPPORT", tmp_path)
    for name in ("journey-bench-recursion-500-1", "journey-bench-recursion-500-9"):
        (tmp_path / name).mkdir()
    named = tmp_path / "journey-bench-recursion-500-1"

    assert rescore.session_dir("arm-500", {"session_dir": str(named)}) == named
    assert rescore.session_dir("arm-500", {"user_data_subdir": named.name}) == named
    # No hint at all still falls back — the hazard is named, not removed.
    assert rescore.session_dir("arm-500", {}) in {
        tmp_path / "journey-bench-recursion-500-1",
        tmp_path / "journey-bench-recursion-500-9",
    }


def test_terminal_codes_still_extracts_the_ceiling_code(rescore, tmp_path):
    """Characterization: FINDING §1 rests on this extraction, so pin it.

    Written to survive `_event_records` being factored out from under it. The
    payload `code` wins over the event name, and a run with no code falls back
    to the event type rather than vanishing.
    """

    session = tmp_path / "agent-data" / "v1" / "workspaces" / "w" / "sessions" / "s"
    session.mkdir(parents=True)
    lines = [
        {
            "record": {
                "event_type": "run_failed",
                "run_id": "r1",
                "payload": {"code": "recursion_limit_exceeded"},
            }
        },
        {"event_type": "run_completed", "run_id": "r2", "payload": {}},
        {"event_type": "model_delta", "run_id": "r3", "payload": {"code": "nope"}},
    ]
    (session / "events.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n"
    )
    assert rescore.terminal_codes(tmp_path) == {
        "r1": "recursion_limit_exceeded",
        "r2": "run_completed",
    }


# ── 3. a counter that cannot observe its subject returns None ────────────────
class _StubSession:
    """Just enough `DriverSession` for `measure` — one events response."""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def transport(self, method: str, path: str) -> dict:
        assert method == "GET" and path.endswith("/events")
        return {"events": self._events}


def test_the_live_measure_reports_none_rather_than_zero_for_a_dead_counter(bench):
    """`usage.recorded` never fires on the run path, so its sum is not a zero.

    Both committed reports carry `llm_calls: 0` across all eight rows while the
    store shows 8 model calls, because the events API stream contains the event
    exactly zero times. A structurally impossible counter must say NOT MEASURED;
    a 0 there is indistinguishable from a run that made no model call, which is
    FINDINGS.md method note 1 verbatim.
    """

    session = _StubSession(
        [
            {"event_type": "model_call_started", "payload": {}},
            {"event_type": "tool_call_started", "payload": {}},
            {"event_type": "tool_call_started", "payload": {}},
            {"event_type": "model_delta", "payload": {"text": "x"}},
        ]
    )
    measured = bench.measure(session, "r1")
    assert measured["llm_calls"] is None, "0 here means 'not measured'; say so"
    assert measured["input_tokens"] is None
    assert measured["output_tokens"] is None
    # What IS observable stays observable.
    assert measured["tool_calls"] == 2
    assert measured["events"] == 4


def test_the_compare_table_renders_an_unmeasured_number_as_a_dash(bench):
    """A `-` cell and a `0` cell must not look the same in the published table."""

    assert bench._cell_number(None) == "-"
    assert bench._cell_number(0) == "0"
    assert bench._ok_glyph(None) == "?"
    assert bench._ok_glyph(True) == "Y"
    assert bench._ok_glyph(False) == "-"


class TestTheReportRowCarriesTheCorrectnessAxis:
    """The row-writing seam, which was unreachable by any test until now.

    ``collect`` needs a booted app and a paid model call, so the dict it builds
    per task — the dict that carries the entire correctness axis — could not be
    exercised. Deleting ``expected`` or ``outcome_ok`` from it left the whole
    suite green, while every future arm would record no expectation, ``rescore``
    would report ``?`` forever, and the axis would go dark with nothing failing.

    ``build_row`` exists so these four assertions can run. They are cheap and
    they are the difference between an axis that is measured and one that merely
    looks measured.
    """

    @staticmethod
    def _row(bench, answer: str) -> dict:
        task = bench.TASKS[0]
        return bench.build_row(
            task,
            run_id="run_abc",
            record={"status": "completed", "safe_error": None},
            answer=answer,
        )

    def test_the_row_records_the_pattern_it_was_graded_against(self, bench) -> None:
        # Without this key `rescore` cannot re-grade the arm offline, and the
        # row is reported UNKNOWN forever rather than right or wrong.
        row = self._row(bench, "irrelevant")
        assert row["expected"] == bench.TASKS[0].expect.pattern
        assert row["expected"], "an empty expectation grades every answer unknown"

    def test_the_row_records_the_live_verdict(self, bench) -> None:
        task = bench.TASKS[0]
        hit = self._row(bench, f"the answer is {task.expect.pattern}")
        assert "outcome_ok" in hit, "the live verdict is the axis; it must be recorded"

    def test_a_wrong_answer_grades_false_not_missing(self, bench) -> None:
        row = self._row(bench, "a confident answer to a different question")
        assert row["outcome_ok"] is False, (
            "a wrong answer must grade False — never absent, which reads as unknown"
        )

    def test_the_recorded_pattern_round_trips_through_re(self, bench) -> None:
        # `rescore` recompiles this string offline. If a flag lived on the
        # compiled object instead of inside the pattern, the offline verdict
        # would differ from the live one — silently, and in the direction that
        # invents a wrong answer.
        import re

        for task in bench.TASKS:
            row = bench.build_row(
                task,
                run_id="r",
                record={"status": "completed", "safe_error": None},
                answer="",
            )
            recompiled = re.compile(row["expected"])
            probe = f"{task.task_id} probe"
            assert bool(recompiled.search(probe)) == bool(task.expect.search(probe)), (
                f"{task.task_id}: recompiled pattern disagrees with the live one"
            )
