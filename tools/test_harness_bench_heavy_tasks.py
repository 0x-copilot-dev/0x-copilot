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
   A fourth, added with the rebased ``h6-bigread``: a fixture the AGENT builds
   has to be sized so the thing it is supposed to cross is actually crossed. H6
   makes two reads straddle the pre-model tool-result cap — one admitted inline,
   the next offloaded — and if either lands on the wrong side the task runs, the
   money is spent, and the cap is measured from one direction only.
2. **The scorer sees what it says it sees.** The store writes one row per state
   TRANSITION, so counting rows is not counting calls; a call closed by the
   blanket reconciler is terminal with an empty ``result_summary``; an OFFLOADED
   result carries a different label and used to be dropped entirely; and
   ``run_id``-less rows must never be filled with zeros — nor must an
   unobservable peak, which is now ``None``. Each is checked against a synthetic
   store here rather than against a real one, so the check does not depend on a
   session directory existing.
3. **The literals held here still match the service.** ``TOOL_CALL_BUDGET``,
   ``INLINE_TOKEN_BUDGET``, ``CHARS_PER_TOKEN_ESTIMATE`` and the super-step fit
   are copied into the harness because a tool must not import a service's
   ``src``. Copies drift; this asserts they have not. The one constant that
   CANNOT be pinned here is ``DEEPAGENTS_READ_EVICT_TOKENS``: it lives in a
   site-package, so a deepagents bump could move it under H6's second read and
   quietly make the library's truncation the thing H6 measures.

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


def test_h6_is_grant_free_and_lives_on_the_memory_route(heavy):
    """H6 must need NOTHING, or it goes back to never being measured.

    Its predecessor read a host CSV behind a folder grant that can only be minted
    through a native picker. On a host that denies Accessibility that lane cannot
    be driven at all, so every arm ever run recorded `skipped` for H6 and the
    tool-result cap stayed unmeasured while a `peak 68 of 8,000` sat in the
    report looking like a measurement.

    The three structural traps are checked together here because H6 is the task
    that would trip all three: it must address `/memories/`, it must not ask for
    grep or glob there (both answer EMPTY on that backend), and it must keep
    every tool name under the per-name budget.
    """

    task = next(t for t in heavy.TASKS if t.task_id == "h6-bigread")
    assert task.needs is heavy.Needs.NOTHING, (
        "h6 declares a prerequisite again; it will record `skipped` and the cap "
        "will go back to being unmeasured"
    )
    assert heavy.WIDE_PATH.split("/")[1] in heavy.VIRTUAL_ROOTS
    assert heavy.WIDE_PATH in task.prompt

    # Every absolute path the prompt names has to be a virtual root. A
    # host-shaped one is refused ungranted, which is a refusal wearing the cap's
    # name.
    for path in re.findall(
        r"(?<![\w/]) /[A-Za-z0-9_.-]+", task.prompt.replace(" /", "  /")
    ):
        head = path.strip().lstrip("/").split("/")[0]
        assert head in heavy.VIRTUAL_ROOTS, f"h6 names host-absolute {path.strip()}"

    assert "do not use grep or glob" in task.prompt.lower(), (
        "h6 reads a large file; without this line a model may reach for grep, "
        "which returns an EMPTY RESULT on FileMemoryBackend rather than an error"
    )
    for name, count in task.planned_calls.items():
        assert count < heavy.TOOL_CALL_BUDGET, (
            f"h6 plans {name} x{count} against a budget of "
            f"{heavy.TOOL_CALL_BUDGET}; it would measure the budget, not the cap"
        )


