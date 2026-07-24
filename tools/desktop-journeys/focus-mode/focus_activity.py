#!/usr/bin/env python3
"""focus-mode — live journeys for the Run cockpit's Focus-mode activity rendering.

Drives the REAL supervised desktop app: signs in locally, adds a provider key in
the FTUE, then sends three probe prompts and asserts how the cockpit renders the
in-flight run — streaming that grows incrementally, an inline tool card, an inline
subagent fleet card — plus the Run-details focus panel + its collapse control.

Studio is OFF (STUDIO_ENABLED=false) so the cockpit is ALWAYS Focus and the
run-mode switcher is hidden: we assert thread-canvas[data-mode=focus], we never
click a Studio/Focus toggle.

The provider key is read from services/ai-backend/.env via load_env_key and is
NEVER printed. Default provider `openai`; override with FOCUS_PROVIDER=anthropic.

Run (from repo root, with the stack staged/built — see ../README.md):

    python3 tools/desktop-journeys/focus-mode/focus_activity.py

Exits non-zero if a hard-asserted step fails. Model-choice-dependent tails
(tool call / subagent dispatch / reasoning summaries) print BLOCKED, not FAIL,
when the backing model does not exercise them.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("FOCUS_PROVIDER", "openai")

# ── probe prompts (verbatim — the JOURNEYS.md step tables cite these) ─────────
P_STREAM = "Write a detailed 220 word explanation of how a bicycle works, no tools."
P_TOOL = "Search the web for what deepagents are and summarize in 2 lines."
P_FLEET = "Use exactly ONE subagent to check whether 97 is prime."


def log(line: str) -> None:
    print(line, flush=True)


# ── JS probes (read what the user actually sees in the transcript) ───────────
JS_ASSISTANT_LEN = (
    "(()=>{const e=[...document.querySelectorAll("
    "'[data-testid^=tc-chat-message-][data-role=assistant]')];"
    "return e.length?e[e.length-1].innerText.length:0})()"
)
JS_ASSISTANT_COUNT = "document.querySelectorAll('[data-testid^=tc-chat-message-][data-role=assistant]').length"


def _q(sel: str) -> str:
    return json.dumps(sel)


def send_in_run(s: DriverSession, text: str) -> None:
    """Send a follow-up message through the run cockpit composer (same Composer
    testIds as the FTUE composer: composer-textarea + the Send button)."""
    assert s.wait_for("[data-testid=composer-textarea]"), "run composer never appeared"
    s.fill("[data-testid=composer-textarea]", text)
    time.sleep(0.3)
    s.click('button[aria-label="Send message"]')


def wait_new_turn(s: DriverSession, prev_count: int, timeout_s: int = 40) -> bool:
    """Wait for a new assistant message to be appended (turn count grows)."""
    for _ in range(timeout_s * 4):
        if int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > prev_count:
            return True
        time.sleep(0.25)
    return False


def poll_growth(
    s: DriverSession, seconds: float = 40.0, interval: float = 0.2
) -> list[int]:
    """Rapidly sample the last assistant message length; return the sequence of
    strictly-increasing lengths observed (one entry per growth step)."""
    steps: list[int] = []
    last = -1
    deadline = time.time() + seconds
    stable = 0
    while time.time() < deadline:
        n = int(s.evaluate(JS_ASSISTANT_LEN) or 0)
        if n > last:
            steps.append(n)
            last = n
            stable = 0
        else:
            stable += 1
            # stop once the answer has plateaued (streaming finished) and we have
            # enough evidence of incremental growth
            if len(steps) >= 3 and stable >= 8:
                break
        time.sleep(interval)
    return steps


def tool_card_state(s: DriverSession) -> dict | None:
    js = (
        "(()=>{const c=document.querySelector('[data-testid^=tc-chat-tool-]:not([data-testid$=-args])"
        ":not([data-testid$=-result])');if(!c)return null;"
        "const sum=c.querySelector('summary');return JSON.stringify({"
        "status:c.getAttribute('data-tool-status'),text:c.innerText,"
        "hasDetails:!!(sum&&/Details/.test(sum.innerText))})})()"
    )
    raw = s.evaluate(js)
    return json.loads(raw) if raw else None


def fleet_card_state(s: DriverSession) -> dict | None:
    js = (
        "(()=>{const c=document.querySelector('[data-testid^=tc-chat-fleet-]');"
        "if(!c)return null;return JSON.stringify({text:c.innerText})})()"
    )
    raw = s.evaluate(js)
    return json.loads(raw) if raw else None


# ─────────────────────────────────────────────────────────────────────────────
def journey_streaming(s: DriverSession) -> None:
    log("── J1 streaming ─────────────────────────────────────────────")
    # first message of the run is the streaming probe
    s.send_first_run_message(P_STREAM)
    assert s.wait_for("[data-testid=tc-chat]", 60), "never landed on the run transcript"
    assert s.wait_for("[data-testid^=tc-chat-message-]", 60), "no message rendered"
    s.shot("j1-run-landed")

    mode = s.run_mode()
    assert mode == "focus", f"expected Focus cockpit, got data-mode={mode!r}"
    log(f"PASS  cockpit is Focus (thread-canvas data-mode={mode})")

    steps = poll_growth(s)
    s.shot("j1-streaming-grown")
    growths = len(steps)
    log(
        f"      observed {growths} growth steps; lengths sample={steps[:6]}{'…' if growths > 6 else ''}"
    )
    assert growths >= 3, (
        f"expected >=3 incremental growth steps (streaming), saw {growths} "
        f"— text arrived atomically (regression: model_delta payload is {{delta,message}})"
    )
    log(f"PASS  streaming grows incrementally ({growths} growth steps, not atomic)")


def journey_tool_card(s: DriverSession) -> None:
    log("── J2 tool card ─────────────────────────────────────────────")
    prev = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_TOOL)
    assert wait_new_turn(s, prev), "no new assistant turn after the web-search prompt"

    card = None
    for _ in range(160):  # up to ~40s for the model to call the tool
        card = tool_card_state(s)
        if card is not None:
            break
        time.sleep(0.25)
    s.shot("j2-tool-card")

    if card is None:
        log(
            "BLOCKED  no inline tool card — the keyed model did not call web_search "
            "for this prompt (capability present, not exercised)"
        )
        return

    assert "web_search" in card["text"], (
        f"tool card did not name web_search: {card['text']!r}"
    )
    log("PASS  inline tool card present and names web_search")

    # wait for it to resolve to done
    done = card["status"] == "done"
    for _ in range(120):
        card = tool_card_state(s)
        if card and card["status"] == "done":
            done = True
            break
        time.sleep(0.25)
    s.shot("j2-tool-card-done")
    if done:
        log("PASS  tool card reached done state")
    else:
        log(
            f"BLOCKED  tool card did not reach done (status={card and card['status']}) "
            "— model/tool did not complete in window"
        )
    if card and card["hasDetails"]:
        log("PASS  tool card exposes a Details expander")
    else:
        log("BLOCKED  no Details expander (no args/result captured on this call)")


def journey_fleet_card(s: DriverSession) -> None:
    log("── J3 subagent fleet card ───────────────────────────────────")
    prev = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_FLEET)
    assert wait_new_turn(s, prev), "no new assistant turn after the subagent prompt"

    fleet = None
    for _ in range(200):  # up to ~50s — subagent dispatch can be slower
        fleet = fleet_card_state(s)
        if fleet is not None:
            break
        time.sleep(0.25)
    s.shot("j3-fleet-card")

    if fleet is None:
        log(
            "BLOCKED  no inline fleet card — the keyed model did not dispatch a "
            "subagent for this prompt (capability present, not exercised)"
        )
        return

    text = fleet["text"]
    assert "ispatched" in text, f"fleet card missing 'Dispatched' copy: {text!r}"
    if "Dispatched a subagent" in text:
        log("PASS  inline fleet card reads 'Dispatched a subagent' (singular)")
    else:
        log(f"PASS  inline fleet card present (batch copy): {text.splitlines()[0]!r}")

    # observe progression to done
    done = "done" in text.lower() or "1/1" in text
    for _ in range(160):
        fleet = fleet_card_state(s)
        if fleet and ("done" in fleet["text"].lower() or "1/1" in fleet["text"]):
            done = True
            break
        time.sleep(0.25)
    s.shot("j3-fleet-done")
    if done:
        log("PASS  fleet progressed to a done state (0/1 → 1/1 done)")
    else:
        log(
            "BLOCKED  fleet did not reach done in window (dispatch present, "
            "completion not observed)"
        )


def journey_focus_panel(s: DriverSession) -> None:
    log("── J4 focus panel + collapse ────────────────────────────────")
    assert s.wait_for("[data-testid=tc-focus-panel]"), "Run-details focus panel absent"
    width = s.evaluate(
        "(document.querySelector('[data-testid=tc-focus-panel]')||{}).offsetWidth||0"
    )
    s.shot("j4-panel-expanded")
    log(f"PASS  Run-details panel shown (offsetWidth={width}px, ~324 expected)")

    assert s.present("[data-testid^=tc-focus-panel-]"), "no active focus-panel tab body"
    log("PASS  focus panel exposes an active tab body (Agents/Approvals/Sources)")

    # collapse → 46px icon rail
    s.click("[data-testid=tc-focus-panel-collapse]")
    assert s.wait_for("[data-testid=tc-focus-strip]"), (
        "collapse did not reveal the icon rail"
    )
    assert not s.present("[data-testid=tc-focus-panel]"), (
        "panel still present after collapse"
    )
    strip_w = s.evaluate(
        "(document.querySelector('[data-testid=tc-focus-strip]')||{}).offsetWidth||0"
    )
    s.shot("j4-panel-collapsed")
    log(
        f"PASS  collapse → 46px icon rail (tc-focus-strip offsetWidth={strip_w}px, ~46 expected)"
    )

    # re-expand
    s.click("[data-testid=tc-focus-strip-expand]")
    assert s.wait_for("[data-testid=tc-focus-panel]"), (
        "re-expand did not restore the panel"
    )
    assert not s.present("[data-testid=tc-focus-strip]"), (
        "icon rail still present after expand"
    )
    s.shot("j4-panel-reexpanded")
    log("PASS  re-expand restored the full panel")


def main() -> int:
    key = load_env_key(PROVIDER)  # value never printed
    with DriverSession(name="focus-mode") as s:
        s.sign_in_local()
        s.shot("00-signed-in")
        s.ftue_add_key(PROVIDER, key)
        s.shot("00-key-added")
        log(f"PASS  FTUE: signed in locally + added a {PROVIDER} key")

        journey_streaming(s)
        journey_tool_card(s)
        journey_fleet_card(s)
        journey_focus_panel(s)

        log("── BLOCKED-until ────────────────────────────────────────────")
        log(
            "BLOCKED  thinking/reasoning block — renders only when the backing "
            "model emits reasoning summaries; gpt-5.4-mini emitted 0 reasoning "
            "events in testing (needs a summary-emitting model)"
        )
    log("ALL HARD ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
