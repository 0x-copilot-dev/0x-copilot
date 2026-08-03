#!/usr/bin/env python3
"""J5 — the thinking disclosure: collapsed by default, expandable to the prose.

Picks the model through the UI rather than an env var, for two reasons: the
supervisor does not forward RUNTIME_DEFAULT_MODEL (so there is no env to set),
and the default desktop model is a small one that emits no reasoning at all —
which is exactly the trap that made an earlier capture look like a regression.

Captures three states:
  1. the wait  — "Thinking" shimmer, before any output
  2. collapsed — the settled disclosure, closed, sitting above the answer
  3. expanded  — the same disclosure opened, showing the model's reasoning

Run with THINKING_PROVIDER=openai|anthropic and MODEL_MATCH=<substring>.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

JOURNEY = "turn-interleaving/thinking_expandable"
PROVIDER = os.environ.get("THINKING_PROVIDER", "openai")
MODEL_MATCH = os.environ.get("MODEL_MATCH", "gpt-5.6-luna")

PROMPT = (
    "Work out, carefully and from first principles, the exact probability of "
    "drawing one ball of each colour when drawing 3 without replacement from a "
    "bag of 5 red, 4 blue and 3 green. Give it as a reduced fraction and prove "
    "it is in lowest terms. Do not use any tools."
)

JS_STATE = """(()=>{
  const chip=document.querySelector('[data-testid=cs-thinking]');
  const blocks=[...document.querySelectorAll('[data-testid=cs-thinking-block]')];
  return JSON.stringify({
    shimmer: !!chip,
    label: chip ? (chip.querySelector('.cs-thinking__label')||{}).textContent||'' : null,
    blocks: blocks.length,
    open: blocks.map((b)=>b.open),
    status: blocks.map((b)=>b.getAttribute('data-status')),
    bodyChars: blocks.map((b)=>((b.querySelector('div')||{}).innerText||'').trim().length),
  });
})()"""

JS_MODEL_ROWS = """(()=>[...document.querySelectorAll('[data-testid^=model-picker-row-]')]
  .map((n)=>({id:n.getAttribute('data-testid'), text:(n.innerText||'').trim().slice(0,60)})))"""


def log(line: str) -> None:
    print(line, flush=True)


def emit(outcome: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "journey": JOURNEY,
                "provider": PROVIDER,
                "model_match": MODEL_MATCH,
                "outcome": outcome,
                "reason": reason,
            }
        ),
        flush=True,
    )


def state(s: DriverSession) -> dict:
    return json.loads(s.evaluate(JS_STATE) or "{}")


def main() -> int:
    try:
        key = load_env_key(PROVIDER)
    except SystemExit as exc:
        emit("skipped", str(exc))
        return 3

    with DriverSession(name=f"expandable-{PROVIDER}") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}")

        # -- pick a reasoning model, the way a user does -------------------
        if not s.wait_for("[data-testid=composer-model-toggle]", 30):
            emit("blocked", "composer model toggle never rendered")
            return 2
        s.click("[data-testid=composer-model-toggle]")
        if not s.wait_for("[data-testid=model-picker]", 15):
            emit("blocked", "model picker never opened")
            return 2
        rows = json.loads(s.evaluate(JS_MODEL_ROWS) or "[]")
        target = next(
            (
                r
                for r in rows
                if MODEL_MATCH.lower() in r["id"].lower()
                or MODEL_MATCH.lower() in r["text"].lower()
            ),
            None,
        )
        if target is None:
            log(f"available models: {[r['id'] for r in rows][:20]}")
            emit("blocked", f"no model row matching {MODEL_MATCH!r}")
            return 2
        s.click(f"[data-testid={target['id']}]")
        log(f"PASS  model switched to {target['text']!r}")
        time.sleep(1)

        # -- send, and catch the shimmer on the way past ------------------
        s.send_first_run_message(PROMPT)
        caught_shimmer = False
        deadline = time.time() + 60
        while time.time() < deadline:
            st = state(s)
            if st.get("shimmer") and not caught_shimmer:
                caught_shimmer = True
                s.shot(f"01-{PROVIDER}-waiting")
                log(f"  shimmer: {st.get('label')!r}")
            if st.get("blocks"):
                break
            time.sleep(0.25)

        # -- settle ------------------------------------------------------
        previous, stable = None, 0
        deadline = time.time() + 240
        while time.time() < deadline:
            st = state(s)
            snap = json.dumps(st, sort_keys=True)
            if snap == previous and not st.get("shimmer"):
                stable += 1
                if stable >= 8:
                    break
            else:
                stable = 0
            previous = snap
            time.sleep(0.5)

        st = state(s)
        log(f"settled state: {st}")
        if not st.get("blocks"):
            emit(
                "blocked",
                f"no thinking block rendered on {target['text']!r} — the model "
                "may not have reasoned on this input",
            )
            return 2

        # 2 — collapsed (the default)
        s.shot(f"02-{PROVIDER}-collapsed")
        assert not any(st["open"]), (
            f"a thinking block opened itself: {st['open']}. Collapsed-by-default "
            "is the contract — an auto-expanding span pushes the answer down "
            "the column every time the model pauses."
        )
        log("PASS  collapsed by default")

        # 3 — expanded, by clicking the disclosure as a user would
        s.click("[data-testid=cs-thinking-block] > summary")
        time.sleep(0.6)
        opened = state(s)
        s.shot(f"03-{PROVIDER}-expanded")
        log(f"after click: {opened}")
        assert any(opened["open"]), "clicking the header did not expand the span"
        assert max(opened["bodyChars"]) > 0, (
            "the disclosure opened but its body is empty — the reasoning text "
            "never reached the part"
        )
        log(f"PASS  expands to {max(opened['bodyChars'])} chars of reasoning")

        emit(
            "passed",
            f"{st['blocks']} thinking block(s); collapsed by default, expands on click",
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
