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
model to fix a measurement mistake. That extends to CORRECTNESS: each row records
the pattern its answer was graded against, and the answer itself is re-read here
from the session's `events.jsonl`, so a finished arm can be re-graded without
paying for it again.

    python tools/harness-bench/rescore.py arm-25 arm-500
    python tools/harness-bench/rescore.py heavy-arm-25 heavy-arm-500

**Stdlib only, deliberately.** Importing `recursion_ceiling_ab` to reach its task
definitions would drag in `tools/desktop-journeys/_lib`, which needs the journey
harness and dies on a system `python3` — and the documented invocation above is a
bare `python`. The price of that choice is that this file can re-SCORE an arm but
cannot re-EXPRESS its expectation; see `outcome_for`.

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
peak_result_tokens      a result that was OFFLOADED before assembly. An
                        offloaded result is labelled `…context:offload_stub`,
                        NOT `…conversation:tool_result`, so it is not in this
                        max at all — `offloaded_results` is the companion that
                        counts exactly those. This column is `None`, never 0,
                        when no inline tool result was observed: a run with no
                        tool result and a run whose results were all tiny are
                        different facts and must not print the same number.
offloaded_results       the PAYLOAD. The stub it counts is the bounded
                        preview plus a `/large_tool_results/<sha256>`
                        reference — the ledger records that the cap fired and
                        cannot say how big the thing it caught was.
                        `largest_offloaded_blob` is where the size lives.
peak_stub_tokens        the same blind spot, one level down: it is the size of
                        the STUB, which is bounded by construction, so it says
                        nothing about what was offloaded.
largest_offloaded_blob  which RUN wrote it. The object store is per-PROFILE,
                        not per-run, so this is attributable to an arm only
                        because each arm boots a fresh profile
                        (`BENCH_REUSE_PROFILE=1` breaks that). It is also blind
                        to a payload written by anything other than a tool
                        offload that happens to share the store.
memory_files            content. It reports each document's path and BYTE SIZE;
                        the path segments are `safe_key` hashes, so size is the
                        only legible signal — which is exactly what makes it
                        H6's construction check. A file the agent grew to ~64KB
                        and a file it under-grew to ~20KB are one `stat` apart,
                        and no answer text can forge that.
budget_notes            which tool exhausted, unless the segment label says.
super_steps_estimate    everything a fit is blind to. It is
                        `6 + 4 * tool_invocations` from the measured fit in
                        `ExecutionHyperparameters.recursion_limit`'s comment
                        (middleware + a subagent); a middleware change
                        invalidates it, and it cannot count steps spent in a
                        round that produced no invocation row.
outcome_ok              a right answer reached by the WRONG WORK, and a wrong
                        answer that happens to contain the sentinel string. It
                        reads the final assistant text and nothing else.
                        THREE-VALUED on purpose. `True`/`False` mean the answer
                        was checked and was right/wrong; `None` means NOT
                        MEASURED — either the arm recorded no expected answer
                        (every row in `arm-25`/`arm-500`, which predate the
                        column) or the run is absent from the event log. A
                        `False` on a FAILED run means "produced no answer",
                        which is honest but is not a statement about the model:
                        read `status` and `terminal_code` beside it.
outcome_reason          nothing — it is the text of why `outcome_ok` is `None`,
                        present only then.
answer_head             nothing it claims. Transcription only, first
                        `ANSWER_HEAD_CHARS` characters; it is NEVER graded.
                        Grading always runs against the full text from the
                        store, so the cap cannot change a verdict.
answer_tail             same, from the other end. It exists because a sentinel
                        line is by design the LAST line, so `answer_head` alone
                        cannot show the text a `-` verdict turned on.
