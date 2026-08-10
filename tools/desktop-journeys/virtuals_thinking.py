#!/usr/bin/env python3
"""virtuals-thinking — a gateway model's reasoning reaches the transcript.

Guards the whole chain for a Virtuals-hosted reasoning model (Kimi K3), because
every link in it has already failed silently at least once and none of the
failures raised anything.

    provider sends it → langchain preserves it → the runtime emits it
      → the transcript paints it → both cockpit modes paint it

What this exists to stop coming back:

* **The client dropping it.** `ChatOpenAI` targets the official OpenAI
  specification and documents that non-standard fields (`reasoning_content`,
  `reasoning_details`) are neither extracted nor preserved. Virtuals streams
  Kimi K3's chain of thought on exactly that field and BILLS it
  (`usage.completion_tokens_details.reasoning_tokens`), so before the
  preserving subclass in `deep_agent_builder` the user paid for reasoning that
  could never be displayed — and `StreamMessageParser.compat_reasoning_delta`,
  written for this precise shape, was unreachable code.
* **The model being unreachable.** The catalog ships most rows
  `enabled: false` and the composer opens on a curated short list, so Kimi K3
  was not selectable at all until first-run began offering every configured
  model. VT-1 fails the moment that regresses.
* **A mislabelled verdict.** Every phase reads the model off the composer pill,
  because the run record does not carry it: an earlier pass produced a full set
  of "Kimi K3" screenshots that were actually Claude Sonnet 5.

Also asserts the thing that would be WORSE than losing the reasoning: raw chain
of thought leaking into the visible answer.

    COPILOT_JOURNEY_DOTENV=/path/to/main/services/ai-backend/.env \\
      python3 tools/desktop-journeys/virtuals_thinking.py

Skips (exit 3) without VIRTUALS_ACP_KEY. Spends real tokens on a real model.
"""

from __future__ import annotations

import json
import time

from _lib import (
    DriverSession,
    JourneyPlan,
    PhaseSkipped,
    load_env_key,
    wait_for_conversation_id,
    wait_for_new_run,
    wait_for_terminal_run,
)

PROVIDER = "virtuals"
MODEL_FRAGMENT = "Kimi K3"

# Has to be reasoned through rather than recalled, and bounded so the spend
# lands in thinking rather than prose.
PROMPT = (
    "Three switches outside a windowless room control three bulbs inside. "
    "You may flip switches freely but may enter the room only once. "
    "Determine which switch controls which bulb. "
    "Reply with the procedure only, under 60 words."
)

# The same nodes transcript_rendering's TR-13/TR-15 read, so a verdict here is
# comparable with those rather than a second, private question.
JS_STATE = """
(() => {
  const rail = document.querySelector('[data-testid=run-workspace-rail]');
  const pill = document.querySelector('.atlas-model-pill');
  return {
    mode: rail ? rail.getAttribute('data-mode') : null,
    pill: pill ? (pill.innerText || '').trim().split('\\n')[0] : null,
    reasoningNodes: document.querySelectorAll(
      '[data-part-type=reasoning], .aui-reasoning, [data-testid*=reasoning], [data-testid=cs-thinking-block]'
    ).length,
    // The disclosure's own summary copy, so a phase can say WHAT it saw rather
    // than only that a node existed.
    disclosure: [...document.querySelectorAll('summary, [data-testid=cs-thinking]')]
      .map((n) => (n.textContent || '').trim())
      .find((t) => /thought process|thinking/i.test(t)) || null,
    assistantRows: document.querySelectorAll('[data-testid^=tc-chat-message-assistant]').length,
  };
})()
"""

_STATE: dict[str, object] = {}


def _log(message: str) -> None:
    print(f"  {message}", flush=True)


def _event_kinds(session: DriverSession, run_id: str) -> dict[str, int]:
    payload = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = payload.get("events", payload) if isinstance(payload, dict) else payload
    kinds: dict[str, int] = {}
    for event in events or []:
        name = str(event.get("event_type") or event.get("type") or "?")
        kinds[name] = kinds.get(name, 0) + 1
    _STATE["events"] = events or []
    return kinds


def sign_in_and_key(session: DriverSession) -> None:
    key = load_env_key(PROVIDER)  # value never printed
    print(f"[virtuals-thinking] provider={PROVIDER} key_len={len(key)} (withheld)")
    session.sign_in_local()
    session.ftue_add_key(PROVIDER, key)


# ── phases ───────────────────────────────────────────────────────────────────
def vt1_kimi_is_selectable(session: DriverSession) -> None:
    """Kimi K3 is offered by the picker and can be chosen.

    First run offers every CONFIGURED model rather than the curated short list,
    precisely because it renders outside the shell and has no Settings to reach
    the full catalog from. Before that, this model was absent from the picker
    entirely and no amount of backend curation could put it there.
    """
    assert session.select_model(MODEL_FRAGMENT), (
        f"{MODEL_FRAGMENT!r} is not selectable — the picker is back on the "
        "curated short list, or the Virtuals catalog did not land"
    )
    pill = session.model_pill()
    _log(f"pill={pill!r}")
    assert pill is not None and "kimi" in pill.lower(), (
        f"pill did not settle on Kimi K3 (got {pill!r})"
    )
    session.shot("kimi-selected")