def test_the_wide_fixture_straddles_the_inline_budget(heavy):
    """One read under the cap, the next over it — or H6 measures one side only.

    Asserted as HEADROOM rather than as the measured numbers. What the admission
    adapter actually weighs is deepagents' line-numbered RENDER of the file, not
    the raw file `wide_content` returns, and the render only ever adds characters
    (a gutter per line, a continuation row per 5,000 chars). Pinning the exact
    measured token count would turn this gate into a transcription of one run
    that reds on any rendering change; pinning generous margins keeps it a
    statement about the design.
    """

    under = heavy.wide_tokens(heavy.WIDE_READ_AFTER)
    over = heavy.wide_tokens(heavy.WIDE_EXPANSIONS)

    assert under <= 0.6 * heavy.INLINE_TOKEN_BUDGET, (
        f"the first read is {under} est tokens against a cap of "
        f"{heavy.INLINE_TOKEN_BUDGET}; too close to offload once the "
        "line-number render adds to it, and H6 would then measure two "
        "offloads rather than a straddle"
    )
    assert over >= 1.5 * heavy.INLINE_TOKEN_BUDGET, (
        f"the second read is only {over} est tokens; it must clear the cap by "
        "a margin no rendering difference can close"
    )
    # And the cap under test must be OURS. Past deepagents' own read truncation
    # the model gets a clipped result for a reason that has nothing to do with
    # `ToolResultAdmissionAdapter`, and H6 would silently measure the library.
    assert over < heavy.DEEPAGENTS_READ_EVICT_TOKENS, (
        f"the second read is {over} est tokens, at or past deepagents' own "
        f"{heavy.DEEPAGENTS_READ_EVICT_TOKENS}-token read truncation"
    )
    # The declared answer must be derivable from the rows, not typed.
    assert heavy.WIDE_HOURS == sum(hours for _, _, hours in heavy.WIDE_ROWS)
    assert heavy.wide_seed().count(heavy.wide_marker(1)) == len(heavy.WIDE_ROWS)


def test_the_marker_chain_refuses_an_out_of_order_edit(heavy):
    """The one property that stops a batched edit from silently under-growing.

    Haiku batched twelve tool calls into a single assistant turn in the §8 arms,
    and `FileMemoryBackend.edit` is an unlocked read-modify-write. Four `X -> XX`
    calls issued together would all read the same base, all write 2x, and all
    report SUCCESS — leaving a file one-eighth the intended size under four green
    tool cards, which is precisely the empty-success shape this suite exists to
    catch.

    Chaining the markers makes step k+1's `old_string` absent until step k has
    committed, so the backend answers `"old_string was not found in the memory
    file."` — loud, and retryable within the six spare `edit_file` calls the
    budget leaves. This asserts the property the defence rests on.
    """

    seed = heavy.wide_seed()
    for step in range(2, heavy.WIDE_EXPANSIONS + 2):
        assert heavy.wide_marker(step) not in seed, (
            f"marker {step} is already present in the seed, so edit {step} could "
            "run out of order and the chain no longer forces serialisation"
        )
    # Each step must consume the previous marker entirely and leave only the next.
    for step in range(1, heavy.WIDE_EXPANSIONS + 1):
        after = heavy.wide_content(step)
        assert heavy.wide_marker(step) not in after
        assert (
            after.count(heavy.wide_marker(step + 1))
            == len(heavy.WIDE_ROWS) * heavy.WIDE_FACTOR**step
        )
    # Growth is a real multiplication, not a rounding artefact of the estimate.
    assert heavy.wide_tokens(heavy.WIDE_EXPANSIONS) > heavy.wide_tokens(
        heavy.WIDE_READ_AFTER
    ) * (heavy.WIDE_FACTOR - 1)


