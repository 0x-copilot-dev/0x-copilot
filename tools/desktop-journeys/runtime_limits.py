#!/usr/bin/env python3
"""runtime-limits — the budgets that stop a run, and whether the app can see them.

The harness program added five run-governing mechanisms to `ai-backend`: a
`recursion_limit` on the graph with a typed translation of LangGraph's
`GraphRecursionError`, a `RunDeadline`, a cap on oversized tool results, MCP
tool-name namespacing, and mid-run steering. This file asks the only question a
LIVE journey can ask about them: **can the packaged app reach them at all?**

The answer today is one phase of yes and three of no, and the three noes are the
point. Each is a real seam that is implemented in the runtime and has no route
to the product, so each is recorded as `blocked` (exit `2`) with the file the
gap lives in — never as a pass, and never as a failure, because nothing is
broken: the wiring was simply never laid. Each turns green on its own the day
someone lays it.

    python3 tools/desktop-journeys/runtime_limits.py

Boot class `source · fresh · live BYOK`, which is the SAME class as
`composer_and_budgets.py`. Per README "Adding a claim", these phases belong
folded into that file rather than living here once RL-2..RL-4 stop being
blocked; they are kept separate while they are a reachability report rather
than a passing suite.

Phase order, by the state each consumes:

  RL-1  spends the virgin FTUE composer on one real multi-step run, and is the
        only phase that drives a model. It must run first — RL-2 reads the
        SAME run's log, and the later phases assert nothing about run state.
  RL-2  reads RL-1's ai-backend log; requires RL-1 to have produced a run.
  RL-3  reads the shipped client contract; no run state, no ordering need.
  RL-4  reads the shipped client contract; no run state, no ordering need.

Two candidate claims are deliberately absent rather than blocked. "A destructive
connector operation still gates under the bypass posture" and "two connectors
exposing the same bare tool name both stay callable" each need a CONNECTED MCP
server, and README "A journey can NEVER complete an OAuth connect" makes that a
precondition a journey cannot establish. They belong in `mcp_connected.py`,
against its hand-connected reuse profile.

The provider key is read from services/ai-backend/.env and only ever reaches the
password field — never printed, logged, or written to an artifact.
"""

from __future__ import annotations

from pathlib import Path

from _lib import (
    DriverSession,
    JourneyPlan,
    blocked_unless,
    byok_provider,
    require,
    wait_for_conversation_id,
    wait_for_new_run,
    wait_for_terminal_run,
)


STATE: dict[str, object] = {}


def log(line: str) -> None:
    print(f"  {line}", flush=True)


REPO_ROOT = Path(__file__).resolve().parents[2]

#: A prompt that costs several graph super-steps without needing a tool the
#: model may decline. RL-1 is about the run surviving its step ceiling, so the
#: work has to be genuinely multi-step rather than a single completion.
MULTI_STEP_PROMPT = (
    "Work through this in separate steps, and keep each step short. "
    "Step 1: list three primary colours. "
    "Step 2: for each, name one fruit of that colour. "
    "Step 3: reply with the three pairs as a single line."
)

#: The typed code `_TracedRuntimeCall.guard` raises a `GraphRecursionError` as
#: (`agent_runtime/execution/contracts.py`, `RuntimeErrorCode`). Its presence in
#: a healthy run's log is the regression this journey guards against.
RECURSION_CODE = "recursion_limit_exceeded"

#: The library exception the translation exists to REPLACE. If this reaches the
#: log untranslated, the user gets a paraphrased library error rather than the
#: actionable copy in `execution/runtime.py`.
RAW_GRAPH_ERROR = "GraphRecursionError"