def vt2_run_completes_on_kimi(session: DriverSession) -> None:
    """A Virtuals-backed run on Kimi K3 reaches an answer."""
    session.send(PROMPT, timeout_s=300)
    conversation_id = wait_for_conversation_id(session)
    run_id = wait_for_new_run(session, conversation_id)
    run = wait_for_terminal_run(session, run_id)
    _STATE["run_id"] = run_id
    pill = session.model_pill()
    _log(f"run={run_id} status={run.get('status')!r} pill={pill!r}")
    session.shot("run-answered")
    assert str(run.get("status")) == "completed", f"run ended {run.get('status')!r}"
    # The pill is the model of record here: the run object does not carry one,
    # and asserting on nothing is how a Sonnet transcript got filed as Kimi.
    assert pill is not None and "kimi" in pill.lower(), (
        f"the run was not on Kimi K3 (pill={pill!r}); every verdict below would "
        "describe the wrong model"
    )


def vt3_runtime_emits_reasoning_events(session: DriverSession) -> None:
    """The provider's reasoning_content became reasoning events on the run.

    The seam that must hold: `ChatOpenAI` discards the field, so the funnel's
    preserving subclass re-attaches it onto `additional_kwargs`, where
    `StreamMessageParser.compat_reasoning_delta` reads it. Zero events here
    means the chain broke at the client and nothing downstream can compensate.
    """
    run_id = _STATE.get("run_id")
    if not isinstance(run_id, str):
        raise PhaseSkipped("VT-2 never bound a run id")
    kinds = _event_kinds(session, run_id)
    _log("event kinds: " + json.dumps(kinds, sort_keys=True))
    reasoning = {k: v for k, v in kinds.items() if "reason" in k or "think" in k}
    _log(f"reasoning events: {reasoning or 'NONE'}")
    assert reasoning, (
        "the runtime emitted NO reasoning events — the gateway's "
        "reasoning_content never survived the langchain client, so the "
        "transcript has nothing to paint (see _ReasoningPreservingChatOpenAI)"
    )


def vt4_studio_paints_the_disclosure(session: DriverSession) -> None:
    """Studio renders the thought-process disclosure for that reasoning."""
    state = session.evaluate(JS_STATE)
    _STATE["studio"] = state
    _log(json.dumps(state))
    session.shot("studio-thinking")
    assert state["reasoningNodes"] > 0, (
        "the run carried reasoning events but Studio painted no disclosure"
    )


def vt5_reasoning_never_leaks_into_the_answer(session: DriverSession) -> None:
    """Chain of thought is shown AS reasoning, never as the reply.

    The failure this guards is worse than losing the thinking: a model whose
    reasoning lands in `content` puts raw chain of thought in front of the user
    as the answer. `think_scrubber` exists for the inline-tag shape; this
    asserts the sibling-field shape never takes that path either.
    """
    events = _STATE.get("events") or []
    visible = "".join(
        str(
            (event.get("payload") or {}).get("text")
            or (event.get("payload") or {}).get("delta")
            or (event.get("payload") or {}).get("content")
            or ""
        )
        for event in events  # type: ignore[union-attr]
        if str(event.get("event_type") or "") in {"model_delta", "final_response"}
    )
    _log(f"visible answer ({len(visible)} chars): {visible[:160]!r}")
    lowered = visible.lower()
    for marker in ("<think", "</think", "reasoning_content"):
        assert marker not in lowered, (
            f"chain-of-thought marker {marker!r} reached the visible answer"
        )


def vt6_focus_matches_studio(session: DriverSession) -> None:
    """Parity: Focus paints the same reasoning Studio does."""
    studio = _STATE.get("studio") or session.evaluate(JS_STATE)
    session.evaluate(
        """
        (() => {
          const b = [...document.querySelectorAll('button, [role=tab], [role=radio]')]
            .find((x) => (x.textContent || '').trim() === 'Focus');
          if (b) b.click();
        })()
        """
    )
    time.sleep(2.5)
    focus = session.evaluate(JS_STATE)
    session.shot("focus-thinking")
    _log(f"studio={json.dumps(studio)}")
    _log(f"focus ={json.dumps(focus)}")
    assert focus["mode"] == "focus", (
        f"expected the Focus cockpit, got {focus['mode']!r}"
    )
    assert (focus["reasoningNodes"] > 0) == (studio["reasoningNodes"] > 0), (  # type: ignore[index]
        f"thinking parity broken: studio={studio['reasoningNodes']} "  # type: ignore[index]
        f"focus={focus['reasoningNodes']} — the same run renders reasoning in "
        "one cockpit and not the other"
    )


def main() -> int:
    plan = JourneyPlan("virtuals-thinking")
    plan.boot(
        "source · virtuals · kimi-k3",
        lambda: DriverSession(name="virtuals-thinking"),
        setup=sign_in_and_key,
        phases=[
            ("VT-1", "Kimi K3 is offered by the picker", vt1_kimi_is_selectable),
            ("VT-2", "a run on Kimi K3 answers", vt2_run_completes_on_kimi),
            (
                "VT-3",
                "the runtime emits reasoning events",
                vt3_runtime_emits_reasoning_events,
            ),
            (
                "VT-4",
                "Studio paints the thought-process disclosure",
                vt4_studio_paints_the_disclosure,
            ),
            (
                "VT-5",
                "reasoning never leaks into the visible answer",
                vt5_reasoning_never_leaks_into_the_answer,
            ),
            (
                "VT-6",
                "Focus paints the same reasoning as Studio",
                vt6_focus_matches_studio,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
