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
anything this file infers: terminal status, wall clock, the count of
`usage.recorded` events (emitted once per usage-bearing LLM call at
`runtime_worker/streaming_executor.py:579`, so it IS the super-step spend), and
gross input/output tokens.

**Read the round counts before the completion counts.** If no task in either
arm spends more than 25 rounds, then the ceiling was never binding, the raise
bought nothing, and this file has done its job by saying so. A wider ceiling is
only worth something if real work was hitting the old one.

PRECONDITION: the stage must be built from the tree under test (README §1b in
tools/desktop-journeys/). A stale stage inverts every number here into nonsense.
The provider key is read from services/ai-backend/.env and only ever reaches the
password field.
"""

from __future__ import annotations

import json
import os
import sys
import time
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


def log(line: str) -> None:
    print(f"  {line}", flush=True)

OUT_DIR = Path(__file__).resolve().parent / "runs"

#: Ordered by how many tool/model rounds the request should honestly need. The
#: point is to span the old ceiling, not to be clever: if even the widest task
#: sits far below 25 rounds, the raise is unjustified and the table says so.
TASKS: tuple[tuple[str, str], ...] = (
    (
        "t1-trivial",
        "Reply with exactly the word: ready.",
    ),
    (
        "t2-three-steps",
        "Work through this in separate steps, and keep each step short. "
        "Step 1: list three primary colours. "
        "Step 2: for each, name one fruit of that colour. "
        "Step 3: reply with the three pairs as a single line.",
    ),
    (
        "t3-todo-driven",
        "Plan this as a checklist first, then do it one item at a time, "
        "marking each item done before starting the next. "
        "Produce: (a) three European capitals, (b) the country of each, "
        "(c) one river in each country, (d) a final table of all three rows.",
    ),
    (
        "t4-long-chain",
        "Do this strictly one step per turn, never batching two steps. "
        "Count from 1 to 12. For each number, on its own step, say whether it "
        "is prime and give one short reason. After the twelfth, reply with the "
        "list of primes you found.",
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
    """Derive round spend and token spend from the run's own event stream."""

    payload = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = payload.get("events", []) if isinstance(payload, dict) else []
    rounds = 0
    tool_calls = 0
    input_tokens = 0
    output_tokens = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type") or event.get("type")
        body = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "usage.recorded":
            rounds += 1
            input_tokens += int(body.get("input_tokens") or 0)
            output_tokens += int(body.get("output_tokens") or 0)
        elif event_type in {"tool_call", "tool_call_started"}:
            tool_calls += 1
    return {
        "llm_calls": rounds,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "events": len(events),
    }


def sign_in_and_key(session: DriverSession) -> None:
    provider, key = byok_provider()
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    session.sign_in_local()
    session.ftue_add_key(provider, key)


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
    for task_id, prompt in TASKS:
        started = time.time()
        if conversation_id is None:
            session.send_first_run_message(prompt)
            conversation_id = wait_for_conversation_id(session)
            before = 0
        else:
            before = len(runs_for_conversation(session, conversation_id))
            session.send(prompt, timeout_s=240)
        run_id = wait_for_new_run(session, conversation_id, before_count=before)
        record = terminal_run(session, run_id)
        row = {
            "task": task_id,
            "run_id": run_id,
            "status": record.get("status"),
            "safe_error": record.get("safe_error"),
            "seconds": round(time.time() - started, 1),
            **measure(session, run_id),
        }
        results.append(row)
        log(
            f"  {task_id}: status={row['status']} llm_calls={row['llm_calls']} "
            f"tool_calls={row['tool_calls']} in={row['input_tokens']} "
            f"out={row['output_tokens']} {row['seconds']}s"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"arm-{limit}.json").write_text(
        json.dumps({"recursion_limit": limit, "tasks": results}, indent=2)
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


def compare() -> int:
    arms = {}
    for path in sorted(OUT_DIR.glob("arm-*.json")):
        data = json.loads(path.read_text())
        arms[data["recursion_limit"]] = data
    if len(arms) < 2:
        print(f"need two arms in {OUT_DIR}; found {sorted(arms)}")
        return 2

    print(f"\n{'task':<16} " + " ".join(f"{('limit=' + k):<34}" for k in sorted(arms)))
    print(f"{'':<16} " + " ".join(f"{'status  llm  tool   in/out    s':<34}" for _ in arms))
    max_rounds = 0
    completions: dict[str, int] = {k: 0 for k in arms}
    for index, (task_id, _) in enumerate(TASKS):
        cells = []
        for key in sorted(arms):
            rows = arms[key]["tasks"]
            row = rows[index] if index < len(rows) else {}
            max_rounds = max(max_rounds, int(row.get("llm_calls") or 0))
            if row.get("status") == "completed":
                completions[key] += 1
            cells.append(
                f"{str(row.get('status'))[:9]:<9}{row.get('llm_calls', '-'):>3} "
                f"{row.get('tool_calls', '-'):>4} "
                f"{row.get('input_tokens', 0):>6}/{row.get('output_tokens', 0):<5} "
                f"{row.get('seconds', '-'):>5}"
            )
        print(f"{task_id:<16} " + " ".join(f"{c:<34}" for c in cells))

    print()
    for key in sorted(arms):
        print(f"  limit={key}: {completions[key]}/{len(TASKS)} completed")
    print(f"\n  peak LLM calls observed in ANY task: {max_rounds}")
    if max_rounds <= 25:
        print(
            "  → the old ceiling of 25 was NEVER reached by this task set, so\n"
            "    raising it bought nothing here. Either the tasks are too small\n"
            "    to be representative, or the raise is unjustified. Do not claim\n"
            "    a completion-rate win from this data."
        )
    else:
        print(
            "  → at least one task exceeded 25 LLM calls, so the inherited\n"
            "    ceiling was binding real work. Compare the completion counts."
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