def test_a_task_needing_a_prerequisite_never_silently_becomes_a_pass(heavy):
    """An absent connector must record under the task's OWN id.

    A set of seven tasks that prints six rows has quietly changed what it
    measured. `Arm.run_task` returns a row either way, and the row must not be
    scoreable as a completed run.

    Only `CONNECTED_MCP` remains gated: H6 was rebased off the folder grant and
    `Needs.HOST_GRANT` is gone with it. That makes this the LAST gated lane in
    the set, which is a reason to guard it harder, not a reason to relax — the
    fail-closed rule below is the one that stops H7 from measuring MCP
    namespacing on a profile with no connectors and reporting a number.
    """

    arm = heavy.Arm.__new__(heavy.Arm)
    arm.available = set()
    arm.blocked = {}
    mcp_task = next(t for t in heavy.TASKS if t.needs is heavy.Needs.CONNECTED_MCP)
    # No blocked entry and nothing confirmed: the MCP task still cannot run and
    # must SAY so. Deriving availability from the absence of a blocked entry
    # would promote it to runnable whenever setup did not reach its check.
    reason = arm.prerequisite(mcp_task)
    assert reason is not None and "never confirmed present" in reason

    # A recorded reason is reported verbatim rather than replaced by the generic.
    arm.blocked[heavy.Needs.CONNECTED_MCP] = "this profile has 0"
    assert arm.prerequisite(mcp_task) == "this profile has 0"

    plain = next(t for t in heavy.TASKS if t.needs is heavy.Needs.NOTHING)
    assert arm.prerequisite(plain) is None

    # And confirming it is what unblocks it — a gated task must still be
    # reachable, or the guard has merely disabled the measurement.
    arm.available.add(heavy.Needs.CONNECTED_MCP)
    assert arm.prerequisite(mcp_task) is None


def test_no_prompt_is_run_through_str_format(heavy):
    """The substitution step is GONE, and no prompt may quietly need it back.

    `h6-bigread` used to carry `{ledger}` / `{emea}` placeholders substituted
    mid-arm, after the boot, after the key, after money had been spent. H6 now
    builds its own fixture, so no task needs a run-time value and `Arm.run_task`
    sends `task.prompt` verbatim.

    That removal is what this asserts: a prompt reintroducing a brace pair would
    be sent literally to the model rather than substituted — a silent wrong
    prompt in a paid run, not a crash.
    """

    assert not hasattr(heavy.HeavyTask, "fixture_keys")
    for task in heavy.TASKS:
        assert "{" not in task.prompt and "}" not in task.prompt, (
            f"{task.task_id} carries braces, but nothing substitutes them any "
            "more — it would reach the model with the braces intact"
        )


def test_pinning_an_unknown_task_id_fails_loudly(heavy, monkeypatch):
    """A pinned id that matches nothing must raise, not run zero tasks.

    Same contract as `JOURNEY_PHASES`: a caller pinning a renamed task and
    getting a clean empty run has proven nothing while reporting success.
    """

    monkeypatch.setenv(heavy.TASK_SELECTOR_ENV, "h1-corpus")
    assert [t.task_id for t in heavy.selected_tasks()] == ["h1-corpus"]
    monkeypatch.setenv(heavy.TASK_SELECTOR_ENV, "h1-corpus, h3-transform")
    assert [t.task_id for t in heavy.selected_tasks()] == ["h1-corpus", "h3-transform"]
    monkeypatch.setenv(heavy.TASK_SELECTOR_ENV, "h9-does-not-exist")
    with pytest.raises(SystemExit, match="h9-does-not-exist"):
        heavy.selected_tasks()
    monkeypatch.delenv(heavy.TASK_SELECTOR_ENV)
    assert heavy.selected_tasks() == heavy.TASKS


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
    assert shape["offloaded_results"] == 0, "nothing here crossed the cap"


def test_an_offloaded_result_is_reported_not_read_as_a_small_one(rescore):
    """The cap firing carries a DIFFERENT label, and used to be invisible here.

    `ToolResultAdmissionAdapter` replaces an oversized result with a bounded stub
    labelled `agent_runtime.context:offload_stub`, not
    `agent_runtime.conversation:tool_result`. `occupancy_shape` filtered on the
    tool_result label alone, so the single event proving the cap fired was
    dropped and the run reported the peak of its remaining small results — a
    real number, correctly computed, answering a question nobody asked. h6-bigread
    exists to cross this cap, so a scorer blind to the crossing makes it
    pointless.
    """

    rows = [
        {
            "segments_json": {
                "segments": [
                    {
                        "label": rescore.TOOL_RESULT_LABEL,
                        "estimated_tokens": 4029,
                        "byte_count": 16113,
                    }
                ]
            }
        },
        {
            "segments_json": {
                "segments": [
                    {
                        "label": rescore.OFFLOAD_STUB_LABEL,
                        "estimated_tokens": 558,
                        "byte_count": 2233,
                    }
                ]
            }
        },
    ]
    shape = rescore.occupancy_shape(rows)
    assert shape["offloaded_results"] == 1, "the cap fired and must be counted"
    assert shape["peak_stub_tokens"] == 558
    # The stub must NOT enter the inline peak: it would report 558 tokens for a
    # result that was really ~15,949, which understates the very thing it is
    # supposed to bound.
    assert shape["peak_result_tokens"] == 4029


