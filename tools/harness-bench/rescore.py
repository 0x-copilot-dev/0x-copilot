#!/usr/bin/env python3
"""Score a benchmark arm from the run store the app actually wrote.

The first cut of `recursion_ceiling_ab.py` counted `usage.recorded` off the
events API and got zero for every task — the matcher was wrong, and a broken
instrument reporting 0 looks exactly like a cheap run. This scorer reads the
file-native store instead, which is the same data the product bills from:

    <userData>/agent-data/v1/state/run_usage.jsonl          one row per run
    <userData>/agent-data/v1/state/tool_invocations.jsonl   one row per TRANSITION
    <userData>/agent-data/v1/state/context_occupancy.jsonl  one row per model call

Rescoring is offline and free, so an arm never has to be re-run against a paid
model to fix a measurement mistake.

    python tools/harness-bench/rescore.py arm-25 arm-500
    python tools/harness-bench/rescore.py heavy-arm-25 heavy-arm-500

Every metric below states what it is BLIND to
---------------------------------------------
This is not decoration. `tool_rounds` counted COMPLETED tool invocations and was
structurally unable to observe a run that died with a call still in flight —
which is exactly how the step-ceiling finding was nearly lost (FINDINGS.md §1).
A metric whose blind spot is unstated will eventually be read as if it had none.

======================  ====================================================
metric                  blind to
======================  ====================================================
tool_rounds             a call still IN FLIGHT when the run died. Retained as
                        a rough cost signal only; `orphaned_rounds` is the
                        companion that sees exactly that case.
tool_invocations        a call the model emitted that never reached the
                        ledger at all (rejected before the first row).
orphaned_rounds         a call that a RECONCILER already closed. Measured: the
                        run that tripped the ceiling at limit=25 shows zero
                        orphans, because the blanket handler stamped its open
                        `write_todos` `failed` with a `completed_at`. This
                        catches only a store truncated before reconciliation —
                        `reconciled_rounds` is what catches the other case.
reconciled_rounds       a tool that legitimately returns nothing. It counts
                        terminal rows with an EMPTY `result_summary`, which is
                        the tell FINDINGS.md §2 identified: the three good
                        `write_todos` rows carry a summary and the accused one
                        carries `{}`. A tool whose real result is empty would
                        be counted here wrongly.
model_calls             a model call whose occupancy row failed to write. It
                        is NOT blind to in-flight tool calls, which is why it
                        is the better ceiling proxy than tool_rounds.
peak_parallel           interleaving finer than the timestamp resolution, and
                        it treats a missing `completed_at` as STILL OPEN — so
                        an orphaned call inflates it. Read it next to
                        `orphaned_rounds`, never alone.
delegated_rounds        delegation DEPTH. `task_id` names the subagent type,
                        not its nesting level, and every `context_occupancy`
                        row observed so far carries `graph_scope: "root"`. So
                        this shows THAT delegation happened, and cannot show
                        that `max_delegation_depth` was enforced.
namespaced_tools        a connector tool DROPPED at load time for colliding
                        with another server's name. A dropped tool never
                        produces an invocation row, so the collision it caused
                        is invisible here — only the load-time card list shows
                        it. Zero namespaced tools with one connector attached
                        is therefore consistent with both "namespacing is
                        absent" and "no collision occurred".
peak_result_tokens      a result that was OFFLOADED before assembly: it enters
                        context as a small pointer, so the ledger sees the
                        pointer and not the payload. A low peak can mean the
                        cap held OR that offloading fired first.
budget_notes            which tool exhausted, unless the segment label says.
super_steps_estimate    everything a fit is blind to. It is
                        `6 + 4 * tool_invocations` from the measured fit in
                        `ExecutionHyperparameters.recursion_limit`'s comment
                        (middleware + a subagent); a middleware change
                        invalidates it, and it cannot count steps spent in a
                        round that produced no invocation row.
======================  ====================================================
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "0xCopilot"
HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

#: `ToolInvocationStatus` values that end an invocation. Anything else means the
#: call was still open when the store stopped being written to.
TERMINAL_TOOL_STATUSES = frozenset({"completed", "failed", "cancelled"})

#: The occupancy segment label carrying a tool's result text, and the one the
#: budget middleware injects when a tool name is exhausted.
TOOL_RESULT_LABEL = "agent_runtime.conversation:tool_result"
BUDGET_NOTE_LABEL = "agent_runtime.capabilities:tool_budget_note"

#: The measured super-step fit for THIS graph (RuntimeControlMiddleware + a
#: subagent), from the comment on `ExecutionHyperparameters.recursion_limit`.
SUPER_STEP_BASE = 6
SUPER_STEP_PER_ROUND = 4


def session_dir(arm: str, report: dict | None = None) -> Path | None:
    """The userData dir this arm actually wrote, named rather than guessed.

    A report written by `heavy_tasks_ab.py` records its own
    ``user_data_subdir``, and that is used verbatim. Older reports
    (`arm-25` / `arm-500`) do not, so they fall back to globbing for the
    NEWEST directory matching the arm's DriverSession name — which is a real
    hazard worth naming: re-run an arm and the fallback happily scores the
    newer session against the older report, producing a table in which every
    column is internally consistent and wrong.
    """

    if report is not None:
        subdir = report.get("user_data_subdir")
        if isinstance(subdir, str) and subdir:
            candidate = APP_SUPPORT / subdir
            return candidate if candidate.is_dir() else None

    stem = arm.removeprefix("heavy-arm-").removeprefix("arm-")
    family = "bench-heavy" if arm.startswith("heavy-") else "bench-recursion"
    candidates = sorted(
        (p for p in APP_SUPPORT.glob(f"journey-{family}-{stem}-*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_state(directory: Path, name: str) -> list[dict]:
    path = directory / "agent-data" / "v1" / "state" / name
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line).get("record")
        if isinstance(record, dict):
            rows.append(record)
    return rows


def terminal_codes(directory: Path) -> dict[str, str]:
    """Map run_id → the typed code on its terminal event.

    This exists because the first cut of this scorer inferred "did the ceiling
    bind?" from the count of COMPLETED tool rounds, and that is not the same
    question. A run that trips the ceiling with a call still open never
    completes that call, so it is invisible to a completed-rounds count — the
    scorer reported 3 rounds against a ceiling of 25 and I concluded the
    ceiling was never approached. The run's own `run_failed` event said
    ``recursion_limit_exceeded``. Read the terminal code; never infer it.
    """

    codes: dict[str, str] = {}
    for path in directory.glob("agent-data/v1/workspaces/*/sessions/*/events.jsonl"):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            record = row.get("record", row)
            if record.get("event_type") not in {"run_failed", "run_completed"}:
                continue
            payload = record.get("payload") or {}
            run_id = record.get("run_id")
            if isinstance(run_id, str) and isinstance(payload, dict):
                codes[run_id] = str(payload.get("code") or record.get("event_type"))
    return codes


# ── tool invocations: rows are TRANSITIONS, not calls ────────────────────────
def group_invocations(rows: list[dict]) -> "OrderedDict[str, list[dict]]":
    """Group transition rows by ``invocation_id``, preserving file order.

    ``tool_invocations.jsonl`` is append-only and writes one row per state
    change, so a single call contributes a ``running`` row AND a ``completed``
    row. Counting rows where ``status == "completed"`` happens to equal the
    number of finished calls today, but only by coincidence of there being
    exactly one such row each. Grouping first makes "how many calls" and "how
    did each one end" separate, answerable questions.
    """

    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for row in rows:
        key = str(row.get("invocation_id") or row.get("call_id") or id(row))
        grouped.setdefault(key, []).append(row)
    return grouped


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def peak_parallel(invocations: list[list[dict]]) -> int:
    """Most invocations open at once — the only view of batched execution here.

    A call with no ``completed_at`` is treated as STILL OPEN rather than
    dropped, which is deliberate: the alternative silently hides exactly the
    orphaned call this file exists to surface. It does mean an orphan inflates
    this number, so read it beside ``orphaned_rounds``.
    """

    edges: list[tuple[datetime, int]] = []
    for transitions in invocations:
        start = _stamp(transitions[0].get("started_at"))
        if start is None:
            continue
        end = _stamp(transitions[-1].get("completed_at"))
        edges.append((start, 1))
        if end is not None:
            edges.append((end, -1))
    if not edges:
        return 0
    # Closes before opens at an identical instant: two calls that touch but do
    # not overlap must not read as parallel.
    edges.sort(key=lambda pair: (pair[0], pair[1]))
    open_now = peak = 0
    for _, delta in edges:
        open_now += delta
        peak = max(peak, open_now)
    return peak


def tool_shape(invocations: list[list[dict]]) -> dict:
    """Everything the invocation ledger can say about ONE run."""

    # Deliberately still "invocations that SUCCEEDED", byte-compatible with the
    # previous scorer: FINDINGS.md quotes this column, and silently widening it
    # to include failures would change published numbers without saying so.
    # `tool_invocations` below is the wider count the super-step fit needs.
    succeeded = 0
    orphaned = 0
    reconciled: list[str] = []
    failures: list[str] = []
    delegated = 0
    subagents: list[str] = []
    namespaced: list[str] = []
    by_name: dict[str, set[str]] = {}
    for transitions in invocations:
        last = transitions[-1]
        status = str(last.get("status") or "")
        if status == "completed":
            succeeded += 1
        if status not in TERMINAL_TOOL_STATUSES:
            orphaned += 1
        elif not last.get("result_summary"):
            # Terminal, but with nothing to show for it. FINDINGS.md §2: the
            # `write_todos` accused of throwing never ran — it was in flight
            # when the graph hit its ceiling and a blanket handler closed it.
            # The three genuine rows carry a `result_summary`; that one carries
            # `{}`. This is what `orphaned_rounds` cannot see, because the
            # reconciler leaves no open row behind.
            reconciled.append(str(last.get("tool_name") or "?"))
        if status == "failed":
            failures.append(f"{last.get('tool_name')}:{last.get('safe_error_code')}")
        task_id = last.get("task_id")
        if isinstance(task_id, str) and task_id:
            delegated += 1
            if task_id not in subagents:
                subagents.append(task_id)
        name = str(last.get("tool_name") or "")
        if name.startswith("mcp__"):
            namespaced.append(name)
        slug = last.get("connector_slug")
        if isinstance(slug, str) and slug:
            by_name.setdefault(name, set()).add(slug)
    return {
        "tool_invocations": len(invocations),
        "tool_rounds": succeeded,
        "orphaned_rounds": orphaned,
        "reconciled_rounds": reconciled,
        "peak_parallel": peak_parallel(invocations),
        "delegated_rounds": delegated,
        "subagents": subagents,
        "namespaced_tools": len(namespaced),
        # One tool NAME reached through two different connectors is precisely
        # the collision `mcp__<server>__<tool>` namespacing exists to prevent.
        "tool_name_collisions": sorted(
            name for name, slugs in by_name.items() if len(slugs) > 1
        ),
        "tool_failures": failures,
    }


# ── context occupancy: model calls, result sizes, budget notes ───────────────
def occupancy_shape(rows: list[dict]) -> dict:
    """Per-run model-call count and the largest tool result that entered context.

    ``model_calls`` is the honest round count for the ceiling question: the
    occupancy row is written when the prompt is ASSEMBLED, so a model call that
    went on to emit a tool call the run never finished is still counted here.
    That is the exact case ``tool_rounds`` cannot see.
    """

    peak_tokens = peak_bytes = 0
    budget_notes = 0
    for row in rows:
        for segment in (row.get("segments_json") or {}).get("segments", []):
            if not isinstance(segment, dict):
                continue
            label = str(segment.get("label") or "")
            if label == TOOL_RESULT_LABEL:
                peak_tokens = max(
                    peak_tokens, int(segment.get("estimated_tokens") or 0)
                )
                peak_bytes = max(peak_bytes, int(segment.get("byte_count") or 0))
            elif label == BUDGET_NOTE_LABEL:
                budget_notes += 1
    return {
        "model_calls": len(rows),
        "peak_result_tokens": peak_tokens,
        "peak_result_bytes": peak_bytes,
        # A run stopped by the per-tool-name budget and a run stopped by the
        # step ceiling both end early with work undone. Only this column and
        # `terminal_code` together tell them apart.
        "budget_notes": budget_notes,
    }


def memory_files(directory: Path) -> list[str]:
    """What the agent left in ``/memories/`` — the finish state, checkable free.

    The grant-free heavy tasks write here, so this is how a completed run's
    claim to have done the work is checked WITHOUT re-reading the transcript
    and without another paid run.
    """

    root = directory / "agent-data" / "v1" / "memory"
    if not root.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )


def score(arm: str) -> dict | None:
    report_path = RUNS / f"{arm}.json"
    if not report_path.is_file():
        print(f"  no report at {report_path}")
        return None
    report = json.loads(report_path.read_text())
    directory = session_dir(arm, report)
    if directory is None:
        print(f"  no session dir for {arm}")
        return None

    usage = {r.get("run_id"): r for r in load_state(directory, "run_usage.jsonl")}
    codes = terminal_codes(directory)
    tools: dict[str, list[dict]] = {}
    for row in load_state(directory, "tool_invocations.jsonl"):
        tools.setdefault(str(row.get("run_id")), []).append(row)
    occupancy: dict[str, list[dict]] = {}
    for row in load_state(directory, "context_occupancy.jsonl"):
        occupancy.setdefault(str(row.get("run_id")), []).append(row)

    for task in report["tasks"]:
        run_id = task.get("run_id")
        if not run_id:
            # A task whose prerequisite was absent has no run to score. Filling
            # it with zeros would make "never ran" indistinguishable from "ran
            # and spent nothing", which is method note 1 all over again.
            continue
        u = usage.get(run_id, {})
        grouped = list(group_invocations(tools.get(run_id, [])).values())
        task["input_tokens"] = u.get("input_tokens", 0)
        task["output_tokens"] = u.get("output_tokens", 0)
        task["cached_input_tokens"] = u.get("cached_input_tokens", 0)
        task["total_tokens"] = u.get("total_tokens", 0)
        task["duration_ms"] = u.get("duration_ms", 0)
        task.update(tool_shape(grouped))
        task.update(occupancy_shape(occupancy.get(run_id, [])))
        task["super_steps_estimate"] = SUPER_STEP_BASE + SUPER_STEP_PER_ROUND * len(
            grouped
        )
        task["terminal_code"] = codes.get(run_id, "?")
    report["session_dir"] = str(directory)
    report["memory_files"] = memory_files(directory)
    report_path.write_text(json.dumps(report, indent=2))
    return report


def scored_tasks(report: dict) -> list[dict]:
    """Tasks that actually produced a run. See `score` on why zeros are wrong."""

    return [task for task in report["tasks"] if task.get("run_id")]


def main() -> int:
    arms = sys.argv[1:] or ["arm-25", "arm-500"]
    scored = [r for r in (score(a) for a in arms) if r]
    if not scored:
        return 2

    print(
        f"\n{'arm':<8}{'task':<18}{'status':<11}{'ok':<4}{'calls':>6}{'orph':>5}"
        f"{'par':>4}{'dlg':>4}{'mdl':>5}{'~steps':>7}{'in':>9}{'cached':>9}"
        f"{'out':>7}{'peakres':>8}  failures"
    )
    peak_rounds = peak_steps = peak_result = 0
    for report in scored:
        arm = report["recursion_limit"]
        for task in scored_tasks(report):
            peak_rounds = max(peak_rounds, task["tool_rounds"])
            peak_steps = max(peak_steps, task["super_steps_estimate"])
            peak_result = max(peak_result, task["peak_result_tokens"])
            ok = task.get("outcome_ok")
            print(
                f"{arm:<8}{task['task']:<18}{str(task['status']):<11}"
                f"{('Y' if ok else '-' if ok is False else '?'):<4}"
                f"{task['tool_invocations']:>6}{task['orphaned_rounds']:>5}"
                f"{task['peak_parallel']:>4}{task['delegated_rounds']:>4}"
                f"{task['model_calls']:>5}{task['super_steps_estimate']:>7}"
                f"{task['input_tokens']:>9}{task['cached_input_tokens']:>9}"
                f"{task['output_tokens']:>7}{task['peak_result_tokens']:>8}  "
                f"{','.join(task['tool_failures']) or '-'}"
            )

    print()
    for report in scored:
        rows = scored_tasks(report)
        done = sum(1 for t in rows if t["status"] == "completed")
        # Only tasks that DECLARED an expected answer can be right or wrong.
        # `recursion_ceiling_ab.py` checks none, and printing "0/4 correct" for
        # a set that never asked would be a fabricated negative — the same
        # class of mistake as a broken instrument reporting zero tokens.
        judged = [t for t in rows if t.get("outcome_ok") is not None]
        correct = (
            f"{sum(1 for t in judged if t['outcome_ok'])}/{len(judged)} correct"
            if judged
            else "correctness not checked by this set"
        )
        skipped = len(report["tasks"]) - len(rows)
        total_tokens = sum(t["total_tokens"] for t in rows)
        line = (
            f"  limit={report['recursion_limit']}: {done}/{len(rows)} completed, "
            f"{correct}, {total_tokens} total tokens"
        )
        print(line + (f"  ({skipped} never ran)" if skipped else ""))

    print(f"\n  peak COMPLETED tool rounds in any task: {peak_rounds}")
    print(
        f"  peak ESTIMATED super-steps in any task:  {peak_steps}  "
        f"(fit: {SUPER_STEP_BASE} + {SUPER_STEP_PER_ROUND}/round)"
    )
    print(f"  peak tool result entering context:       {peak_result} tokens")

    orphans = [
        (r["recursion_limit"], t["task"], t["orphaned_rounds"])
        for r in scored
        for t in scored_tasks(r)
        if t["orphaned_rounds"]
    ]
    if orphans:
        print("\n  calls with NO terminal row — the store stopped mid-write:")
        for limit, task, count in orphans:
            print(f"    limit={limit}  {task}  x{count}")

    reconciled = [
        (r["recursion_limit"], t["task"], t["reconciled_rounds"])
        for r in scored
        for t in scored_tasks(r)
        if t["reconciled_rounds"]
    ]
    if reconciled:
        print("\n  calls CLOSED BY RECONCILIATION, not by running (terminal row,")
        print("  empty result_summary) — the shape that made a tool look like it")
        print("  threw when the graph had actually hit its ceiling under it:")
        for limit, task, names in reconciled:
            print(f"    limit={limit}  {task}  {', '.join(names)}")

    budgeted = [
        (r["recursion_limit"], t["task"])
        for r in scored
        for t in scored_tasks(r)
        if t["budget_notes"]
    ]
    if budgeted:
        print("\n  runs that hit the per-TOOL-NAME call budget (NOT the ceiling):")
        for limit, task in budgeted:
            print(f"    limit={limit}  {task}")

    namespaced = sum(t["namespaced_tools"] for r in scored for t in scored_tasks(r))
    collisions = sorted(
        {c for r in scored for t in scored_tasks(r) for c in t["tool_name_collisions"]}
    )
    print(f"\n  MCP tool names carrying an mcp__<server>__<tool> prefix: {namespaced}")
    if collisions:
        print(f"  tool names reached through MORE THAN ONE connector: {collisions}")
    print(
        "  → zero namespaced names on a profile with no connected server is NOT\n"
        "    evidence either way. A dropped colliding tool never writes a row."
    )

    ceiling_hits = [
        (r["recursion_limit"], t["task"])
        for r in scored
        for t in scored_tasks(r)
        if t.get("terminal_code") == "recursion_limit_exceeded"
    ]
    if ceiling_hits:
        print("\n  runs stopped BY THE STEP CEILING (the only reliable signal):")
        for limit, task in ceiling_hits:
            print(f"    limit={limit}  {task}")
        print(
            "  → completed-round counts UNDERCOUNT: a run that trips the ceiling\n"
            "    with a call still open never completes it. Read terminal_code."
        )
    else:
        print("\n  no run was stopped by the step ceiling in any arm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
