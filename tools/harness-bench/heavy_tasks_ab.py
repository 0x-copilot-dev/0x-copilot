#!/usr/bin/env python3
"""A task set heavy enough to reach the claims the four short prompts cannot.

`recursion_ceiling_ab.py` carries four prompts that peak at about five tool
rounds. That was enough to catch the step ceiling binding at 25 — barely, and
only after a scorer rewrite — but five rounds cannot exercise **delegation**,
the **per-tool-name call budget**, the **tool-result cap**, **parallel tool
execution**, or **MCP tool-name namespacing**. Those claims are still unmeasured
because no task in the harness is big enough to touch them.

    BENCH_ARM=25  python tools/harness-bench/heavy_tasks_ab.py
    BENCH_ARM=500 python tools/harness-bench/heavy_tasks_ab.py
    python tools/harness-bench/heavy_tasks_ab.py --compare
    python tools/harness-bench/heavy_tasks_ab.py --plan     # free, no app, no model

    # validate the plumbing for the price of ONE task before paying for an arm:
    BENCH_ARM=500 HEAVY_TASKS=h1-corpus python tools/harness-bench/heavy_tasks_ab.py

Same one-variable design as the recursion file: both arms run the same stage,
the same tasks, the same model, in the same order, in their OWN process, and
differ only in ``COPILOT_HP__EXECUTION__RECURSION_LIMIT``. Score afterwards with
``rescore.py heavy-arm-25 heavy-arm-500`` — scoring is offline, so a measurement
mistake never costs another paid run.

What one arm costs
------------------
Roughly **45-60 model calls**, i.e. ~1.2M *listed* input tokens, ~10k output,
and 5-8 minutes of wall clock. Listed is not billed: 97% of warm input is a
cache read, so the full-price-equivalent is about **150k tokens** (the ~22.3k
cold prompt at full price plus ~2.6k per warm call) — comfortably under $1 per
arm at Sonnet-class list prices. The whole two-arm experiment is therefore about
15x the recursion set and still cheap enough to re-run.

Those are the numbers for a healthy arm. The per-task wait ceiling is 420s, so a
thoroughly stuck arm walks to ~45 minutes before reporting — long, deliberately,
because a benchmark that gives up early records a timeout where the interesting
answer was. Validate the plumbing with `HEAVY_TASKS=h1-corpus` first; it is one
task, well under a minute of model time, and it exercises the whole path from
boot through report to `rescore.py`.

Why these tasks and not livelier ones
-------------------------------------
Four constraints, each of which killed a more interesting design:

1. **Deterministic finish state.** Every task states an answer this file can
   check by regex against the final assistant text (``expect``), and every
   number in it is fixed here rather than invented by the model. A task whose
   round count swings run-to-run cannot support an A/B, and a task with no
   checkable answer repeats the mistake FINDINGS.md documents: measuring a proxy
   and never asking whether the work was actually done. ``outcome_ok`` is the
   outcome metric; ``tool_rounds`` is only a cost signal.
2. **No connector, no OAuth.** A journey can *never* complete an OAuth connect —
   the driver stubs ``shell.openExternal`` (see tools/desktop-journeys/README).
   The MCP namespacing task is therefore declared with
   ``Needs.CONNECTED_MCP`` and reports **blocked**, loudly, on a profile with no
   connected server. It is never silently skipped and never quietly replaced by
   a task that measures something else.
3. **Bounded per-tool-name spend.** ``execution.tool_call_budget`` is **10 calls
   of one tool name per run** (20 at ``deep``). A task planning 12 ``write_file``
   calls does not measure a long chain; it measures the budget cutting the chain
   off, and the two failures look identical from the outside. Every task here
   declares ``planned_calls`` and ``tools/test_harness_bench_heavy_tasks.py``
   fails if any single tool name is planned at or above the budget.
4. **The filesystem grants nothing by default.** See below.

Which tasks need a granted folder, and which do not
---------------------------------------------------
**H1-H5 need no grant.** They work in ``/memories/``, a real mounted route on
the desktop file store (``FileMemoryBackendFactory``), writable through the
model's ordinary ``write_file`` / ``read_file`` / ``edit_file`` and persisted at
``<userData>/agent-data/v1/memory/`` where ``rescore.py`` can inspect the finish
state offline, for free.

Two traps that shaped those prompts, both structural rather than stylistic:

* A **host-absolute** path (``/bench/part-01.md`` — anything whose first segment
  is not one of ``HostPathClassifier.VIRTUAL_ROOTS``) is claimed by the
  workspace backend and *refused* without a grant. A prompt that names one
  measures a refusal, not a chain. Hence every grant-free prompt addresses
  ``/memories/<flat-name>``, and the design test asserts it.
* ``FileMemoryBackend.grep`` and ``.glob`` return **empty results, not errors**.
  A prompt that says "search /memories/" gets a green empty answer — the exact
  empty-success shape that produced the ``ls ~/Downloads`` defect. The prompts
  therefore say ``ls`` and ``read``, never grep or glob, and the design test
  asserts that too.

**H6 needs a granted folder** and says so. Its fixture is a 2,600-row CSV this
file writes to a temp dir — free to produce, unlike making the model type one —
and it is the only task that can push ``reads.default_line_limit`` (2000) and
the tool-result cap. The grant is arranged in ``setup`` through the app's REAL
native picker (``_workspace_lib.attach_folder``, macOS AppleScript). If the host
denies Accessibility the picker cannot be driven, and H6 records **skipped**
with that reason rather than running a smaller task under the same name.

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
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop-journeys"))

from _lib import (  # noqa: E402
    DriverSession,
    JourneyPlan,
    PhaseSkipped,
    byok_provider,
    preflight_staged_runtime,
    runs_for_conversation,
    wait_for_conversation_id,
    wait_for_new_run,
)
from _workspace_lib import (  # noqa: E402
    assistant_text,
    attach_folder,
)

OUT_DIR = Path(__file__).resolve().parent / "runs"

#: ``ExecutionHyperparameters.tool_call_budget``. Held as a literal because this
#: file must not import from a service's ``src`` (CLAUDE.md: no deployable
#: component imports another's source), and asserted against the shipped
#: hyperparameter document by the design test so the two cannot drift.
TOOL_CALL_BUDGET = 10

#: ``HostPathClassifier.VIRTUAL_ROOTS`` — the POSIX-absolute first segments that
#: stay inside this process. Anything else with a leading ``/`` is host-shaped,
#: is claimed by the workspace backend, and is refused without a grant.
VIRTUAL_ROOTS = frozenset(
    {
        "workspace",
        "memories",
        "policies",
        "skills",
        "drafts",
        "subagents",
        "large_tool_results",
        "mcp",
    }
)


class Needs(StrEnum):
    """What a task requires from the machine BEFORE it can mean anything.

    The distinction is the whole point of declaring it: a task whose
    prerequisite is absent must record ``skipped``/``blocked`` under its own
    name, never be quietly dropped and never be replaced by an easier task that
    happens to run. That is how a set of six tasks becomes a set of four while
    still printing six rows.
    """

    #: Runs on a virgin profile with a provider key and nothing else.
    NOTHING = "nothing"
    #: Needs a folder grant minted through the app's own native picker.
    HOST_GRANT = "host_grant"
    #: Needs a REAL connected MCP server — impossible for a journey to arrange
    #: (the driver suppresses the OAuth browser handoff), so this is a
    #: precondition of the profile, never a step.
    CONNECTED_MCP = "connected_mcp"


@dataclass(frozen=True)
class HeavyTask:
    """One measured prompt, with everything needed to judge it stated up front.

    ``expect`` is what makes this a task set rather than a token burner: the
    run's own final text must match it, and ``outcome_ok`` records whether it
    did. FINDINGS.md's method note 2 is the reason — a proxy metric must be
    checked against the thing it proxies, and here the thing it proxies is "did
    the agent actually do the work".
    """

    task_id: str
    needs: Needs
    #: ``str.format``-ready; ``{ledger}`` is substituted with the fixture path.
    prompt: str
    #: Planned calls per tool NAME. Read by the design test against
    #: ``TOOL_CALL_BUDGET``; a plan at or above it measures the budget, not the
    #: task.
    planned_calls: Mapping[str, int]
    #: Regex the final assistant text must match for the task to count as done.
    expect: re.Pattern[str]
    #: The claim this task exists to reach. One line, in the report.
    claim: str
    #: Substituted into ``prompt``; filled at run time for HOST_GRANT tasks.
    fixture_keys: tuple[str, ...] = field(default=())


#: The corpus every grant-free task works over. Held here, not in the prompt
#: text, so the expected answers below are derived rather than typed twice.
CORPUS: tuple[tuple[str, str, int], ...] = (
    ("01", "ada", 7),
    ("02", "lin", 3),
    ("03", "ada", 8),
    ("04", "omar", 9),
    ("05", "lin", 2),
    ("06", "omar", 4),
)

CORPUS_TOTAL = sum(hours for _, _, hours in CORPUS)  # 33
CORPUS_HIGH = tuple(part for part, _, hours in CORPUS if hours > 5)  # 01, 03, 04


def _corpus_table() -> str:
    return "\n".join(
        f"  /memories/bench-part-{part}.md  ->  part-{part} owner={owner} hours={hours}"
        for part, owner, hours in CORPUS
    )


#: Ordered by the state each consumes, exactly like a journey's phases: H2 reads
#: what H1 wrote, H3 edits it, H4 delegates over it, H5 walks it one step per
#: turn. One conversation, so the growing context is a constant across arms
#: rather than a confound — but it DOES inflate later tasks' input tokens, so
#: read the token columns as within-arm relative.
TASKS: tuple[HeavyTask, ...] = (
    HeavyTask(
        task_id="h1-corpus",
        needs=Needs.NOTHING,
        prompt=(
            "Create exactly six memory files, one per tool call, using write_file. "
            "Use these exact paths and these exact one-line contents — no heading, "
            "no extra lines, no commentary inside the files:\n"
            f"{_corpus_table()}\n"
            "Do not batch them into one call and do not use grep or glob. "
            "When all six exist, reply with exactly: CORPUS OK"
        ),
        planned_calls={"write_file": 6},
        expect=re.compile(r"CORPUS OK", re.IGNORECASE),
        claim="a 6-round write chain completes at all (baseline for H2-H5)",
    ),
    HeavyTask(
        task_id="h2-crossref",
        needs=Needs.NOTHING,
        prompt=(
            "Now cross-reference what you just wrote. List /memories/ with ls, then "
            "read each of the six bench-part files with read_file, one call per "
            "file. Do not use grep or glob — they return empty results on this "
            "route, which is not the same as there being nothing there. "
            "Then reply with exactly one line of the form:\n"
            "  TOTAL=<sum of every hours value> TOP=<owner with the most hours>"
        ),
        planned_calls={"ls": 1, "read_file": 6},
        expect=re.compile(rf"TOTAL\s*=\s*{CORPUS_TOTAL}\b.*TOP\s*=\s*ada\b", re.I),
        claim="a 7-round read/aggregate chain returns the right answer",
    ),
    HeavyTask(
        task_id="h3-transform",
        needs=Needs.NOTHING,
        prompt=(
            "For every bench-part file whose hours value is greater than 5, use "
            "edit_file to append the text ' flag=high' to the end of its single "
            "line. Change nothing else and touch no other file. "
            "Then reply with exactly one line of the form:\n"
            "  CHANGED=<the part numbers you edited, ascending, comma separated>"
        ),
        planned_calls={"edit_file": len(CORPUS_HIGH)},
        expect=re.compile(r"CHANGED\s*=\s*" + r"\s*,\s*".join(CORPUS_HIGH)),
        claim="a conditional multi-file transform selects the right subset",
    ),
    HeavyTask(
        task_id="h4-delegate",
        needs=Needs.NOTHING,
        prompt=(
            "Delegate this rather than doing it yourself. Launch one subagent per "
            "owner (ada, lin, omar). Each subagent must read only that owner's "
            "bench-part files from /memories/ and report that owner's total hours. "
            "When every subagent has reported, reply with exactly one line:\n"
            "  ADA=<n> LIN=<n> OMAR=<n>"
        ),
        planned_calls={"task": 3},
        expect=re.compile(r"ADA\s*=\s*15\b.*LIN\s*=\s*5\b.*OMAR\s*=\s*13\b", re.I),
        claim="delegation actually happens, and its depth/concurrency are visible",
    ),
    HeavyTask(
        task_id="h5-longchain",
        needs=Needs.NOTHING,
        prompt=(
            "Do this strictly one step per turn, never batching two steps into one "
            "message, and keep every step short. For each of the six bench-part "
            "files in ascending order: read it, then on the NEXT step write a "
            "one-line summary to /memories/bench-summary-<the same two digits>.md "
            "in the form 'part-<nn> <owner> <hours>'. That is twelve steps. "
            "After the twelfth, reply with exactly: CHAIN DONE 12"
        ),
        planned_calls={"read_file": 6, "write_file": 6},
        expect=re.compile(r"CHAIN DONE 12"),
        claim="a 12-round chain spans the old ceiling of 25 super-steps",
    ),
    HeavyTask(
        task_id="h6-bigread",
        needs=Needs.HOST_GRANT,
        prompt=(
            "Read the file {ledger} and answer from its contents only. It has a "
            "header row and 2600 data rows. Reply with exactly one line of the "
            "form:\n"
            "  EMEA=<sum of the amount column over rows whose region is EMEA>\n"
            "Read that exact path directly — do not list the directory and do not "
            "guess. The file is longer than one read returns, so page through it "
            "with the offset argument until you have seen every row."
        ),
        planned_calls={"read_file": 4},
        expect=re.compile(r"EMEA\s*=\s*{emea}"),
        claim="a >2000-line read pages correctly and pushes the tool-result cap",
        fixture_keys=("ledger", "emea"),
    ),
    HeavyTask(
        task_id="h7-mcp-namespace",
        needs=Needs.CONNECTED_MCP,
        prompt=(
            "List the connected MCP servers with ls /mcp, then call one read-only "
            "tool on each connected server. Reply with exactly one line listing "
            "the tool names you called, comma separated, in the form:\n"
            "  CALLED=<name>,<name>"
        ),
        planned_calls={"ls": 2, "call_mcp_tool": 4},
        expect=re.compile(r"CALLED\s*=\s*\S+"),
        claim="whether MCP tool names are namespaced per server (steal #4)",
    ),
)


def log(line: str) -> None:
    print(f"  {line}", flush=True)


#: Env var pinning WHICH tasks this arm runs (comma/space separated).
TASK_SELECTOR_ENV = "HEAVY_TASKS"


def selected_tasks() -> tuple[HeavyTask, ...]:
    """The tasks ``HEAVY_TASKS`` pins, or all of them.

    Exists so validating the harness costs ONE task instead of a whole arm::

        BENCH_ARM=500 HEAVY_TASKS=h1-corpus python .../heavy_tasks_ab.py

    Same fail-loud contract as ``JOURNEY_PHASES``: an id matching no declared
    task RAISES rather than running nothing. A caller that pins a renamed task
    and gets a clean empty run has proven nothing while reporting success —
    which is the precise pathology this whole harness exists to not have.

    The tasks consume each other's state on purpose (H2 reads what H1 wrote), so
    pinning a later one ALONE is a different measurement from the same task run
    in order. Use it to validate the plumbing or to bisect, never to "just
    re-run the expensive one".
    """

    raw = os.environ.get(TASK_SELECTOR_ENV, "").strip()
    if not raw:
        return TASKS
    wanted = {token.strip().lower() for token in raw.replace(",", " ").split() if token}
    kept = tuple(task for task in TASKS if task.task_id.lower() in wanted)
    unmatched = sorted(wanted - {task.task_id.lower() for task in kept})
    if unmatched:
        raise SystemExit(
            f"{TASK_SELECTOR_ENV} named {', '.join(unmatched)}, which this set "
            f"does not declare. Known ids: {', '.join(t.task_id for t in TASKS)}"
        )
    return kept


# ── the H6 fixture ───────────────────────────────────────────────────────────
#: 2,600 data rows: comfortably over ``reads.default_line_limit`` (2000), so a
#: single read cannot see the file and the model has to page. Generated from a
#: fixed arithmetic rule rather than a random seed, so the expected answer is
#: derived here and is identical on every machine and in every arm.
LEDGER_ROWS = 2_600
LEDGER_REGIONS = ("EMEA", "AMER", "APAC", "EMEA")


def ledger_lines() -> list[str]:
    lines = ["row,region,amount"]
    for index in range(LEDGER_ROWS):
        region = LEDGER_REGIONS[index % len(LEDGER_REGIONS)]
        lines.append(f"{index + 1},{region},{index % 97}")
    return lines


def ledger_emea_total() -> int:
    return sum(
        index % 97
        for index in range(LEDGER_ROWS)
        if LEDGER_REGIONS[index % len(LEDGER_REGIONS)] == "EMEA"
    )


def write_ledger(root: Path) -> Path:
    path = root / "ledger.csv"
    path.write_text("\n".join(ledger_lines()) + "\n", encoding="utf-8")
    return path


# ── driving one arm ──────────────────────────────────────────────────────────
def terminal_run(session: DriverSession, run_id: str, timeout_s: int = 420) -> dict:
    """Wait for a terminal run and RETURN it, whatever it is.

    Deliberately not ``_lib.wait_for_terminal_run``: that one asserts
    ``status == "completed"``, which is correct for a journey and fatal for a
    benchmark whose entire subject is how often a run does NOT complete. The
    budget is longer than the recursion file's 240s because these tasks are
    ten times the length.
    """

    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        record = session.transport("GET", f"/v1/agent/runs/{run_id}")
        if isinstance(record, dict):
            last = record
            if record.get("status") in {"completed", "failed", "cancelled"}:
                return record
        time.sleep(1.0)
    last.setdefault("status", "timeout")
    return last


def measure(session: DriverSession, run_id: str) -> dict:
    """Derive round and token spend from the run's own event stream.

    A LOWER BOUND on everything, and knowingly so: this is the live read that
    keeps a running arm legible. ``rescore.py`` re-derives all of it from the
    run store afterwards, offline, and that is the number to quote.

    **Tokens are ``None`` here, not 0, and that is the whole point.** This
    function used to sum ``usage.recorded`` events and print ``in=0 out=0`` on
    every task of every arm — the exact instrument failure this program's own
    method notes open with: *"a broken instrument reporting zero is
    indistinguishable from a genuinely cheap run."* It survived the rewrite that
    note describes because the reader was left in place as a "lower bound".

    ``usage.recorded`` is a **Generative Surfaces v2 ledger event**, not a
    per-model-call usage event on the run stream. `streaming_executor` returns
    early on ``if not surfaces_v2_enabled``, and the `handlers/run` emitter
    meters only the VIEW_SHAPING spec-generation path. So on the ordinary run
    path the event is not merely absent from a given run — it cannot fire, and
    the sum is structurally 0 forever. Verified against a live arm whose store
    recorded 20,287 input tokens while this read reported ``in=0``.

    ``llm_calls`` now counts ``model_call_started``, which the run path does
    emit, and remains a lower bound because a call that dies before its start
    event is invisible to it.
    """

    payload = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = payload.get("events", []) if isinstance(payload, dict) else []
    model_calls = 0
    tool_calls = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type") or event.get("type")
        if event_type == "model_call_started":
            model_calls += 1
        elif event_type in {"tool_call", "tool_call_started"}:
            tool_calls += 1
    return {
        "llm_calls": model_calls,
        "tool_calls": tool_calls,
        # Not observable from the event stream — see the docstring. ``rescore.py``
        # reads them off ``run_usage.jsonl``, which is where they actually live.
        "input_tokens": None,
        "output_tokens": None,
        "events": len(events),
    }


class Arm:
    """One booted app driving every task, and the report it writes."""

    def __init__(self, session: DriverSession, limit: str) -> None:
        self.session = session
        self.limit = limit
        self.results: list[dict] = []
        self.conversation_id: str | None = None
        #: Substitutions available to a task's prompt, filled by ``setup``.
        self.fixtures: dict[str, str] = {}
        #: Prerequisites ``setup`` POSITIVELY confirmed. Fail-closed on purpose:
        #: a prerequisite is present only when something checked and said so.
        #: Deriving it from the absence of a ``blocked`` entry instead means a
        #: setup that never ran — an exception, an early return, a reordering —
        #: silently promotes every gated task to runnable, and H7 then measures
        #: MCP namespacing on a profile with no connectors and reports it.
        self.available: set[Needs] = set()
        #: Why a prerequisite is absent, keyed by ``Needs``. Recorded rather
        #: than raised: an absent grant must cost H6 and nothing else.
        self.blocked: dict[Needs, str] = {}

    def prerequisite(self, task: HeavyTask) -> str | None:
        """The reason ``task`` cannot run, or ``None``."""

        if task.needs is Needs.NOTHING:
            return None
        if task.needs not in self.available:
            return self.blocked.get(
                task.needs, f"{task.needs} was never confirmed present"
            )
        missing = [key for key in task.fixture_keys if key not in self.fixtures]
        if missing:
            return f"fixture keys absent: {', '.join(missing)}"
        return None

    def send(self, prompt: str) -> str:
        if self.conversation_id is None:
            self.session.send_first_run_message(prompt)
            self.conversation_id = wait_for_conversation_id(self.session)
            before = 0
        else:
            before = len(runs_for_conversation(self.session, self.conversation_id))
            self.session.send(prompt, timeout_s=420)
        return wait_for_new_run(self.session, self.conversation_id, before_count=before)

    def run_task(self, task: HeavyTask) -> dict:
        started = time.time()
        reason = self.prerequisite(task)
        if reason is not None:
            # NOT a completed row with zero rounds — that is indistinguishable
            # from a task that ran and did nothing, which is the instrument
            # failure FINDINGS.md's method note 1 is about.
            row = {
                "task": task.task_id,
                "needs": str(task.needs),
                "claim": task.claim,
                "status": "blocked" if task.needs is Needs.CONNECTED_MCP else "skipped",
                "reason": reason,
                "run_id": None,
                "outcome_ok": None,
                "seconds": 0.0,
            }
            log(f"  {task.task_id}: {row['status']} — {reason}")
            return row

        # Substituted ONLY for a task that declares fixture keys. Formatting
        # every prompt would work today and break the first time someone writes
        # a regex quantifier like ``\d{2}`` — ``str.format`` reads that as a
        # field name and raises, in a paid run, after the money is spent.
        prompt, pattern = task.prompt, task.expect
        if task.fixture_keys:
            prompt = prompt.format(**self.fixtures)
            pattern = re.compile(
                task.expect.pattern.format(**self.fixtures), task.expect.flags
            )
        run_id = self.send(prompt)
        record = terminal_run(self.session, run_id)
        answer = assistant_text(self.session, run_id) or ""
        row = {
            "task": task.task_id,
            "needs": str(task.needs),
            "claim": task.claim,
            "run_id": run_id,
            "status": record.get("status"),
            "safe_error": record.get("safe_error"),
            # The OUTCOME, not a proxy for it. A run can complete having done
            # none of the work; only this column says whether it did.
            "outcome_ok": bool(pattern.search(answer)),
            "answer_head": answer.strip()[:200],
            "seconds": round(time.time() - started, 1),
            **measure(self.session, run_id),
        }
        # "tokens=via rescore" rather than "in=0 out=0": a zero here would read
        # as a free run, which is the failure mode the method notes open with.
        log(
            f"  {task.task_id}: status={row['status']} ok={row['outcome_ok']} "
            f"llm_calls={row['llm_calls']} tool_calls={row['tool_calls']} "
            f"tokens=via rescore.py {row['seconds']}s"
        )
        return row

    def collect(self, tasks: tuple[HeavyTask, ...] = TASKS) -> None:
        for task in tasks:
            self.results.append(self.run_task(task))
            # Written after EVERY task, not once at the end. An arm is minutes
            # of paid model time; a crash in task six must not throw away the
            # five that already ran, because re-running them costs real money.
            self.write_report()

    def write_report(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"heavy-arm-{self.limit}.json").write_text(
            json.dumps(
                {
                    "recursion_limit": self.limit,
                    # Recorded, not guessed later. ``rescore.py`` used to find
                    # the session by globbing for the newest directory matching
                    # the arm name, which silently scores a LATER run of the
                    # same arm if one exists. Naming it here removes that.
                    "user_data_subdir": self.session.user_data_subdir,
                    "tool_call_budget": TOOL_CALL_BUDGET,
                    "tasks": self.results,
                },
                indent=2,
            )
            # Trailing newline so a committed report does not get rewritten by
            # `end-of-file-fixer` and `prettier` on every single commit.
            + "\n"
        )


def sign_in_key_and_grant(arm: Arm, tasks: tuple[HeavyTask, ...] = TASKS) -> None:
    """Sign in, add the BYOK key, and arrange the H6 folder grant.

    The grant is attempted, never assumed. Attaching is the ONLY way in and it
    is a NATIVE dialog: when the host denies the controlling process
    Accessibility, no keystroke can reach that sheet. That records against H6
    and costs nothing else.

    **Only attempted when a selected task actually needs it.** Five of the seven
    tasks declare ``Needs.NOTHING``, so a run pinned to those was opening a
    native macOS folder picker, failing to drive it, and printing a BLOCKED line
    for a claim nothing in the run was measuring — noise that reads like a
    broken harness. ``tasks`` is the already-resolved pin, so the question is
    answered from the same list the arm is about to execute rather than from the
    full set.
    """

    session = arm.session
    provider, key = byok_provider()
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    session.sign_in_local()
    session.ftue_add_key(provider, key)

    if not any(task.needs is Needs.HOST_GRANT for task in tasks):
        return

    fixture_root = Path(tempfile.mkdtemp(prefix="heavy-bench-")).resolve()
    ledger = write_ledger(fixture_root)
    try:
        grant_id = attach_folder(
            session, fixture_root, mode="read_only", label="harness-bench heavy"
        )
    except Exception as exc:  # noqa: BLE001 — an absent grant is a verdict
        arm.blocked[Needs.HOST_GRANT] = (
            f"the native folder picker could not be driven: {exc!r}"[:300]
        )
        log(f"  folder grant BLOCKED: {arm.blocked[Needs.HOST_GRANT]}")
        return
    log(f"  folder grant {grant_id[:8]}… over {fixture_root}")
    arm.fixtures["ledger"] = str(ledger)
    arm.fixtures["emea"] = str(ledger_emea_total())
    arm.available.add(Needs.HOST_GRANT)


#: ``McpAuthState.AUTHENTICATED`` / ``AUTH_SKIPPED`` — the two states in which a
#: server's tools are actually callable. Held as literals rather than imported:
#: this file must not import a service's ``src``.
USABLE_AUTH_STATES = frozenset({"authenticated", "auth_skipped"})


def connected_mcp_servers(session: DriverSession) -> list[str]:
    """Names of servers this profile can ACTUALLY call tools on.

    Read as a precondition, never arranged: a journey cannot finish an OAuth
    connect because the driver replaces ``shell.openExternal`` with a recorder.
    Connect by hand into ``journey-bench-heavy-<arm>-reuse`` first (README:
    "A journey can NEVER complete an OAuth connect").

    ``enabled`` alone is not the question and neither is ``health``: a row can
    be enabled and healthy while unauthenticated, and its tools then never reach
    the model. The auth state is what decides whether H7 has anything to measure.
    """

    try:
        payload = session.transport("GET", "/v1/mcp/servers")
    except Exception:  # noqa: BLE001 — absence is an answer, not an error
        return []
    servers = payload.get("servers", []) if isinstance(payload, dict) else []
    return [
        str(server.get("name") or server.get("display_name") or "?")
        for server in servers
        if isinstance(server, dict)
        and server.get("enabled")
        and str(server.get("auth_state") or "") in USABLE_AUTH_STATES
    ]


def run_arm(limit: str) -> int:
    # Before the plan, not inside the factory. ``JourneyPlan.boot`` catches
    # everything a factory raises and records the group as FAILED — so a merely
    # ABSENT stage would report a defect. A missing local prerequisite is exit
    # 3, and that distinction is the whole contract of `PhaseSkipped`.
    preflight_staged_runtime()
    # Resolve the pin BEFORE the boot, for the same reason `JourneyPlan` pins
    # phases before its factory: a mistyped id must not cost initdb, migrations
    # and three uvicorns — ~110s of boot — to then raise inside the phase.
    pinned = selected_tasks()
    if pinned is not TASKS:
        log(f"pinned to {len(pinned)} task(s): {', '.join(t.task_id for t in pinned)}")
    plan = JourneyPlan(f"bench-heavy-{limit}")
    holder: dict[str, Arm] = {}

    def factory() -> DriverSession:
        session = DriverSession(name=f"bench-heavy-{limit}", fresh=_fresh())
        holder["arm"] = Arm(session, limit)
        return session

    def setup(_: DriverSession) -> None:
        arm = holder["arm"]
        sign_in_key_and_grant(arm, pinned)
        # Same rule as the folder grant: do not report a claim blocked when
        # nothing in this run measures it.
        if not any(task.needs is Needs.CONNECTED_MCP for task in pinned):
            return
        servers = connected_mcp_servers(arm.session)
        if len(servers) < 2:
            arm.blocked[Needs.CONNECTED_MCP] = (
                f"needs two connected MCP servers to see a name collision; "
                f"this profile has {len(servers)}. Connect them by hand into "
                f"journey-bench-heavy-{limit}-reuse and re-run with "
                "BENCH_REUSE_PROFILE=1 — a journey cannot complete an OAuth "
                "connect (tools/desktop-journeys/README.md)."
            )
        else:
            arm.available.add(Needs.CONNECTED_MCP)
            log(f"  connected MCP servers: {', '.join(servers)}")

    plan.boot(
        f"source · recursion_limit={limit} · heavy task set",
        factory,
        setup=setup,
        # The knob only survives into a supervised service because of the
        # COPILOT_HP__ passthrough in apps/desktop/main/services/service-env.ts.
        env={"COPILOT_HP__EXECUTION__RECURSION_LIMIT": limit},
        phases=[
            (
                f"HEAVY-{limit}",
                f"every heavy task at recursion_limit={limit}",
                lambda _: holder["arm"].collect(pinned),
            )
        ],
    )
    return plan.finish()


def _fresh() -> bool:
    """A fresh profile unless the caller is reusing a hand-connected one.

    Fresh is the default and the right default: it is the only way both arms
    start from identical state. ``BENCH_REUSE_PROFILE=1`` opts into the
    ``-reuse`` subdir so H7 can see a connection made by hand — and it is opt-in
    precisely because reusing carries every previous run's memory files into H1.
    """

    return os.environ.get("BENCH_REUSE_PROFILE", "").strip() not in {"1", "true", "yes"}


# ── reporting ────────────────────────────────────────────────────────────────
def plan_only() -> int:
    """Print the task plan and its design constraints. No app, no model, free."""

    print(f"\n{'task':<18}{'needs':<14}{'planned calls':<34}claim")
    for task in TASKS:
        calls = ", ".join(f"{n}x{c}" for n, c in task.planned_calls.items())
        print(f"{task.task_id:<18}{str(task.needs):<14}{calls:<34}{task.claim}")
    worst = max(
        ((n, c) for t in TASKS for n, c in t.planned_calls.items()),
        key=lambda pair: pair[1],
    )
    print(
        f"\n  heaviest single tool name: {worst[0]} x{worst[1]} "
        f"(per-run budget is {TOOL_CALL_BUDGET})"
    )
    print(f"  H6 ledger: {LEDGER_ROWS} data rows, EMEA total {ledger_emea_total()}")
    grantless = sum(1 for t in TASKS if t.needs is Needs.NOTHING)
    print(
        f"  {grantless}/{len(TASKS)} tasks need NO grant and NO connector; "
        f"{sum(1 for t in TASKS if t.needs is Needs.HOST_GRANT)} need a folder "
        f"grant; {sum(1 for t in TASKS if t.needs is Needs.CONNECTED_MCP)} need "
        "a hand-connected MCP profile."
    )
    return 0


def compare() -> int:
    arms = {}
    for path in sorted(OUT_DIR.glob("heavy-arm-*.json")):
        data = json.loads(path.read_text())
        arms[data["recursion_limit"]] = data
    if len(arms) < 2:
        print(f"need two heavy arms in {OUT_DIR}; found {sorted(arms)}")
        return 2

    keys = sorted(arms)
    print(f"\n{'task':<18} " + " ".join(f"{('limit=' + k):<30}" for k in keys))
    print(
        f"{'':<18} " + " ".join(f"{'status    ok  llm tool     s':<30}" for _ in keys)
    )
    done: dict[str, int] = {k: 0 for k in keys}
    correct: dict[str, int] = {k: 0 for k in keys}
    ran: dict[str, int] = {k: 0 for k in keys}
    for index, task in enumerate(TASKS):
        cells = []
        for key in keys:
            rows = arms[key]["tasks"]
            row = rows[index] if index < len(rows) else {}
            if row.get("run_id"):
                ran[key] += 1
            if row.get("status") == "completed":
                done[key] += 1
            if row.get("outcome_ok"):
                correct[key] += 1
            cells.append(
                f"{str(row.get('status'))[:9]:<10}"
                f"{('Y' if row.get('outcome_ok') else '-' if row.get('run_id') else ' '):<3}"
                f"{row.get('llm_calls', '-'):>4}{row.get('tool_calls', '-'):>5}"
                f"{row.get('seconds', '-'):>7}"
            )
        print(f"{task.task_id:<18} " + " ".join(f"{c:<30}" for c in cells))

    print()
    for key in keys:
        print(
            f"  limit={key}: {done[key]}/{ran[key]} completed, "
            f"{correct[key]}/{ran[key]} CORRECT"
        )
    print(
        "\n  Read the CORRECT column, not the completed column. A run that ends\n"
        "  `completed` having produced the wrong answer is the failure mode a\n"
        "  completion-rate metric cannot see — the same shape as the round count\n"
        "  that could not see the ceiling it was measuring (FINDINGS.md §1)."
    )
    return 0


def main() -> int:
    if "--plan" in sys.argv:
        return plan_only()
    if "--compare" in sys.argv:
        return compare()
    arm = os.environ.get("BENCH_ARM", "").strip()
    if arm not in {"25", "500"}:
        print("set BENCH_ARM=25 or BENCH_ARM=500 (or pass --plan / --compare)")
        return 2
    try:
        return run_arm(arm)
    except PhaseSkipped as exc:
        print(
            json.dumps(
                {
                    "journey": f"bench-heavy-{arm}",
                    "outcome": "skipped",
                    "reason": str(exc),
                }
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