def ai_backend_log(s: DriverSession) -> str:
    """The supervised ai-backend log for THIS journey's own app instance.

    Same source `composer_and_budgets.py` uses for budget refusals: the runtime
    log is the honest witness for a decision the transcript does not render.
    """

    path = s._user_data_dir / "logs" / "ai-backend.log"
    require(path.is_file(), f"no supervised ai-backend log at {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def contains(relative: str, needle: str) -> bool:
    """Whether a shipped source file mentions `needle`.

    RL-3 and RL-4 assert about the CLIENT contract, which is a build-time fact
    rather than a runtime one: an event the client's discriminated union does
    not name is dropped before any pixel exists. Reading the shipped file is the
    honest check — driving the app cannot distinguish "the run never compacted"
    from "the client would not have rendered it if it had".
    """

    path = REPO_ROOT / relative
    if not path.is_dir():
        return False
    for source in path.rglob("*.ts*"):
        if needle in source.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in_and_key(s: DriverSession) -> None:
    provider, key = byok_provider()
    STATE["provider"] = provider
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    s.sign_in_local()
    s.ftue_add_key(provider, key)


# ── RL-1: the new step ceiling does not bind an ordinary run ─────────────────
def rl1_a_healthy_multi_step_run_survives_the_step_ceiling(s: DriverSession) -> None:
    """A normal multi-step turn completes without hitting `recursion_limit`.

    `ExecutionHyperparameters.recursion_limit` newly bounds every graph
    invocation (default 500). A backstop set too low does not announce itself:
    ordinary runs simply start dying at their ceiling, and the user sees the
    step-limit copy for a request that was never looping. No unit test can
    price this, because the super-step COST of the real Deep Agents graph — how
    many steps one honest turn actually spends — only exists when the packaged
    stack runs the real graph against a real model.

    Runs FIRST: it spends the virgin FTUE composer, and RL-2 reads its log.
    """

    s.send_first_run_message(MULTI_STEP_PROMPT)
    conversation_id = wait_for_conversation_id(s)
    run_id = wait_for_new_run(s, conversation_id)
    STATE["run_id"] = run_id
    log(f"conversation={conversation_id} run={run_id}")

    record = wait_for_terminal_run(s, run_id)
    s.shot("rl1-run-completed")
    log(f"run status={record.get('status')!r}")

    text = ai_backend_log(s)
    STATE["log_read"] = True
    assert RECURSION_CODE not in text, (
        "an ordinary multi-step turn exhausted the graph's super-step "
        f"allowance — {RECURSION_CODE} appeared in the supervised runtime log. "
        "The backstop is binding healthy work, so either the ceiling is too "
        "low or the graph is spending steps it should not."
    )
    log("PASS  a healthy multi-step run finished inside the step ceiling")


# ── RL-2: forcing an overrun is not something a journey can do ───────────────
def rl2_forcing_a_step_overrun_needs_a_knob_the_supervisor_strips(
    s: DriverSession,
) -> None:
    """The typed step-limit message cannot be provoked through the packaged app.

    `ExecutionHyperparameters.recursion_limit` documents its own override
    surface as "the document (or `COPILOT_HP__EXECUTION__RECURSION_LIMIT`)".
    Neither reaches a supervised service: `buildServiceEnv`
    (`apps/desktop/main/services/service-env.ts`) copies ONLY the names in
    `ENV_PASSTHROUGH_ALLOWLIST` out of the desktop process env, and no
    `COPILOT_HP__*` name — nor any prefix rule — is on that list. A journey that
    exported the variable would boot an app that never saw it, drive a run that
    never overran, and report a confident green over an assertion that never
    executed.

    So the honest verdict is `blocked`, and it is a narrow one: this phase asks
    only that the ceiling be SETTABLE from the boot a journey controls. The day
    the allowlist carries the knob, this phase drops the block and asserts that
    the run fails with `recursion_limit_exceeded` carrying
    `RECURSION_LIMIT_MESSAGE` rather than a paraphrased `GraphRecursionError`.
    """

    require(STATE.get("log_read"), "RL-1 did not produce a supervised run log")

    text = ai_backend_log(s)
    # The half that IS provable from here: whatever else happened, no raw
    # library exception escaped into the log for the model to paraphrase.
    assert RAW_GRAPH_ERROR not in text, (
        f"{RAW_GRAPH_ERROR} reached the runtime log untranslated — the typed "
        "translation in agent_runtime/execution/runtime.py did not run"
    )

    allowlist = (
        REPO_ROOT / "apps" / "desktop" / "main" / "services" / "service-env.ts"
    ).read_text(encoding="utf-8", errors="replace")
    blocked_unless(
        "COPILOT_HP__" in allowlist,
        "the graph step ceiling cannot be set on a supervised service: no "
        "COPILOT_HP__* name is in ENV_PASSTHROUGH_ALLOWLIST "
        "(apps/desktop/main/services/service-env.ts), so a journey cannot "
        "provoke the typed recursion_limit_exceeded failure it wants to assert",
    )
    raise AssertionError(
        "the allowlist now carries a COPILOT_HP__* knob — tighten this phase to "
        "boot with COPILOT_HP__EXECUTION__RECURSION_LIMIT set low and assert "
        "the run fails with recursion_limit_exceeded"
    )


# ── RL-3: the tool-result cap has no client-visible surface ──────────────────
def rl3_a_capped_tool_result_cannot_reach_the_transcript(s: DriverSession) -> None:
    """The transcript cannot say a tool result was capped, because no client knows.

    `CompactionNotice` (`agent_runtime/context/memory/compaction.py`) records a
    real compaction in real numbers, and the runtime emits it. Neither
    `packages/api-types` nor `packages/chat-surface` mentions compaction at all,
    and the client's `isRuntimeEventEnvelope` guard DROPS an envelope whose
    `event_type` it does not know — the exact silent-discard failure CB-4's
    docstring names. So the claim "an oversized tool result is capped and the
    transcript says so" has no reachable second half.

    Asserted against the shipped contract rather than by driving a run on
    purpose: a run that simply never overflowed would look identical from the
    DOM, and would let this claim report a pass it did not earn.
    """

    blocked_unless(
        contains("packages/api-types/src", "compaction")
        or contains("packages/chat-surface/src", "compaction"),
        "no client contract names the compaction event: it is absent from both "
        "packages/api-types/src and packages/chat-surface/src, so a capped tool "
        "result is discarded by the client event guard before it can render",
    )
    raise AssertionError(
        "a client contract now names compaction — tighten this phase to drive a "
        "run whose tool result overflows and assert the divider renders"
    )


# ── RL-4: steering has no route from the app ─────────────────────────────────
def rl4_a_steer_cannot_leave_the_app(s: DriverSession) -> None:
    """Mid-run steering is implemented in the runtime and unreachable from the product.

    `runtime_api` registers `POST /runs/{run_id}/steer` and the ledger event
    `run_steered` is mirrored into `packages/api-types`. But `backend-facade`
    proxies agent-run sub-paths ONE BY ONE — events, surfaces, occupancy,
    sources/open, receipt/export, stream, cancel — with no catch-all, and it
    registers no `/steer`. CLAUDE.md's hard rule is that apps call only the
    facade, so no client can reach the endpoint; nothing under `packages/` or
    `apps/` references the path either.

    A second, independent blocker sits in this harness: `DriverSession.transport`
    invokes `transport.request` with `{method, path}` and carries no request
    body, so even a proxied route could not be POSTed a steer from here.

    Both are named because fixing only one leaves the claim unassertable.
    """

    facade = (
        REPO_ROOT / "services" / "backend-facade" / "src" / "backend_facade" / "app.py"
    )
    proxied = "/steer" in facade.read_text(encoding="utf-8", errors="replace")
    blocked_unless(
        proxied,
        "backend-facade registers no /v1/agent/runs/{run_id}/steer proxy, so "
        "the steer endpoint in runtime_api is unreachable from any app — a "
        "steer sent during an active run cannot leave the desktop client",
    )
    blocked_unless(
        "body" in DriverSession.transport.__doc__.lower()
        if DriverSession.transport.__doc__
        else False,
        "the facade now proxies /steer, but DriverSession.transport still sends "
        "no request body — extend it (and the app's transport.request IPC) "
        "before asserting that a steer reaches the model",
    )
    raise AssertionError(
        "both steering blockers are gone — tighten this phase to steer a live "
        "run and assert a run_steered event lands in its ledger"
    )


def main() -> int:
    plan = JourneyPlan("runtime-limits")
    plan.boot(
        "source · fresh",
        lambda: DriverSession(name="runtime-limits"),
        setup=sign_in_and_key,
        phases=[
            (
                "RL-1",
                "a healthy multi-step run finishes inside the step ceiling",
                rl1_a_healthy_multi_step_run_survives_the_step_ceiling,
            ),
            (
                "RL-2",
                "the step ceiling is settable from a supervised boot",
                rl2_forcing_a_step_overrun_needs_a_knob_the_supervisor_strips,
            ),
            (
                "RL-3",
                "a capped tool result reaches the transcript",
                rl3_a_capped_tool_result_cannot_reach_the_transcript,
            ),
            (
                "RL-4",
                "a steer sent during an active run leaves the app",
                rl4_a_steer_cannot_leave_the_app,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