def test_an_unobserved_result_peak_is_none_and_never_zero(rescore):
    """The third instance of "0 means not measured" in this program.

    A run with no inline tool result segment used to report
    `peak_result_tokens: 0`, indistinguishable from a run whose largest result
    was genuinely tiny — and `runs/arm-500.json` carries exactly that zero on
    every task today. Worse after this change: a run whose every result was
    OFFLOADED would report a peak of 0, i.e. the cap firing on every single read
    rendered as no result ever arriving.
    """

    empty = rescore.occupancy_shape([{"segments_json": {"segments": []}}])
    assert empty["peak_result_tokens"] is None
    assert empty["peak_result_bytes"] is None
    assert empty["peak_stub_tokens"] is None
    assert empty["model_calls"] == 1, "the model call itself WAS observed"

    only_offloaded = rescore.occupancy_shape(
        [
            {
                "segments_json": {
                    "segments": [
                        {"label": rescore.OFFLOAD_STUB_LABEL, "estimated_tokens": 558}
                    ]
                }
            }
        ]
    )
    assert only_offloaded["peak_result_tokens"] is None, (
        "every result was offloaded; reporting 0 would read as 'no results'"
    )
    assert only_offloaded["offloaded_results"] == 1


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


def test_the_inline_token_budget_literal_matches_the_shipped_adapter(heavy):
    """H6's whole design is a size, so the number it is sized against must be real.

    Read as TEXT, never imported — a tool must not import a service's `src`, and
    importing would also drag the whole runtime into a suite that is meant to be
    stdlib-only and instant.

    This exists because the number was already wrong in prose. FINDINGS.md §5 and
    §8 both quote the tool-result cap as **8,192**, which is
    `context.model_result_preview_bytes` — a different constant, in bytes, read
    only by `runtime_worker/mcp_operation_storage.py`, never on a `read_file`
    path. The cap that actually bounds a result before the model sees it is 8,000
    ESTIMATED tokens at 4 chars each, i.e. 32,000 characters. Sizing a fixture
    against 8,192 bytes instead would be off by 4x and would land H6's intended
    inline read on the wrong side of the threshold.
    """

    admission = (
        REPO_ROOT
        / "services"
        / "ai-backend"
        / "src"
        / "agent_runtime"
        / "context"
        / "tool_result_admission.py"
    ).read_text(encoding="utf-8")
    assert (
        f"DEFAULT_INLINE_TOKEN_BUDGET = {heavy.INLINE_TOKEN_BUDGET:_}" in admission
    ), (
        f"heavy_tasks_ab.INLINE_TOKEN_BUDGET is {heavy.INLINE_TOKEN_BUDGET}, but "
        "ToolResultAdmissionAdapter no longer ships that value; h6-bigread's "
        "fixture is now sized against a cap that does not exist"
    )

    budget = (
        REPO_ROOT
        / "services"
        / "ai-backend"
        / "src"
        / "agent_runtime"
        / "context"
        / "memory"
        / "token_budget.py"
    ).read_text(encoding="utf-8")
    assert f"CHARS_PER_TOKEN_ESTIMATE = {heavy.CHARS_PER_TOKEN_ESTIMATE}" in budget, (
        "the chars-per-token estimate the fixture sizing depends on has moved"
    )

    # The number the docs got wrong must not creep back in as the cap.
    assert heavy.INLINE_TOKEN_BUDGET != 8192


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
