#!/usr/bin/env python3
"""Live reproduction: asking for a local folder that was never attached.

The reported failure: a user asked the agent to `ls /workspace/`, read a CSV
and write it back. No folder was attached to the chat, so the workspace backend
answered — correctly — "Local workspace access is unavailable. Create an
artifact or download instead; no local file was changed." The agent received
that, adapted, and explained the situation in chat.

The user nevertheless saw the Studio canvas shouting "RUN INTERRUPTED / This
run needs attention / This operation failed." beside a chat pane holding a
complete, correct answer, under a "Retry run" button that was wired to an SSE
reconnect and could not retry anything.

Three separate faults produced that, and this journey asserts all three are
gone against the real app:

  1. the declined capability is not classified as a failure (neutral card),
  2. the canvas reports on the canvas ("Answered in chat"), not on the run,
  3. no run-level action is offered that the system cannot perform.

The provider key is read from services/ai-backend/.env and only ever typed
into the app's password field.
"""

from __future__ import annotations

import json
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _lib import DriverSession, load_env_key  # noqa: E402

# Overridable like every sibling journey: a full-suite run pins one provider
# (and one cheap model) across all of them, which a hardcoded value blocked.
PROVIDER = os.environ.get("RICH_CHAT_PROVIDER", "openai")

# The prompt from the report, verbatim.
PROMPT = (
    "Run `ls /workspace/` to find the mounted folder, read seed.csv inside it, "
    "then write the file back to that same path with one extra column named "
    "`note` whose value is `checked` on every row. Use your filesystem tools."
)

JS_ASSISTANT_COUNT = (
    'document.querySelectorAll("[data-testid^=tc-chat-message-]'
    '[data-role=assistant]").length'
)

# Fault 2: the canvas must never render a verdict about the run. These strings
# were deleted with the panel's retry button; their reappearance is the
# regression.
JS_CANVAS_ALARM = """(()=>{
  const body=document.body?document.body.innerText:'';
  return /RUN INTERRUPTED|This run needs attention|This operation failed/i
    .test(body) ? body.slice(0,400) : '';
})()"""

# Fault 3: an action the system cannot perform. The old button was literally
# labelled "Retry run"; the terminal beat's action is only legitimate on a run
# that actually died, which this one did not.
JS_RUN_LEVEL_ACTION = """(()=>{
  const hits=[...document.querySelectorAll('button')]
    .map((b)=>(b.innerText||'').trim())
    .filter((t)=>/^Retry run$|^Start a new run with this goal$/.test(t));
  const beat=document.querySelector('[data-testid="run-terminal-beat"]');
  return JSON.stringify({buttons:hits, terminalBeat:!!beat});
})()"""

JS_CANVAS_STATE = """(()=>{
  const p=document.querySelector('[data-testid="canvas-lifecycle-panel"]');
  return p?JSON.stringify({
    lifecycle:p.getAttribute('data-lifecycle'),
    text:(p.innerText||'').slice(0,200),
  }):'';
})()"""

JS_TOOL_CARDS = """(()=>JSON.stringify(
  [...document.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status]')]
    .map((n)=>({
      testId:n.getAttribute('data-testid'),
      status:n.getAttribute('data-tool-status'),
      text:(n.innerText||'').slice(0,240),
    }))
))()"""

JS_LAST_ASSISTANT = """(()=>{
  const nodes=[...document.querySelectorAll(
    '[data-testid^=tc-chat-message-][data-role=assistant]')];
  const last=nodes[nodes.length-1];
  return last?(last.innerText||'').slice(0,600):'';
})()"""


def log(line: str) -> None:
    print(line, flush=True)


def wait_for_answer(s: DriverSession, before: int, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > before:
            return
        time.sleep(0.5)
    raise AssertionError(f"no assistant answer within {timeout_s}s")


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] in {"-h", "--help"}:
            print(
                "Usage: python3 tools/desktop-journeys/chat-rich-cards/"
                "declined_capability.py\n"
                "Asks for a folder that was never attached and requires the "
                "declined capability to stay out of the failure taxonomy."
            )
            return 0
        raise SystemExit(f"unsupported argument: {sys.argv[1]!r}; use --help")

    key = load_env_key(PROVIDER)
    with DriverSession(name="declined-capability") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
        s.send_first_run_message(PROMPT)
        assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
        wait_for_answer(s, before)
        # Let the terminal frames land before reading end-state.
        time.sleep(4)
        s.shot("declined-capability")

        answer = s.evaluate(JS_LAST_ASSISTANT) or ""
        cards = json.loads(s.evaluate(JS_TOOL_CARDS) or "[]")
        canvas = s.evaluate(JS_CANVAS_STATE) or ""
        alarm = s.evaluate(JS_CANVAS_ALARM) or ""
        actions = json.loads(s.evaluate(JS_RUN_LEVEL_ACTION) or "{}")

        log(f"      tool cards: {len(cards)}")
        for card in cards:
            log(f"        {card['testId']} status={card['status']}")
        log(f"      canvas: {canvas or '(no lifecycle panel)'}")

        # The run answered the user. Everything below is about HOW that was
        # reported, so a missing answer means the journey never reached the
        # state under test.
        assert answer.strip(), "assistant turn appeared but carried no answer text"

        # Fault 1 — a declined capability is not a failure.
        errored = [c for c in cards if c["status"] == "error"]
        assert not errored, (
            f"a declined capability was still rendered as a failed step: {errored!r}"
        )

        # Fault 2 — the canvas reports on the canvas.
        assert not alarm, f"the canvas rendered a verdict on the run:\n{alarm}"
        if canvas:
            state = json.loads(canvas)["lifecycle"]
            assert state in {"chat_only", "complete_empty", "presenting"}, (
                f"unexpected canvas lifecycle {state!r} for an answered run"
            )

        # Fault 3 — no action the system cannot perform.
        assert not actions.get("buttons"), (
            f"a run-level action was offered on a run that answered: {actions!r}"
        )
        assert not actions.get("terminalBeat"), (
            "a terminal verdict beat rendered for a run that completed"
        )

    log("PASS  declined capability stayed out of the failure taxonomy")
    log("PASS  canvas rendered no verdict on the run, and offered no dead action")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
