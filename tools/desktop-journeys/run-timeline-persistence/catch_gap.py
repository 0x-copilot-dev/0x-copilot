#!/usr/bin/env python3
"""run-timeline-persistence — catch the mini-timeline gap ON CAMERA.

Companion to ``timeline_persists.py``. That script proves the strip never
disappears by frame counting; this one tries to photograph the disappearance,
which is only possible on a PRE-FIX build.

The gap is transient (~350ms — the window between a send starting a new run and
that run's first event landing), so a screenshot taken on a timer is a coin
flip. Instead an in-page watcher polls every 20ms and latches the instant the
mini-timeline slot goes missing; the driver polls that latch and fires the
screenshot as soon as it trips. It also records the exact gap duration.

    python3 tools/desktop-journeys/run-timeline-persistence/catch_gap.py

Expected results:
    pre-fix build   → GAP CAUGHT, screenshot written, duration reported
    fixed build     → no gap observed (exit 0, nothing to photograph)
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("JOURNEY_PROVIDER", "anthropic")
P_FIRST = "In one short sentence, what is a bicycle?"
P_SECOND = "Now say the same thing in exactly three words."

# Latch the first frame where the cockpit is mounted but the strip is not.
# Guarded on the canvas being present so a navigation frame cannot false-trip.
JS_WATCH = """
(() => {
  if (window.__gapStop) window.__gapStop();
  window.__gap = { seen: false, startedAt: null, endedAt: null, frames: 0 };
  const tick = () => {
    const canvas = document.querySelector('[data-testid=thread-canvas]');
    if (!canvas) return;
    const mini = document.querySelector('[data-testid=tc-mini-timeline-slot]');
    if (!mini) {
      window.__gap.frames += 1;
      if (!window.__gap.seen) {
        window.__gap.seen = true;
        window.__gap.startedAt = performance.now();
      }
    } else if (window.__gap.seen && window.__gap.endedAt === null) {
      window.__gap.endedAt = performance.now();
    }
  };
  const h = setInterval(tick, 20);
  window.__gapStop = () => { clearInterval(h); window.__gapStop = null; };
  return true;
})()
"""

JS_READ = "JSON.stringify(window.__gap || {})"
JS_SEEN = "!!(window.__gap && window.__gap.seen)"


def log(line: str) -> None:
    print(line, flush=True)


def await_model_pill(s: DriverSession, timeout_s: int = 60) -> str:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = (s.model_pill() or "").strip()
        if last and last.lower() != "model":
            return last
        time.sleep(0.5)
    raise AssertionError(f"model pill never resolved (last={last!r})")


def main() -> int:
    with DriverSession(name="run-timeline-gap") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, load_env_key(PROVIDER))
        log(f"  model = {await_model_pill(s)!r}")

        # Land on a live cockpit first: the bug needs an EXISTING run bound so
        # the next send is a run-to-run transition, not idle→first-run.
        s.send_first_run_message(P_FIRST)
        assert s.wait_for("[data-testid=thread-canvas]", 90), "no cockpit"
        time.sleep(10)
        s.shot("before-send-strip-present")

        log("→ arming watcher, then sending (the gap opens on send)")
        s.evaluate(JS_WATCH)
        s.fill("[data-testid=composer-textarea]", P_SECOND)
        time.sleep(0.3)
        s.click('button[aria-label="Send message"]')

        # Race the gap: poll the latch as fast as the RPC allows and shoot the
        # moment it trips.
        caught = False
        deadline = time.time() + 20
        while time.time() < deadline:
            if s.evaluate(JS_SEEN):
                s.shot("GAP-strip-missing")
                caught = True
                break
            time.sleep(0.02)

        # An unconditional mid-send frame so the fixed build produces a shot at
        # the SAME moment the pre-fix build shows the gap — otherwise "after"
        # has no comparable image, only an absence.
        if not caught:
            s.shot("midsend-strip-present")

        time.sleep(8)
        gap = json.loads(s.evaluate(JS_READ) or "{}")
        s.evaluate("window.__gapStop && window.__gapStop()")
        s.shot("after-run-settled")

        if gap.get("seen"):
            start, end = gap.get("startedAt"), gap.get("endedAt")
            dur = f"{end - start:.0f}ms" if start and end else "still missing"
            log(f"\n=== GAP OBSERVED === {gap['frames']} frames, duration {dur}")
            log(f"screenshot caught in the act: {caught}")
            log(f"screenshots → {s.run_dir}")
            return 0
        log("\n=== NO GAP === the strip stayed mounted across the send")
        log(f"screenshots → {s.run_dir}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
