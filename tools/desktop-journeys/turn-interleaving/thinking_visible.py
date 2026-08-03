#!/usr/bin/env python3
"""J3 — the model's THINKING must reach the transcript, on every provider.

Run with THINKING_PROVIDER=anthropic|openai.

Reasoning was invisible for a different reason per provider, so this journey is
parameterised rather than duplicated:

* Anthropic returns thinking blocks with an empty field unless
  `thinking.display: "summarized"` is asked for — and defaults to "omitted" on
  the 5 generation, so we paid for the tokens and dropped the text.
* OpenAI returns a `reasoning` output block only when it actually reasoned; a
  trivial prompt produces none, which is why the prompt below is deliberately
  hard rather than convenient.

The prompt also forces a tool call, so a passing run shows thinking AND activity
interleaved rather than thinking alone.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

JOURNEY = "turn-interleaving/thinking_visible"
PROVIDER = os.environ.get("THINKING_PROVIDER", "anthropic")

# Hard enough that a reasoning model actually reasons (adaptive thinking skips
# easy inputs), and grounded enough that it must search.
PROMPT = (
    "First work out, carefully, the exact probability of drawing one ball of "
    "each colour when drawing 3 without replacement from a bag of 5 red, 4 "
    "blue and 3 green — give it as a reduced fraction and justify why it is in "
    "lowest terms. Then use the web_search tool once to find the official "
    "Python documentation page for math.comb, and say how that function relates "
    "to the calculation you just did."
)

JS_PARTS = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    const id=li.getAttribute('data-testid')||'';
    return {
      id,
      role: li.getAttribute('data-role')||null,
      partType: li.getAttribute('data-part-type')||null,
      partSeq: li.getAttribute('data-part-seq')||null,
      reasoningNodes: li.querySelectorAll('[data-part-type=reasoning], .aui-reasoning, [data-testid*=reasoning]').length,
      activity: !!li.querySelector('[data-testid=tool-run-group]')
        || id.startsWith('tc-chat-tool-') || id.startsWith('tc-chat-fleet-'),
      text: (li.innerText||'').trim().slice(0,200),
    };
  }));
})()"""

JS_BUSY = """(()=>{
  const g=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  return JSON.stringify({running:g.some((n)=>n.getAttribute('data-state')==='running'),
                         spin:!!document.querySelector('[data-tool-status=running]')});
})()"""


def log(line: str) -> None:
    print(line, flush=True)


def emit(outcome: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "journey": JOURNEY,
                "provider": PROVIDER,
                "outcome": outcome,
                "reason": reason,
            }
        ),
        flush=True,
    )


def main() -> int:
    try:
        key = load_env_key(PROVIDER)
    except SystemExit as exc:
        emit("skipped", str(exc))
        return 3

    with DriverSession(name=f"thinking-{PROVIDER}") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        s.send_first_run_message(PROMPT)
        if not s.wait_for("[data-testid=tc-chat]", 120):
            # Screenshot BEFORE failing. "transcript never opened" with no
            # artifact is undiagnosable after the app is torn down — the reason
            # (an error banner, a model picker, a stuck composer) is only ever
            # visible on screen.
            s.shot(f"00-{PROVIDER}-no-transcript")
            body = s.evaluate("document.body.innerText.slice(0,600)") or ""
            log(f"VISIBLE UI:\n{body}")
            emit("blocked", "transcript never opened; see 00-*-no-transcript.png")
            return 2

        deadline, previous, stable = time.time() + 300, None, 0
        rows: list[dict] = []
        while time.time() < deadline:
            busy = json.loads(s.evaluate(JS_BUSY) or "{}")
            rows = json.loads(s.evaluate(JS_PARTS) or "[]")
            snap = json.dumps(rows, sort_keys=True)
            if (
                not (busy.get("running") or busy.get("spin"))
                and rows
                and snap == previous
            ):
                stable += 1
                if stable >= 10:
                    break
            else:
                stable = 0
            previous = snap
            time.sleep(0.5)

        s.shot(f"01-{PROVIDER}-settled")
        log(f"transcript rows ({PROVIDER}):")
        for i, r in enumerate(rows):
            log(
                f"  [{i}] role={r['role']} part={r['partType']}/{r['partSeq']} "
                f"reasoningNodes={r['reasoningNodes']} activity={r['activity']} "
                f":: {r['text'][:70]!r}"
            )

        reasoning_rows = [
            r for r in rows if r["partType"] == "reasoning" or r["reasoningNodes"] > 0
        ]
        activity_rows = [r for r in rows if r["activity"]]
        log(f"reasoning rows={len(reasoning_rows)}  activity rows={len(activity_rows)}")

        if not reasoning_rows:
            # Distinguish "the provider sent none" from "we dropped it": the
            # journey cannot tell, so it must not claim either.
            emit(
                "blocked",
                "no reasoning rendered — either the model skipped thinking on "
                "this input, or the runtime dropped it. Check the run's events.",
            )
            return 2

        s.shot(f"02-{PROVIDER}-thinking")
        emit("passed", f"{len(reasoning_rows)} reasoning row(s) rendered")
        return 0


if __name__ == "__main__":
    sys.exit(main())
