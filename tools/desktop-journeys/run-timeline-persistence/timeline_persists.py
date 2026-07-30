#!/usr/bin/env python3
"""run-timeline-persistence — the mini-timeline must survive a send.

Drives the REAL supervised desktop app and proves two coupled Run-cockpit fixes:

  BUG 1  The mini-timeline strip (beads + Live pill) VANISHED on send. In Studio
         with a run bound, its gate reduced to `!timelineEmpty`; sending starts a
         NEW run, the projection resets to zero beads, and the whole strip
         unmounted until the first event landed.

  BUG 2  The swimlanes band rendered a "Listening for run events…" status line
         before its first bead — a full band of vertical space restating what the
         mini-timeline already says.

A screenshot alone cannot prove BUG 1: the gap is transient, so a lucky capture
proves nothing. This journey installs a 50ms DOM sampler BEFORE each send and
reads the samples back afterwards, so an absence of even ONE frame is a hard
failure. Screenshots are the human-readable companion, not the evidence.

Run (from the checkout that owns the build under test):

    python3 tools/desktop-journeys/run-timeline-persistence/timeline_persists.py

Env:
    JOURNEY_PROVIDER   provider key to use from .env  (default: anthropic)
    COPILOT_JOURNEY_DOTENV  path to the .env holding the key (worktrees)

Exits non-zero if the strip ever disappears or the removed line ever appears.
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

MINI = "[data-testid=tc-mini-timeline-slot]"
PILL = "[data-testid=tc-mini-timeline-now]"
SWIM_EMPTY = "[data-testid=tc-swimlanes-empty]"
CANVAS = "[data-testid=thread-canvas]"

# ── the sampler ──────────────────────────────────────────────────────────────
# Installed before a send; ticks every 50ms recording what the user would see.
# `listening` scans visible text so it catches the string even if the old
# testId were renamed — we are asserting the ABSENCE of a user-visible line.
JS_INSTALL_SAMPLER = """
(() => {
  if (window.__tlStop) { window.__tlStop(); }
  window.__tlSamples = [];
  const tick = () => {
    const canvas = document.querySelector('[data-testid=thread-canvas]');
    window.__tlSamples.push({
      mini: !!document.querySelector('[data-testid=tc-mini-timeline-slot]'),
      pill: !!document.querySelector('[data-testid=tc-mini-timeline-now]'),
      swimEmpty: !!document.querySelector('[data-testid=tc-swimlanes-empty]'),
      listening: (canvas ? canvas.innerText : '').includes('Listening for run events'),
      mode: canvas ? canvas.getAttribute('data-mode') : null,
      beads: document.querySelectorAll('[data-testid^=tc-mini-timeline-bead-]').length,
    });
  };
  tick();
  const h = setInterval(tick, 50);
  window.__tlStop = () => { clearInterval(h); window.__tlStop = null; };
  return true;
})()
"""

JS_READ_SAMPLES = "(() => { if (window.__tlStop) window.__tlStop(); return JSON.stringify(window.__tlSamples || []); })()"


def log(line: str) -> None:
    print(line, flush=True)


def analyse(samples: list[dict], phase: str) -> list[str]:
    """Return a list of failure strings; empty means the phase passed."""
    failures: list[str] = []
    if not samples:
        return [f"{phase}: sampler collected ZERO samples (probe never ran)"]

    # Only judge frames where the cockpit canvas is actually mounted — a sample
    # taken mid-navigation has no canvas and no opinion about the strip.
    on_canvas = [s for s in samples if s["mode"] is not None]
    if not on_canvas:
        return [f"{phase}: never saw the thread canvas across {len(samples)} samples"]

    missing_mini = [i for i, s in enumerate(on_canvas) if not s["mini"]]
    missing_pill = [i for i, s in enumerate(on_canvas) if not s["pill"]]
    saw_listening = [i for i, s in enumerate(on_canvas) if s["listening"]]
    saw_swim_empty = [i for i, s in enumerate(on_canvas) if s["swimEmpty"]]

    zero_bead_frames = sum(1 for s in on_canvas if s["beads"] == 0)

    log(
        f"  {phase}: {len(on_canvas)} canvas frames "
        f"({zero_bead_frames} with zero beads), "
        f"modes={sorted({s['mode'] for s in on_canvas})}"
    )

    if missing_mini:
        failures.append(
            f"{phase}: BUG 1 — mini-timeline absent in {len(missing_mini)}/"
            f"{len(on_canvas)} frames (first at sample #{missing_mini[0]})"
        )
    if missing_pill:
        failures.append(
            f"{phase}: BUG 1 — Live pill absent in {len(missing_pill)}/"
            f"{len(on_canvas)} frames (first at sample #{missing_pill[0]})"
        )
    if saw_listening:
        failures.append(
            f"{phase}: BUG 2 — 'Listening for run events…' visible in "
            f"{len(saw_listening)}/{len(on_canvas)} frames"
        )
    if saw_swim_empty:
        failures.append(
            f"{phase}: BUG 2 — tc-swimlanes-empty rendered in "
            f"{len(saw_swim_empty)}/{len(on_canvas)} frames"
        )
    # A phase that never observed a zero-bead frame did not exercise the bug at
    # all — report it so a vacuously-green run cannot be mistaken for proof.
    if zero_bead_frames == 0:
        failures.append(
            f"{phase}: INCONCLUSIVE — no zero-bead frame sampled, so the "
            f"vanishing condition was never reached"
        )
    return failures


def switch_mode(s: DriverSession, mode: str) -> None:
    s.click(f"[data-testid=run-mode-{mode}]")
    time.sleep(1.0)


def await_model_pill(s: DriverSession, timeout_s: int = 60) -> str:
    """Block until the composer's model pill names a real model.

    The catalog resolves asynchronously after the key is stored; sending while
    the pill still reads a bare "Model" produces a run that never starts, which
    looks exactly like a cockpit-mount failure. Wait for the real name instead.
    """
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = (s.model_pill() or "").strip()
        if last and last.lower() not in {"model", "model "}:
            return last
        time.sleep(0.5)
    raise AssertionError(f"model pill never resolved (last={last!r})")


def main() -> int:
    failures: list[str] = []

    with DriverSession(name="run-timeline-persistence") as s:
        log("→ sign in (local device account)")
        s.sign_in_local()

        log(f"→ FTUE: add {PROVIDER} key")
        s.ftue_add_key(PROVIDER, load_env_key(PROVIDER))
        pill = await_model_pill(s)
        log(f"  model pill resolved = {pill!r}")
        s.shot("ftue-composer")

        # ── PHASE 1: the FIRST send (idle cockpit → live run) ────────────────
        log("→ PHASE 1: first send, sampling from before the click")
        s.evaluate(JS_INSTALL_SAMPLER)
        s.send_first_run_message(P_FIRST)
        if not s.wait_for(CANVAS, 90):
            s.shot("FAIL-no-cockpit")
            body = s.evaluate("document.body.innerText.slice(0,1200)") or ""
            log(f"  visible text at failure:\n{body}")
            raise AssertionError("cockpit never mounted after first send")
        # Sample across the whole zero-bead window and into the streaming tail.
        time.sleep(12)
        p1 = json.loads(s.evaluate(JS_READ_SAMPLES) or "[]")
        failures += analyse(p1, "phase1-first-send")
        s.shot("phase1-after-first-send")

        mode = s.run_mode()
        log(f"  cockpit mode = {mode}")

        # ── PHASE 2: Studio, where the bug lived ─────────────────────────────
        # BUG 1's gate was `mode === "studio" && !(showSwimlanes && empty)`, so
        # Studio is the mode that must be proven.
        if s.present("[data-testid=run-mode-studio]"):
            log("→ PHASE 2: switch to Studio and re-send (the exact repro)")
            switch_mode(s, "studio")
            assert s.run_mode() == "studio", "did not land in Studio"
            s.shot("phase2-studio-idle")

            s.evaluate(JS_INSTALL_SAMPLER)
            s.fill("[data-testid=composer-textarea]", P_SECOND)
            time.sleep(0.3)
            s.click('button[aria-label="Send message"]')
            time.sleep(12)
            p2 = json.loads(s.evaluate(JS_READ_SAMPLES) or "[]")
            failures += analyse(p2, "phase2-studio-resend")
            s.shot("phase2-studio-after-resend")
        else:
            log("  BLOCKED: no Studio control in this build (Focus-only flag)")

        # ── PHASE 3: Focus keeps the strip too ───────────────────────────────
        if s.present("[data-testid=run-mode-focus]"):
            log("→ PHASE 3: Focus mode retains the strip")
            switch_mode(s, "focus")
            if not s.present(MINI):
                failures.append("phase3-focus: mini-timeline absent in Focus")
            if not s.present(PILL):
                failures.append("phase3-focus: Live pill absent in Focus")
            s.shot("phase3-focus")

        # ── Final static assertions on the settled cockpit ───────────────────
        log("→ final: settled-cockpit assertions")
        if s.present(SWIM_EMPTY):
            failures.append("final: tc-swimlanes-empty still in the DOM")
        canvas_text = s.evaluate(
            "(document.querySelector('[data-testid=thread-canvas]')||{}).innerText||''"
        )
        if "Listening for run events" in (canvas_text or ""):
            failures.append("final: 'Listening for run events…' still rendered")
        s.shot("final-settled")

        log(f"\nscreenshots → {s.run_dir}")

    if failures:
        log("\n=== FAIL ===")
        for f in failures:
            log(f"  ✗ {f}")
        return 1
    log("\n=== PASS — strip never vanished; removed line never appeared ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