======================  ====================================================
"""

from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from collections.abc import Iterator
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

#: ``MessageContextOrigins.OFFLOAD_STUB`` — what a tool result becomes once
#: ``ToolResultAdmissionAdapter`` decides it is too big to admit inline.
#:
#: This is a DIFFERENT label from ``TOOL_RESULT_LABEL``, and that is the whole
#: reason this constant exists. `occupancy_shape` used to filter on the
#: tool_result label alone, so the one event that proves the cap fired was
#: invisible to it: a run whose big read was offloaded reported the peak of its
#: remaining SMALL results and nothing else — a real number, correctly
#: computed, answering a question nobody asked. H6 exists to cross this cap, so
#: a scorer that cannot see the crossing makes the task pointless.
OFFLOAD_STUB_LABEL = "agent_runtime.context:offload_stub"

#: The measured super-step fit for THIS graph (RuntimeControlMiddleware + a
#: subagent), from the comment on `ExecutionHyperparameters.recursion_limit`.
SUPER_STEP_BASE = 6
SUPER_STEP_PER_ROUND = 4

#: The event types carrying what the user actually reads, and the payload keys
#: the text can arrive under. Mirrors `_workspace_lib.assistant_text` exactly,
#: INCLUDING its habit of appending every matching key rather than the first —
#: a duplicated string cannot change a `search` verdict, but a live/offline
#: divergence in what counts as "the answer" absolutely can.
FINAL_ANSWER_EVENTS = frozenset({"final_response", "message_completed"})
ANSWER_KEYS = ("text", "content", "message", "final_response")

#: How much of an answer is transcribed into a report. Evidence only; never
#: graded. Matched by `recursion_ceiling_ab.ANSWER_HEAD_CHARS`.
ANSWER_HEAD_CHARS = 200
ANSWER_TAIL_CHARS = 160

#: Why an `outcome_ok` is `None`. Both are "not measured", and neither is
#: `False`: a scorer that collapsed either into a wrong answer would be
#: manufacturing the negative result that stops investigation.
NO_EXPECTATION = "this arm declared no expected answer"
NO_EVENT_LOG = "run absent from the event log"
NOT_REGRADED = "recorded live; this arm did not record the pattern it used"


def session_dir(arm: str, report: dict | None = None) -> Path | None:
    """The userData dir this arm actually wrote, named rather than guessed.

    A report written by `heavy_tasks_ab.py` records its own
    ``user_data_subdir``, and that is used verbatim. Older reports
    (`arm-25` / `arm-500`) do not, so they fall back to globbing for the
    NEWEST directory matching the arm's DriverSession name — which is a real
    hazard worth naming: re-run an arm and the fallback happily scores the
    newer session against the older report, producing a table in which every
    column is internally consistent and wrong.

    Three `journey-bench-recursion-500-*` directories exist on the box those two
    arms were measured on, so that hazard is live, not theoretical. It was
    survivable while every column was a token count. It stops being survivable
    the moment a row carries the TEXT a model produced, because the table would
    then quote another run's answer as this row's evidence. So a report that
    carries no ``user_data_subdir`` but DOES carry the ``session_dir`` a
    previous rescore resolved is pinned to that path. Pinning is circular if the
    first resolution was wrong — that is the trade, taken knowingly: a stable
    wrong answer can be found and corrected, a drifting one cannot.
    """

    if report is not None:
        subdir = report.get("user_data_subdir")
        if isinstance(subdir, str) and subdir:
            candidate = APP_SUPPORT / subdir
            return candidate if candidate.is_dir() else None
        recorded = report.get("session_dir")
        if isinstance(recorded, str) and recorded and Path(recorded).is_dir():
            return Path(recorded)

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


def _event_records(directory: Path) -> Iterator[dict]:
    """Every event record in this session's logs, one dict at a time.

    Both readers below need the same walk, and the walk has two shapes to
    tolerate: the store writes the record at the top level, and some callers
    wrap it under a ``record`` key. Sorted rather than in glob order so two runs
    of the scorer over the same store cannot disagree.
    """

    for path in sorted(
        directory.glob("agent-data/v1/workspaces/*/sessions/*/events.jsonl")
    ):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            record = row.get("record", row) if isinstance(row, dict) else None
            if isinstance(record, dict):
                yield record


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
    for record in _event_records(directory):
        if record.get("event_type") not in {"run_failed", "run_completed"}:
            continue
        payload = record.get("payload") or {}
        run_id = record.get("run_id")
        if isinstance(run_id, str) and isinstance(payload, dict):
            codes[run_id] = str(payload.get("code") or record.get("event_type"))
    return codes


def final_answers(directory: Path) -> tuple[dict[str, str], set[str]]:
    """Map run_id → the final assistant text, AND the run_ids the log contained.

    The second return value is not a convenience, it is the safety property. If
    the glob, the session directory or the payload key were ever wrong, the map
    would come back empty and a caller that graded straight off it would mark
    every row WRONG — a fabricated negative, which is worse than a fabricated
    zero because a negative result stops investigation rather than prompting it
    (FINDINGS.md method note 2). So the caller is required to ask "was this run
    in the log at all?" first: a run_id absent from ``seen`` is NOT MEASURED,
    never wrong. An empty ``seen`` means the reader found nothing and should be
    read as a broken instrument, not as four wrong answers.

    A run that IS in ``seen`` with no entry in the map genuinely produced no
    final answer — `arm-25`'s `t3-todo-driven`, stopped by the step ceiling, is
    exactly that — and grading it against an expectation correctly yields False.

    Blind spot: this mirrors `_workspace_lib.assistant_text`, which does not
    filter by graph scope. Every `final_response` observed so far is root-scoped
    with ``task_id``/``subagent_id`` null, but a subagent-scoped final answer
    would be concatenated into the root run's text rather than ignored. Left
    unfiltered ON PURPOSE — the live and offline readers must not diverge — and
    stated here rather than silently fixed on one side.
    """

    chunks: dict[str, list[str]] = {}
    seen: set[str] = set()
    for record in _event_records(directory):
        run_id = record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        seen.add(run_id)
        if record.get("event_type") not in FINAL_ANSWER_EVENTS:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        for key in ANSWER_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                chunks.setdefault(run_id, []).append(value)
    return {run: "\n".join(parts) for run, parts in chunks.items()}, seen


def outcome_for(
    row: dict, answers: dict[str, str], seen: set[str]
) -> tuple[bool | None, str | None]:
    """Grade ONE row against the expectation that row itself recorded.

    Against the row's own pattern, never against the current task file: an arm
    is a measurement of the prompts it actually ran, and grading an old arm with
    a newer expectation would silently restate history. The cost is that a
    mistaken pattern cannot be re-expressed offline — fixing it needs a new paid
    arm. That is the deliberate price of keeping this file stdlib-only; it is a
    blind spot, not an oversight.
    """

    expected = row.get("expected")
    if not isinstance(expected, str) or not expected:
        return None, NO_EXPECTATION
    run_id = row.get("run_id")
    if not isinstance(run_id, str) or run_id not in seen:
        return None, NO_EVENT_LOG
    return bool(re.compile(expected).search(answers.get(run_id, ""))), None


def ok_cell(verdict: bool | None) -> str:
    """``Y`` right · ``-`` wrong · ``?`` NOT MEASURED. Three glyphs, three states.

    The third is the one that matters. Rendering an unmeasured row as `-` reads
    as a failing answer, and a reader who trusts the table would then be looking
    at a regression that never happened.
    """

    if verdict is True:
        return "Y"
    return "-" if verdict is False else "?"


def last_line(text: object) -> str:
    """The last non-empty line — where a sentinel answer is supposed to be."""

    if not isinstance(text, str):
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:ANSWER_TAIL_CHARS] if lines else ""


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
def _num(value: object) -> str:
    """Render a counter that may be unobservable, distinctly from a real zero."""

    return "?" if value is None else str(value)


def occupancy_shape(rows: list[dict]) -> dict:
    """Per-run model-call count and the largest tool result that entered context.

    ``model_calls`` is the honest round count for the ceiling question: the
    occupancy row is written when the prompt is ASSEMBLED, so a model call that
    went on to emit a tool call the run never finished is still counted here.
    That is the exact case ``tool_rounds`` cannot see.

    **The peaks start at ``None``, not 0.** A run that admitted no inline tool
    result at all used to report ``peak_result_tokens: 0``, which is
    indistinguishable from a run whose largest result was genuinely tiny — the
    third appearance in this program of the defect its method notes open with,
    and visible right now in ``runs/arm-500.json``, where every task carries a
    peak of 0. A number that cannot be observed is returned as ``None`` and
    printed as ``-``.
    """

    peak_tokens: int | None = None
    peak_bytes: int | None = None
    peak_stub: int | None = None
    # `None`, not 0, when there is no occupancy row to count: an empty ledger
    # means the cap was NOT MEASURED, and "the cap never fired" is a different
    # claim. This column is load-bearing for H6, so a false 0 here reads as a
    # measured negative — the same mistake three other counters in this program
    # already made (FINDINGS.md §7.3).
    offloaded: int | None = None if not rows else 0
    budget_notes: int | None = None if not rows else 0
    for row in rows:
        for segment in (row.get("segments_json") or {}).get("segments", []):
            if not isinstance(segment, dict):
                continue
            label = str(segment.get("label") or "")
            tokens = int(segment.get("estimated_tokens") or 0)
            if label == TOOL_RESULT_LABEL:
                peak_tokens = max(peak_tokens or 0, tokens)
                peak_bytes = max(peak_bytes or 0, int(segment.get("byte_count") or 0))
            elif label == OFFLOAD_STUB_LABEL:
                # The cap FIRED here. Counted separately from the inline peak
                # because it answers a different question: not "how big did a
                # result get" but "how often was one too big to admit".
                offloaded = (offloaded or 0) + 1
                peak_stub = max(peak_stub or 0, tokens)
            elif label == BUDGET_NOTE_LABEL:
                budget_notes = (budget_notes or 0) + 1
    return {
        "model_calls": len(rows),
        "peak_result_tokens": peak_tokens,
        "peak_result_bytes": peak_bytes,
        # The load-bearing column for H6's claim. `outcome_ok` cannot carry it:
        # H6's agent authors its own fixture, so it can answer the question from
        # memory without the oversized read ever reaching it.
        "offloaded_results": offloaded,
        "peak_stub_tokens": peak_stub,
        # A run stopped by the per-tool-name budget and a run stopped by the
        # step ceiling both end early with work undone. Only this column and
        # `terminal_code` together tell them apart.
        "budget_notes": budget_notes,
    }


def memory_files(directory: Path) -> dict[str, int]:
    """What the agent left in ``/memories/``, path → BYTE SIZE.

    The heavy tasks all write here, so this is how a completed run's claim to
    have done the work is checked WITHOUT re-reading the transcript and without
    another paid run.

    Sizes, not just names, because of H6. Both path segments are ``safe_key``
    hashes, so the names carry no information at all — but the size does, and it
    is the ONE thing about H6 the model cannot forge. H6 asks the agent to grow
    one document to roughly 64KB through four chained expansions; a document
    that came out at ~20KB means an expansion was mis-transcribed and every
    ``edit_file`` call still reported success. No answer text distinguishes
    those two runs. One ``stat`` does.
    """

    root = directory / "agent-data" / "v1" / "memory"
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def largest_offloaded_blob(directory: Path) -> int | None:
    """Bytes of the biggest object in the content-addressed store, or ``None``.

    ``FileStoreLayout.objects_dir`` is ``<root>/objects/sha256/<aa>/<sha>``, and
    it is where an offloaded tool result's payload actually lands. The occupancy
    ledger records only the STUB that replaced it, so without this the harness
    can say the cap fired and cannot say what size tripped it.

    ``None`` rather than 0 when the store is absent or empty, for the same
    reason as the peaks above: "no object store on disk" and "an object store
    holding a zero-byte file" are different facts.

    Blind spot, stated because it decides how the number may be read: this is
    per-PROFILE, not per-run. Attributing it to an arm is only valid because
    each arm boots a fresh profile — ``BENCH_REUSE_PROFILE=1`` breaks that, and
    so would any second run against the same userData dir.
    """

    root = directory / "agent-data" / "v1" / "objects" / "sha256"
    if not root.is_dir():
        return None
    sizes = [path.stat().st_size for path in root.rglob("*") if path.is_file()]
    return max(sizes) if sizes else None


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
    answers, seen_runs = final_answers(directory)
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
        task["input_tokens"] = u.get("input_tokens")
        task["output_tokens"] = u.get("output_tokens")
        task["cached_input_tokens"] = u.get("cached_input_tokens")
        task["total_tokens"] = u.get("total_tokens")
        task["duration_ms"] = u.get("duration_ms", 0)
        task.update(tool_shape(grouped))
        task.update(occupancy_shape(occupancy.get(run_id, [])))
        task["super_steps_estimate"] = SUPER_STEP_BASE + SUPER_STEP_PER_ROUND * len(
            grouped
        )
        task["terminal_code"] = codes.get(run_id, "?")

        # ── correctness, re-derived from the store ──────────────────────────
        # Transcribed only when the log actually contained this run. Writing an
        # empty string otherwise would overwrite the live evidence with the
        # reader's own failure to find it.
        if run_id in seen_runs:
            text = answers.get(run_id, "")
            task["answer_head"] = text.strip()[:ANSWER_HEAD_CHARS]
            task["answer_tail"] = last_line(text)
        verdict, reason = outcome_for(task, answers, seen_runs)
        if task.get("expected"):
            # This scorer is the AUTHORITY: the live value in the row was graded
            # against a mid-run read of the event stream, this one against the
            # settled store. When they disagree the file would otherwise show
            # only the later number, so say so out loud and make it a finding.
            previous = task.get("outcome_ok")
            if previous is not None and previous is not verdict:
                print(
                    f"  {arm} {task.get('task')}: outcome_ok {previous} -> "
                    f"{verdict} (store overrides the live verdict)"
                )
            task["outcome_ok"] = verdict
            if reason is None:
                task.pop("outcome_reason", None)
            else:
                task["outcome_reason"] = reason
        else:
            # No recorded pattern: this row cannot be re-graded here, and a live
            # verdict (if any) is left EXACTLY as the arm wrote it. Never
            # downgraded to False — see `final_answers`.
            task["outcome_reason"] = (
                NOT_REGRADED if task.get("outcome_ok") is not None else reason
            )
    report["session_dir"] = str(directory)
    report["memory_files"] = memory_files(directory)
    report["largest_offloaded_blob"] = largest_offloaded_blob(directory)
    # Trailing newline: these reports are committed as evidence, and without it
    # `end-of-file-fixer` and `prettier` rewrite the file on every commit — so
    # each rescore lands a diff that is pure whitespace churn over real numbers.
    report_path.write_text(json.dumps(report, indent=2) + "\n")
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
        f"{'out':>7}{'peakres':>8}{'offl':>6}  failures"
    )
    peak_rounds = peak_steps = 0
    #: ``None`` until an inline tool result is actually seen. Starting at 0 would
    #: print a peak of 0 for a set in which every result was OFFLOADED — the cap
    #: firing on every read, reported as no result ever arriving.
    peak_result: int | None = None
    offloaded_total = 0
    for report in scored:
        arm = report["recursion_limit"]
        for task in scored_tasks(report):
            peak_rounds = max(peak_rounds, task["tool_rounds"])
            peak_steps = max(peak_steps, task["super_steps_estimate"])
            task_peak = task["peak_result_tokens"]
            if task_peak is not None:
                peak_result = max(peak_result or 0, task_peak)
            offloaded_total += task.get("offloaded_results") or 0  # None -> 0 sum
            print(
                f"{arm:<8}{task['task']:<18}{str(task['status']):<11}"
                f"{ok_cell(task.get('outcome_ok')):<4}"
                f"{task['tool_invocations']:>6}{task['orphaned_rounds']:>5}"
                f"{task['peak_parallel']:>4}{task['delegated_rounds']:>4}"
                f"{task['model_calls']:>5}{task['super_steps_estimate']:>7}"
                # `?`, never `0`: run_usage.jsonl carried no row for this run,
                # which is "not measured" and not "cost nothing".
                f"{_num(task['input_tokens']):>9}"
                f"{_num(task['cached_input_tokens']):>9}"
                f"{_num(task['output_tokens']):>7}"
                # `-`, never `0`: this column has no observation to report.
                f"{('-' if task_peak is None else task_peak):>8}"
                f"{_num(task.get('offloaded_results')):>6}  "
                f"{','.join(task['tool_failures']) or '-'}"
            )

    print(
        "\n  ok: Y=matched the answer this arm declared · -=did not match · "
        "?=NOT MEASURED\n      (this arm declared no expected answer, or the run "
        "is absent from the\n      event log). ? is not a failure and must never "
        "be counted as one."
    )
    for report in scored:
        rows = scored_tasks(report)
        done = sum(1 for t in rows if t["status"] == "completed")
        # Only tasks that DECLARED an expected answer can be right or wrong.
        # Printing "0/4 correct" for a set that never asked would be a fabricated
        # negative — the same class of mistake as a broken instrument reporting
        # zero tokens, and a worse one, because a negative stops investigation.
        judged = [t for t in rows if t.get("outcome_ok") is not None]
        unjudged = [t for t in rows if t.get("outcome_ok") is None]
        correct = (
            f"{sum(1 for t in judged if t['outcome_ok'])}/{len(judged)} correct"
            if judged
            else "correctness not checked by this set"
        )
        if unjudged:
            reasons = sorted({str(t.get("outcome_reason") or "?") for t in unjudged})
            correct += f" [{len(unjudged)} unknown: {'; '.join(reasons)}]"
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
    print(
        "  peak tool result entering context:       "
        + (
            "not observed (no inline tool result in any scored run)"
            if peak_result is None
            else f"{peak_result} tokens"
        )
    )
    print(
        f"  results OFFLOADED before the model saw them: {offloaded_total}"
        + (
            "  → the pre-model cap never fired; nothing here measures it"
            if offloaded_total == 0
            else "  → the pre-model cap FIRED. The peak above is the largest"
            " result that got THROUGH it, not the largest produced."
        )
    )
    for report in scored:
        blob = report.get("largest_offloaded_blob")
        print(
            f"    limit={report['recursion_limit']}: largest object in the store "
            + ("none written" if blob is None else f"{blob} bytes")
        )

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

    wrong = [
        (
            r["recursion_limit"],
            t["task"],
            str(t.get("claim") or "?"),
            str(t.get("status")),
            last_line(t.get("answer_tail") or t.get("answer_head")),
        )
        for r in scored
        for t in scored_tasks(r)
        if t.get("outcome_ok") is False
    ]
    if wrong:
        print("\n  runs that answered WRONG — the failure a completion count")
        print("  cannot see. A `completed` row here spent full price and")
        print("  returned something other than what was asked for:")
        for limit, task, claim, status, tail in wrong:
            print(f"    limit={limit}  {task}  [{status}]  {claim}")
            print(f"      last line: {tail or '(no answer text)'}")

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
