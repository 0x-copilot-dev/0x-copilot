"""Guard the heavy benchmark task set and the scorer that reads its arms.

Everything here is OFFLINE — no app, no model, no paid run. That is the point:
each of these checks corresponds to a way a paid arm can burn money and return a
number that means something other than what it says, and every one of them was
reachable in a design that looked fine on the page.

Three classes of check:

1. **The task set measures what it claims.** A prompt naming a host-absolute
   path measures a grant refusal, not a chain. A prompt asking for grep on
   ``/memories/`` gets an EMPTY SUCCESS, because ``FileMemoryBackend.grep``
   returns no matches rather than an error. A task planning more calls of one
   tool name than ``execution.tool_call_budget`` measures the budget cutting it
   off. All three produce a run that completes, spends money, and answers a
   different question than the one asked.
2. **The scorer sees what it says it sees.** The store writes one row per state
   TRANSITION, so counting rows is not counting calls; a call closed by the
   blanket reconciler is terminal with an empty ``result_summary``; and
   ``run_id``-less rows must never be filled with zeros. Each is checked against
   a synthetic store here rather than against a real one, so the check does not
   depend on a session directory existing.
3. **The literals held here still match the service.** ``TOOL_CALL_BUDGET`` and
   the super-step fit are copied into the harness because a tool must not import
   a service's ``src``. Copies drift; this asserts they have not.

Run with the repo-gates set:

    PYTHONPATH=tools python -m pytest -q tools/test_harness_bench_heavy_tasks.py
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
HYPERPARAMETERS = (
    REPO_ROOT
    / "services"
    / "ai-backend"
    / "src"
    / "agent_runtime"
    / "hyperparameters"
    / "hyperparameters.json"
)


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
def heavy():
    # `heavy_tasks_ab` imports the desktop-journey harness, which needs 3.11+
    # for StrEnum and nothing else at import time — no app, no driver.
    return _load("heavy_tasks_ab")


# ── 1. the task set measures what it claims ─────────────────────────────────
def test_no_grantless_prompt_names_a_host_absolute_path(heavy):
    """A `/foo` path is claimed by the workspace backend and REFUSED ungranted.

    `HostPathClassifier` treats every POSIX-absolute path whose first segment is
    not a virtual root as host-shaped. A grant-free task naming one does not
    measure a long chain; it measures a refusal, in a paid run, under a task id
    that claims otherwise.
    """

    bad: list[str] = []
    for task in heavy.TASKS:
        if task.needs is not heavy.Needs.NOTHING:
            continue
        for path in re.findall(
            r"(?<![\w/]) /[A-Za-z0-9_.-]+", task.prompt.replace(" /", "  /")
        ):
            head = path.strip().lstrip("/").split("/")[0]
            if head and head not in heavy.VIRTUAL_ROOTS:
                bad.append(f"{task.task_id}: {path.strip()}")
    assert not bad, (
        "grant-free tasks name host-absolute paths, which are refused without a "
        f"folder grant: {bad}"
    )


def test_no_prompt_asks_for_grep_or_glob_on_the_memory_route(heavy):
    """`FileMemoryBackend.grep`/`glob` return EMPTY RESULTS, not errors.

    An empty success over a populated route is the exact shape of the
    `ls ~/Downloads` defect: a green tool card over nothing. A task that asks
    for it records a wrong answer as a model failure.
    """

    offenders = [
        task.task_id
        for task in heavy.TASKS
        if "/memories/" in task.prompt
        and re.search(r"\b(grep|glob)\b", task.prompt)
        and "do not use grep" not in task.prompt.lower()
        and "do not use grep or glob" not in task.prompt.lower()
    ]
    assert not offenders, (
        f"these tasks ask for grep/glob on /memories/, which answers empty "
        f"rather than failing: {offenders}"
    )


def test_no_task_plans_more_calls_of_one_tool_name_than_the_budget(heavy):
    """Over the per-tool-name budget, the run measures the budget, not the task.

    `execution.tool_call_budget` is a cap per tool NAME per run. A plan at or
    above it ends early with work undone — which is indistinguishable from a
    step-ceiling stop unless you read `budget_notes`, and is certainly not the
    long chain the task id promises.
    """

    over = [
        (task.task_id, name, count)
        for task in heavy.TASKS
        for name, count in task.planned_calls.items()
        if count >= heavy.TOOL_CALL_BUDGET
    ]
    assert not over, (
        f"planned calls reach the per-tool-name budget of "
        f"{heavy.TOOL_CALL_BUDGET}: {over}"
    )


def test_every_task_declares_a_checkable_finish_state(heavy):
    """A task with no expected answer cannot report `outcome_ok`.

    FINDINGS.md's whole correction was that a proxy metric was never checked
    against the thing it proxied. `expect` is that check, so it has to exist for
    every task and it has to be specific enough to fail.
    """

    for task in heavy.TASKS:
        assert task.expect.pattern, f"{task.task_id} declares no expected answer"
        assert task.expect.pattern not in {".", ".*", ""}, (
            f"{task.task_id}'s expected answer matches anything"
        )
        assert task.claim, f"{task.task_id} does not say which claim it reaches"


def test_the_expected_answers_are_derived_from_the_corpus_not_typed(heavy):
    """The stated answers must follow from CORPUS, or the task grades wrongly."""

    totals: dict[str, int] = {}
    for _, owner, hours in heavy.CORPUS:
        totals[owner] = totals.get(owner, 0) + hours
    assert heavy.CORPUS_TOTAL == sum(totals.values())
    ranked = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)
    assert ranked[0][1] > ranked[1][1], (
        "the top owner ties, so h2-crossref has two correct answers"
    )
    crossref = next(t for t in heavy.TASKS if t.task_id == "h2-crossref")
    assert crossref.expect.search(f"TOTAL={heavy.CORPUS_TOTAL} TOP={ranked[0][0]}")
    delegate = next(t for t in heavy.TASKS if t.task_id == "h4-delegate")
    assert delegate.expect.search(
        f"ADA={totals['ada']} LIN={totals['lin']} OMAR={totals['omar']}"
    )


def test_the_h6_ledger_is_long_enough_to_force_paging(heavy):
    """Under `reads.default_line_limit` the file fits in one read and proves nothing."""

    assert heavy.LEDGER_ROWS > 2000
    lines = heavy.ledger_lines()
    assert len(lines) == heavy.LEDGER_ROWS + 1, "header row missing"
    expected = sum(
        int(line.split(",")[2]) for line in lines[1:] if line.split(",")[1] == "EMEA"
    )
    assert heavy.ledger_emea_total() == expected


def test_a_task_needing_a_prerequisite_never_silently_becomes_a_pass(heavy):
    """An absent grant/connector must record under the task's OWN id.

    A set of seven tasks that prints five rows has quietly changed what it
    measured. `Arm.run_task` returns a row either way, and the row must not be
    scoreable as a completed run.
    """

    arm = heavy.Arm.__new__(heavy.Arm)
    arm.fixtures = {}
    arm.available = set()
    arm.blocked = {heavy.Needs.HOST_GRANT: "no accessibility permission"}
    grant_task = next(t for t in heavy.TASKS if t.needs is heavy.Needs.HOST_GRANT)
    mcp_task = next(t for t in heavy.TASKS if t.needs is heavy.Needs.CONNECTED_MCP)
    assert arm.prerequisite(grant_task) == "no accessibility permission"
    # No blocked entry and nothing confirmed: the MCP task still cannot run and
    # must SAY so. Deriving availability from the absence of a blocked entry
    # would promote it to runnable whenever setup did not reach its check.
    assert arm.prerequisite(mcp_task) is not None
    plain = next(t for t in heavy.TASKS if t.needs is heavy.Needs.NOTHING)
    assert arm.prerequisite(plain) is None

    # And confirming it is what unblocks it — a gated task must still be
    # reachable, or the guard has merely disabled the measurement.
    arm.available.add(heavy.Needs.CONNECTED_MCP)
    assert arm.prerequisite(mcp_task) is None
    arm.available.add(heavy.Needs.HOST_GRANT)
    assert "fixture keys absent" in str(arm.prerequisite(grant_task))
    arm.fixtures = {key: "x" for key in grant_task.fixture_keys}
    assert arm.prerequisite(grant_task) is None


# ── 2. the scorer sees what it says it sees ─────────────────────────────────
def _row(**kwargs) -> dict:
    row = {
        "invocation_id": "inv",
        "run_id": "run",
        "task_id": None,
        "tool_name": "read_file",
        "connector_slug": None,
        "status": "completed",
        "result_summary": {"content": "ok"},
        "started_at": "2026-08-14T10:00:00Z",
        "completed_at": "2026-08-14T10:00:01Z",
        "safe_error_code": None,
    }
    row.update(kwargs)
    return row


def test_rows_are_transitions_so_calls_must_be_grouped(rescore):
    """Two rows for one call is ONE call. Counting rows overcounts the chain."""

    rows = [
        _row(invocation_id="a", status="running", completed_at=None),
        _row(invocation_id="a", status="completed"),
        _row(invocation_id="b", status="running", completed_at=None),
        _row(invocation_id="b", status="completed"),
    ]
    grouped = list(rescore.group_invocations(rows).values())
    assert len(grouped) == 2
    shape = rescore.tool_shape(grouped)
    assert shape["tool_invocations"] == 2
    assert shape["tool_rounds"] == 2


def test_an_orphaned_call_is_counted_not_dropped(rescore):
    """A call still `running` at the end is the case tool_rounds cannot see."""

    rows = [
        _row(invocation_id="a", status="completed"),
        _row(invocation_id="b", status="running", completed_at=None),
    ]
    shape = rescore.tool_shape(list(rescore.group_invocations(rows).values()))
    assert shape["tool_rounds"] == 1, "a still-open call must not read as done"
    assert shape["orphaned_rounds"] == 1
    assert shape["tool_invocations"] == 2


def test_a_reconciler_closed_call_is_distinguished_from_a_real_one(rescore):
    """FINDINGS.md §2: the accused row is terminal with an EMPTY result_summary.

    `orphaned_rounds` cannot catch this — the reconciler leaves no open row.
    Without this column the harness would again report a tool as having thrown
    when the graph had hit its ceiling underneath it.
    """

    rows = [
        _row(invocation_id="a", tool_name="write_todos"),
        _row(
            invocation_id="b",
            tool_name="write_todos",
            status="failed",
            result_summary={},
            safe_error_code="tool_run_failed",
        ),
    ]
    shape = rescore.tool_shape(list(rescore.group_invocations(rows).values()))
    assert shape["orphaned_rounds"] == 0, "the reconciler closed it; no orphan exists"
    assert shape["reconciled_rounds"] == ["write_todos"]


def test_parallel_execution_is_visible_and_touching_calls_are_not(rescore):
    """Overlap answers the batched-execution claim; adjacency must not."""

    overlapping = [
        _row(
            invocation_id="a",
            started_at="2026-08-14T10:00:00Z",
            completed_at="2026-08-14T10:00:05Z",
        ),
        _row(
            invocation_id="b",
            started_at="2026-08-14T10:00:02Z",
            completed_at="2026-08-14T10:00:06Z",
        ),
    ]
    assert rescore.peak_parallel([[r] for r in overlapping]) == 2
    sequential = [
        _row(
            invocation_id="a",
            started_at="2026-08-14T10:00:00Z",
            completed_at="2026-08-14T10:00:05Z",
        ),
        _row(
            invocation_id="b",
            started_at="2026-08-14T10:00:05Z",
            completed_at="2026-08-14T10:00:09Z",
        ),
    ]
    assert rescore.peak_parallel([[r] for r in sequential]) == 1


def test_delegation_and_mcp_namespacing_are_reported_from_the_ledger(rescore):
    rows = [
        _row(invocation_id="a", task_id="research-agent", tool_name="web_search"),
        _row(invocation_id="b", task_id="research-agent", tool_name="read_file"),
        _row(
            invocation_id="c", tool_name="mcp__linear__search", connector_slug="linear"
        ),
        # The collision namespacing exists to prevent: one bare name, two servers.
        _row(invocation_id="d", tool_name="search", connector_slug="linear"),
        _row(invocation_id="e", tool_name="search", connector_slug="notion"),
    ]
    shape = rescore.tool_shape(list(rescore.group_invocations(rows).values()))
    assert shape["delegated_rounds"] == 2
    assert shape["subagents"] == ["research-agent"]
    assert shape["namespaced_tools"] == 1
    assert shape["tool_name_collisions"] == ["search"]


def test_occupancy_reports_model_calls_result_peak_and_budget_notes(rescore):
    rows = [
        {
            "segments_json": {
                "segments": [
                    {
                        "label": rescore.TOOL_RESULT_LABEL,
                        "estimated_tokens": 1581,
                        "byte_count": 4722,
                    },
                    {
                        "label": "agent_runtime.conversation:user",
                        "estimated_tokens": 43,
                    },
                ]
            }
        },
        {
            "segments_json": {
                "segments": [
                    {
                        "label": rescore.TOOL_RESULT_LABEL,
                        "estimated_tokens": 210,
                        "byte_count": 800,
                    },
                    {"label": rescore.BUDGET_NOTE_LABEL, "estimated_tokens": 30},
                ]
            }
        },
    ]
    shape = rescore.occupancy_shape(rows)
    assert shape["model_calls"] == 2, "the round count that CAN see a doomed call"
    assert shape["peak_result_tokens"] == 1581
    assert shape["peak_result_bytes"] == 4722
    assert shape["budget_notes"] == 1


def test_a_task_that_never_ran_is_excluded_rather_than_zeroed(rescore):
    """Zeros for a task that never ran are the instrument-failure shape.

    "0 tokens" from a broken scorer and "0 tokens" from a cheap run are
    indistinguishable — FINDINGS.md method note 1. A skipped task must drop out
    of the aggregate, not enter it as a free success.
    """

    report = {
        "tasks": [
            {"task": "h1", "run_id": "r1", "status": "completed", "total_tokens": 10},
            {"task": "h6", "run_id": None, "status": "skipped"},
        ]
    }
    assert [t["task"] for t in rescore.scored_tasks(report)] == ["h1"]


# ── 3. the copied literals still match the service ──────────────────────────
def test_tool_call_budget_literal_matches_the_shipped_hyperparameter(heavy):
    """The harness copies this because a tool must not import a service's src."""

    document = json.loads(HYPERPARAMETERS.read_text(encoding="utf-8"))
    shipped = document["execution"]["tool_call_budget"]
    assert heavy.TOOL_CALL_BUDGET == shipped, (
        f"heavy_tasks_ab.TOOL_CALL_BUDGET is {heavy.TOOL_CALL_BUDGET} but the "
        f"service ships {shipped}; the planned-calls guard is now wrong"
    )


def test_the_recursion_limit_the_arms_move_is_the_shipped_default(heavy):
    """`BENCH_ARM=500` must be the value production actually runs.

    An arm that is not the shipped default measures a configuration nobody has,
    and the A/B stops being about the change that shipped.
    """

    document = json.loads(HYPERPARAMETERS.read_text(encoding="utf-8"))
    assert document["execution"]["recursion_limit"] == 500


def test_the_super_step_fit_is_stated_where_it_came_from(rescore):
    """The fit is 6 + 4/round for middleware + a subagent; both halves matter."""

    assert (rescore.SUPER_STEP_BASE, rescore.SUPER_STEP_PER_ROUND) == (6, 4)
    contracts = (
        REPO_ROOT
        / "services"
        / "ai-backend"
        / "src"
        / "agent_runtime"
        / "hyperparameters"
        / "contracts.py"
    ).read_text(encoding="utf-8")
    assert "6 + 4 * tool_rounds" in contracts, (
        "the measured super-step fit this scorer copies is no longer stated in "
        "ExecutionHyperparameters.recursion_limit's comment"
    )
