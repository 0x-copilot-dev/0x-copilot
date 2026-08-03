#!/usr/bin/env python3
"""J4 — the "Thinking" shimmer must be on screen during the wait.

Everything else in this set asserts the SETTLED transcript. This one asserts the
opposite end: the seconds right after send, when the column used to be empty.
It therefore screenshots EAGERLY and repeatedly rather than waiting for the run
to finish — by the time a run settles the shimmer is gone, which is correct
behaviour and useless evidence.

Run with THINKING_PROVIDER=anthropic|openai.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

JOURNEY = "turn-interleaving/shimmer_visible"
PROVIDER = os.environ.get("THINKING_PROVIDER", "anthropic")

# Hard enough to make a reasoning model actually think, and NO tool instruction
# — a tool call can park on an approval, which correctly suppresses the shimmer
# and would make this journey measure the approval flow instead.
PROMPT = (
    "Work out, carefully and from first principles, the exact probability of "
    "drawing one ball of each colour when drawing 3 without replacement from a "
    "bag of 5 red, 4 blue and 3 green. Give it as a reduced fraction and prove "
    "it is in lowest terms. Do not use any tools."
)

JS_SHIMMER = """(()=>{
  const awaiting=document.querySelector('[data-testid=tc-chat-awaiting]');
  const chip=document.querySelector('[data-testid=cs-thinking]');
  const block=document.querySelector('[data-testid=cs-thinking-block]');
  return JSON.stringify({
    awaitingRow: !!awaiting,
    shimmer: !!chip,
    label: chip ? (chip.querySelector('.cs-thinking__label')||{}).textContent||'' : null,
    block: !!block,
    blockStatus: block ? block.getAttribute('data-status') : null,
  });
})()"""


def log(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    try:
        key = load_env_key(PROVIDER)
    except SystemExit as exc:
        print(
            json.dumps({"journey": JOURNEY, "outcome": "skipped", "reason": str(exc)})
        )
        return 3

    with DriverSession(name=f"shimmer-{PROVIDER}") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}")

        s.send_first_run_message(PROMPT)

        # Poll fast from the instant of send. The window we care about is a few
        # seconds long and closes on its own.
        seen_awaiting = False
        seen_block = False
        best_label = None
        shots = 0
        deadline = time.time() + 90
        while time.time() < deadline:
            state = json.loads(s.evaluate(JS_SHIMMER) or "{}")
            if state.get("shimmer") and shots < 3:
                shots += 1
                s.shot(f"{shots:02d}-{PROVIDER}-shimmer")
                log(f"  shimmer on screen: {state}")
            if state.get("awaitingRow"):
                seen_awaiting = True
            if state.get("block"):
                seen_block = True
                best_label = state.get("label") or best_label
            if state.get("label"):
                best_label = state["label"]
            if seen_awaiting and seen_block:
                break
            time.sleep(0.25)

        s.shot(f"99-{PROVIDER}-final")
        log(
            f"awaiting-row seen={seen_awaiting}  reasoning-block seen={seen_block}  "
            f"label={best_label!r}  shots={shots}"
        )

        if not (seen_awaiting or seen_block):
            print(
                json.dumps(
                    {
                        "journey": JOURNEY,
                        "provider": PROVIDER,
                        "outcome": "blocked",
                        "reason": "shimmer never appeared; run may have failed or answered instantly",
                    }
                )
            )
            return 2
        print(
            json.dumps(
                {
                    "journey": JOURNEY,
                    "provider": PROVIDER,
                    "outcome": "passed",
                    "reason": f"awaiting={seen_awaiting} reasoning_block={seen_block}",
                }
            )
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
