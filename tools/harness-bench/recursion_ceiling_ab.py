#!/usr/bin/env python3
"""Measure what the graph step ceiling actually buys, by moving only the ceiling.

The harness program raised `ExecutionHyperparameters.recursion_limit` from
LangGraph's inherited default of **25 super-steps** to an explicit 500. That is
the one change in the program with a plausible double-digit completion-rate
effect — and it was shipped unmeasured. This file is the experiment that can
falsify it.

**The design point: one variable.** Both arms run the SAME stage, the same
tasks, the same model, in the same order. The only difference is
`COPILOT_HP__EXECUTION__RECURSION_LIMIT`. That knob only reaches a supervised
service because of the passthrough fix in `apps/desktop/main/services/
service-env.ts`; before it, this experiment was not runnable at all, which is
why the ceiling shipped unmeasured in the first place.

    BENCH_ARM=25  python tools/harness-bench/recursion_ceiling_ab.py
    BENCH_ARM=500 python tools/harness-bench/recursion_ceiling_ab.py
    python tools/harness-bench/recursion_ceiling_ab.py --compare

Run each arm in its OWN process. Sharing one process would share one boot, and
the ceiling is read once per service start.

**What it records per task**, from the run's own event stream rather than from
anything this file infers: terminal status, wall clock, the tool calls the
stream shows, and — since this revision — whether the answer was RIGHT.

This file scored TERMINATION and nothing else
---------------------------------------------
Every published cost number in FINDINGS.md was measured here, and until this
revision the only outcome column was `status`. A run that ends `completed`
having answered a different question counts as a completion, so a change that
made the model cheaper and worse would have shown up as pure win. That is not
hypothetical on this task set: at limit=500, `t4-long-chain` terminated
`completed` in one model call and its entire final answer was *"**1:** Not
prime — 1 has only one divisor (itself)…"* — one number of twelve, no list of
primes. At limit=25 the same task answered the PREVIOUS task's prompt. Both
counted toward `4/4` and `3/4`.

So every task now declares `expect`, a regex its final assistant text must
match, scored as `outcome_ok` beside — never instead of — the termination
columns. Three rules hold that column honest:

1. **Every expected answer is fixed in this file and derived from a constant
   here**, never typed twice and never invented by the model.
2. **An expectation may be relaxed only by changing the PROMPT and re-running.**
   Never by editing the pattern after reading what a model said. Widening a
   regex until the arm looks good converts a measurement into a decoration.
3. **A row that declares no `expected` is UNKNOWN, not wrong.** The two arms in
   `runs/` predate this column; `rescore.py` reports them `?`, and a scorer that
   turned that into `0/4 correct` would be fabricating a negative.

`t2` and `t3` needed their PROMPTS changed, because as written they had no
unique answer — "three primary colours" is red/yellow/blue *or* red/green/blue
and the fruits were free choice; "three European capitals … one river in each"
is whatever the model picks. Each keeps verbatim the structural instruction that
drives its round count (separate steps · checklist-first-mark-done), so the cost
shape is meant to be comparable — but that is a claim about the new prompts, not
a measurement of them, and it stays unverified until both arms are re-run.
`t4` gained only a sentinel line; `t1` is unchanged.

**Read the round counts before the completion counts.** A wider ceiling is only
worth something if real work was hitting the old one — and `terminal_code`, not
a round count, is what answers that (FINDINGS.md §1).

PRECONDITION: the stage must be built from the tree under test (README §1b in
tools/desktop-journeys/). A stale stage inverts every number here into nonsense.
The provider key is read from services/ai-backend/.env and only ever reaches the
password field.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop-journeys"))

from _lib import (  # noqa: E402
    DriverSession,
    JourneyPlan,
    byok_provider,
    runs_for_conversation,
    wait_for_conversation_id,
    wait_for_new_run,
)
from _workspace_lib import assistant_text  # noqa: E402


def log(line: str) -> None:
    print(f"  {line}", flush=True)


OUT_DIR = Path(__file__).resolve().parent / "runs"

#: How much of the final answer is transcribed into the report. Evidence only —
#: grading always runs against the FULL text from the store, so this cap can
#: never change a verdict. Matched by `rescore.ANSWER_HEAD_CHARS`.
ANSWER_HEAD_CHARS = 200


@dataclass(frozen=True)
class RecursionTask:
    """One prompt, and the answer this file says it must produce.

    Deliberately NOT `heavy_tasks_ab.HeavyTask`: these four tasks have no
    prerequisites, no per-tool-name plan and no fixture substitution, and
    reaching across two standalone entry-point scripts to share four fields
    would buy nothing. What the two types DO share is the contract that matters
    — `expect` is scored separately from every cost column, so a run that
    terminates having answered the wrong question is visible as exactly that.
    """

    task_id: str
    prompt: str
    #: Regex the FINAL assistant text must match. Every literal in it is derived
    #: from a constant below, never typed twice, and never widened after reading
    #: what a model actually said (see the module docstring, rule 2).
    expect: re.Pattern[str]
    #: The claim this task exists to reach. One line, in the report.
    claim: str


def _sequence_expect(token: str, items: tuple[object, ...]) -> re.Pattern[str]:
    """``<token>=a, b, c`` — that list, and nothing longer.

    The trailing ``(?!\\s*,\\s*\\w)`` guard is load-bearing rather than
    decoration. Without it a prefix match accepts ``T4=2,3,5,7,11,13`` — the
    right answer with a wrong one appended — as correct, which is the single
    most likely way this task is failed. ``\\b`` alone does not catch that case;
    ``\\b`` alone is what stops ``T4=2,3,5,7,110``. Both are needed.

    Inline ``(?i)`` rather than an ``re.IGNORECASE`` argument, because the
    pattern STRING is what gets recorded in the arm's report and recompiled
    offline by `rescore.py`, and `re.Pattern.pattern` drops the flags. A flag
    that holds live and vanishes offline changes a verdict silently — in the
    direction that invents a wrong answer.
    """

    body = r"\s*,\s*".join(re.escape(str(item)) for item in items)
    return re.compile(rf"(?i){re.escape(token)}\s*=\s*{body}\b(?!\s*,\s*\w)")


#: t1's whole answer. Held here so the prompt and the expectation cannot drift.
T1_WORD = "ready"

#: t2's arithmetic, in the order the prompt walks it. The ANSWER is computed
#: from these, so no number in the expectation is typed twice.
T2_TERMS = (7, 5, 3, 6)
T2_ANSWER = (T2_TERMS[0] + T2_TERMS[1]) * T2_TERMS[2] - T2_TERMS[3]  # 30

#: t3's rows. The values are deliberately distinct: a tie would give the task
#: two correct orderings, and a task with two correct answers cannot be graded.
T3_ROWS = (("alpha", 4), ("beta", 9), ("gamma", 6))
T3_ORDER = tuple(name for name, _ in sorted(T3_ROWS, key=lambda row: -row[1]))

T4_LIMIT = 12


def _primes_up_to(limit: int) -> tuple[int, ...]:
    """Trial division, so t4's answer is COMPUTED here rather than typed.

    1 is excluded, on the standard convention that a prime has exactly two
    distinct divisors. State that plainly rather than burying it in a regex: it
    is a judgement this file is making, and a model that answers
    ``T4=1,2,3,5,7,11`` grades wrong because of THIS line, not because of
    anything the run did.
    """

    return tuple(
        n for n in range(2, limit + 1) if all(n % d for d in range(2, int(n**0.5) + 1))
    )


T4_PRIMES = _primes_up_to(T4_LIMIT)  # 2, 3, 5, 7, 11

#: Ordered by how many tool/model rounds the request should honestly need. The
#: point is to span the old ceiling, not to be clever: if even the widest task
#: sits far below 25 rounds, the raise is unjustified and the table says so.
TASKS: tuple[RecursionTask, ...] = (
    RecursionTask(
        task_id="t1-trivial",
        prompt=f"Reply with exactly the word: {T1_WORD}.",
        # Anchored end to end: "I am ready" is not "exactly the word", and the
        # anchors are also what stop this pattern matching its own prompt.
        expect=re.compile(rf"(?i)\A\W*{T1_WORD}\W*\Z"),
        claim="the cheapest possible run terminates and answers",
    ),
    RecursionTask(
        task_id="t2-three-steps",
        # PROMPT CHANGED (see module docstring). It used to ask for "three
        # primary colours" and a fruit of each — red/yellow/blue and
        # red/green/blue are both correct, and the fruits were free choice, so
        # there was no answer to check. The step structure that drives the round
        # count is kept verbatim; only the content is now determinate.
        prompt=(
            "Work through this in separate steps, and keep each step short. "
            f"Step 1: add {T2_TERMS[0]} and {T2_TERMS[1]}. "
            f"Step 2: multiply that result by {T2_TERMS[2]}. "
            f"Step 3: subtract {T2_TERMS[3]} from that result, then reply with "
            "exactly one line of the form: T2=<the final number>"
        ),
        expect=re.compile(rf"(?i)T2\s*=\s*{T2_ANSWER}\b"),
        claim="a three-step chain carries a value forward without losing it",
    ),
    RecursionTask(
        task_id="t3-todo-driven",
        # PROMPT CHANGED (see module docstring). "Three European capitals, the
        # country of each, one river in each" has as many right answers as there
        # are capitals. The checklist-and-mark-done instruction — which is what
        # made this task drive `write_todos`, and what made it trip the ceiling
        # at limit=25 — is kept verbatim.
        prompt=(
            "Plan this as a checklist first, then do it one item at a time, "
            "marking each item done before starting the next. Here are three "
            "rows:\n"
            + "".join(f"  {name} {value}\n" for name, value in T3_ROWS)
            + "Produce: (a) the three rows sorted by value, largest first, "
            "(b) a final table of all three rows in that order, and (c) as the "
            "last line, exactly one line of the form: "
            "T3=<the three names in that order, comma separated>"
        ),
        expect=_sequence_expect("T3", T3_ORDER),
        claim="a checklist-driven task reaches the right final ordering",
    ),
    RecursionTask(
        task_id="t4-long-chain",
        # PROMPT UNCHANGED except the last sentence, which used to end "reply
        # with the list of primes you found" — checkable only by reading it.
        prompt=(
            "Do this strictly one step per turn, never batching two steps. "
            f"Count from 1 to {T4_LIMIT}. For each number, on its own step, say "
            "whether it is prime and give one short reason. After the last one, "
            "reply with exactly one line of the form: "
            "T4=<the primes you found, ascending, comma separated>"
        ),
        expect=_sequence_expect("T4", T4_PRIMES),
        claim="the longest chain in the set finishes the work it was given",
    ),
)


def terminal_run(session: DriverSession, run_id: str, timeout_s: int = 240) -> dict:
    """Wait for a terminal run and RETURN it, whatever it is.

    Deliberately not `_lib.wait_for_terminal_run`: that one asserts
    ``status == "completed"``, which is correct for a journey and fatal for a
    benchmark whose entire subject is how often a run does NOT complete.
    """

    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        record = session.transport("GET", f"/v1/agent/runs/{run_id}")
        if isinstance(record, dict):
            last = record
            if record.get("status") in {"completed", "failed", "cancelled"}:
                return record
        time.sleep(0.5)
    last.setdefault("status", "timeout")
    return last


def measure(session: DriverSession, run_id: str) -> dict:
    """What the LIVE event stream can honestly say about one run.

    `llm_calls` used to sum `usage.recorded` events, and it published **0 for
    every task in both committed arms**. That is not a cheap run and it is not a
    matcher typo: counted over the arm-25 session's own `events.jsonl`,
    `usage.recorded` appears **zero times** across all four runs, while
    `context_occupancy.jsonl` records 8 model calls over the same runs. The
    event does not fire on the ordinary run path at all. A counter structurally
    incapable of firing must return `None`, never 0 — a zero here is
    indistinguishable from a run that made no model call, which is precisely the
    instrument failure FINDINGS.md method note 1 opens with.

    `model_call_started` is NOT the substitute it looks like. It fires once per
    RUN on this path — 1, 1, 1, 1 over four runs whose real model-call counts
    are 1, 1, 4, 2 — so counting it would trade an obvious zero for a plausible
    undercount, which is strictly worse. The honest round count is
    `rescore.py`'s `model_calls`, read from `context_occupancy.jsonl` offline.

    The two reports already in `runs/` carry `llm_calls: 0` from before this
    correction. That zero is a dead instrument's, not a measurement; read
    `model_calls` in those rows instead.

    Blind spot of what survives: `tool_calls` counts `tool_call_started`, so it
    DOES see a call the run never finished (which is the case `tool_rounds`
    misses), but it cannot see a call the model emitted that never reached the
    stream at all.
    """

    payload = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = payload.get("events", []) if isinstance(payload, dict) else []
    tool_calls = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type") or event.get("type")
        if event_type in {"tool_call", "tool_call_started"}:
            tool_calls += 1
    return {
        "llm_calls": None,
        "tool_calls": tool_calls,
        "input_tokens": None,
        "output_tokens": None,
        "events": len(events),
    }


def sign_in_and_key(session: DriverSession) -> None:
    provider, key = byok_provider()
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    session.sign_in_local()
    session.ftue_add_key(provider, key)


def build_row(
    task: "RecursionTask",
    *,
    run_id: str,
    record: Mapping[str, object],
    answer: str,
) -> dict:
    """The report row's graded half, factored out so a test can reach it.

    ``collect`` needs a booted app and a paid model call, so everything built
    inside its loop was unreachable by any test — and two of the keys here are
    the whole correctness axis. Deleting either one left the suite green while
    every future arm silently recorded no expectation, ``rescore`` reported
    ``?`` forever, and the axis went dark with nothing failing. That is this
    program's signature defect, so the seam is a function now and
    ``tools/test_harness_bench_recursion_answers.py`` pins its shape.
    """

    return {
        "task": task.task_id,
        "claim": task.claim,
        "run_id": run_id,
        "status": record.get("status"),
        "safe_error": record.get("safe_error"),
        # The OUTCOME, not a proxy for it. `rescore.py` re-derives this from
        # the store afterwards and OVERWRITES it; this live value exists so
        # a paid arm is legible while it is still running, and so a crash
        # mid-arm leaves something readable behind.
        "outcome_ok": bool(task.expect.search(answer)),
        # Recorded so the arm can be re-graded offline for free. A row
        # WITHOUT this key declared no expected answer and must be reported
        # UNKNOWN — never wrong. That is what the two arms in `runs/` are.
        "expected": task.expect.pattern,
        "answer_head": answer.strip()[:ANSWER_HEAD_CHARS],
    }


def collect(session: DriverSession, limit: str) -> None:
    """Drive every task through one booted app and write the arm's report.

    One conversation, not one per task: a fresh chat needs UI mechanics this
    file does not need to own, and both arms see the identical sequence, so the
    growing context is a constant across the comparison rather than a
    confound. It DOES inflate later tasks' input tokens — read the token
    columns as within-arm relative, not as the cost of that task standing alone.
    """

    results: list[dict] = []
    conversation_id: str | None = None
    for task in TASKS:
        started = time.time()
        if conversation_id is None:
            session.send_first_run_message(task.prompt)
            conversation_id = wait_for_conversation_id(session)
            before = 0
        else:
            before = len(runs_for_conversation(session, conversation_id))
            session.send(task.prompt, timeout_s=240)
        run_id = wait_for_new_run(session, conversation_id, before_count=before)
        record = terminal_run(session, run_id)
        answer = assistant_text(session, run_id) or ""
        row = {
            **build_row(task, run_id=run_id, record=record, answer=answer),
            "seconds": round(time.time() - started, 1),
            **measure(session, run_id),
        }
        results.append(row)
        log(
            f"  {task.task_id}: status={row['status']} ok={row['outcome_ok']} "
            f"tool_calls={row['tool_calls']} events={row['events']} "
            f"{row['seconds']}s  (tokens and model calls: rescore.py)"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"arm-{limit}.json").write_text(
        json.dumps(
            {
                "recursion_limit": limit,
                # Recorded, not guessed later. `rescore.py` otherwise finds the
                # session by globbing for the NEWEST directory matching the arm
                # name — and three `journey-bench-recursion-500-*` directories
                # already exist on the box these arms were measured on. That was
                # survivable while the columns were token counts; a row that now
                # carries the TEXT a model produced must not be able to quote
                # another run's answer.
                "user_data_subdir": session.user_data_subdir,
                "tasks": results,
            },
            indent=2,
        )
        + "\n"
    )


def run_arm(limit: str) -> int:
    plan = JourneyPlan(f"bench-recursion-{limit}")
    plan.boot(
        f"source · fresh · recursion_limit={limit}",
        lambda: DriverSession(name=f"bench-recursion-{limit}"),
        setup=sign_in_and_key,
        # The knob only survives into a supervised service because of the
        # COPILOT_HP__ passthrough in apps/desktop/main/services/service-env.ts.
        env={"COPILOT_HP__EXECUTION__RECURSION_LIMIT": limit},
        phases=[
            (
                f"BENCH-{limit}",
                f"every task at recursion_limit={limit}",
                lambda s: collect(s, limit),
            )
        ],
    )
    return plan.finish()


def _cell_number(value: object) -> str:
    """``-`` for a number that was NOT MEASURED. Never 0 — see `measure`."""

    return "-" if value is None else str(value)


def _ok_glyph(verdict: object) -> str:
    """``Y`` right · ``-`` wrong · ``?`` this arm declared no expected answer.

    Same three-way convention as `rescore.ok_cell`, and the third state is the
    point: the arms in `runs/` predate `expect`, and rendering them as wrong
    would be inventing a result rather than reporting one.
    """

    if verdict is True:
        return "Y"
    return "-" if verdict is False else "?"


def compare() -> int:
    arms = {}
    for path in sorted(OUT_DIR.glob("arm-*.json")):
        data = json.loads(path.read_text())
        arms[data["recursion_limit"]] = data
    if len(arms) < 2:
        print(f"need two arms in {OUT_DIR}; found {sorted(arms)}")
        return 2

    width = 42
    print(
        f"\n{'task':<16} "
        + " ".join(f"{('limit=' + k):<{width}}" for k in sorted(arms))
    )
    print(
        f"{'':<16} "
        + " ".join(
            f"{'status   ok  tool  mdl   in/out       s':<{width}}" for _ in arms
        )
    )
    # From `model_calls`, which `rescore.py` derives from the store — NOT from
    # the live `llm_calls`, which is structurally 0 (see `measure`). None here
    # means these reports were never rescored, and that is said out loud rather
    # than collapsed into a peak of zero.
    peak_model_calls: int | None = None
    completions: dict[str, int] = {k: 0 for k in arms}
    for task in TASKS:
        cells = []
        for key in sorted(arms):
            # Keyed by task id, not by position: a report written under an older
            # prompt set must line up by name or not at all.
            rows = {str(r.get("task")): r for r in arms[key]["tasks"]}
            row = rows.get(task.task_id, {})
            calls = row.get("model_calls")
            if isinstance(calls, int):
                peak_model_calls = max(peak_model_calls or 0, calls)
            if row.get("status") == "completed":
                completions[key] += 1
            cells.append(
                f"{str(row.get('status'))[:8]:<8} "
                f"{_ok_glyph(row.get('outcome_ok')):<3} "
                f"{_cell_number(row.get('tool_calls')):>4} "
                f"{_cell_number(row.get('model_calls')):>4} "
                f"{_cell_number(row.get('input_tokens')):>6}/"
                f"{_cell_number(row.get('output_tokens')):<6} "
                f"{_cell_number(row.get('seconds')):>5}"
            )
        print(f"{task.task_id:<16} " + " ".join(f"{c:<{width}}" for c in cells))

    print(
        "\n  ok: Y=matched the answer this arm declared · -=did not match · "
        "?=this arm\n      declared none (UNKNOWN, which is not a failure)"
    )
    for key in sorted(arms):
        rows = arms[key]["tasks"]
        judged = [r for r in rows if r.get("outcome_ok") is not None]
        correct = (
            f"{sum(1 for r in judged if r['outcome_ok'])}/{len(judged)} correct"
            if judged
            else f"correctness UNKNOWN for all {len(rows)} (no expectation recorded)"
        )
        print(f"  limit={key}: {completions[key]}/{len(TASKS)} completed, {correct}")
    print(
        "  → the completion column cannot see a run that ended `completed`\n"
        "    having answered the wrong question. Measured on the arms in runs/:\n"
        "    at limit=500, t4-long-chain completed in one model call and\n"
        "    answered about the number 1 alone. Read both columns."
    )

    if peak_model_calls is None:
        print(
            "\n  peak model calls in ANY task: NOT MEASURED — these reports have\n"
            "    not been rescored, and the live `llm_calls` column is a dead\n"
            "    instrument's zero. Run `rescore.py` before reading anything\n"
            "    into the ceiling question."
        )
    else:
        print(f"\n  peak MODEL CALLS in ANY task: {peak_model_calls}  (from rescore)")
    print(
        "  → model calls do NOT bound super-steps, and a low count is NOT\n"
        "    evidence the ceiling was clear: arm-25's t3-todo-driven was stopped\n"
        "    by the ceiling at 4 model calls, because middleware and tool nodes\n"
        "    each cost steps (~6 + 4/round). `terminal_code` answers the ceiling\n"
        "    question; `rescore.py` lists every run stopped by it."
    )
    return 0


def main() -> int:
    if "--compare" in sys.argv:
        return compare()
    arm = os.environ.get("BENCH_ARM", "").strip()
    if arm not in {"25", "500"}:
        print("set BENCH_ARM=25 or BENCH_ARM=500 (or pass --compare)")
        return 2
    return run_arm(arm)


if __name__ == "__main__":
    raise SystemExit(main())
