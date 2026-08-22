#!/usr/bin/env python3
"""Capture the three product stills the READMEs have been waiting on.

`apps/website/README.md` names the set: **before** (a goal in the composer),
**during** (live tool events with work taking shape — it calls this "the
product's proof point"), and **after** (the finished result beside its
Activity). `public/media/app-run.png` is the placeholder standing in for all
three, and it is an empty composer, so it shows none of them.

This is a capture, not an assertion — it drives the real packaged app the way
every other journey here does, but its output is images rather than a verdict.
It still fails loudly if a shot would be a lie: if the run never streams, or
never finishes, there is nothing worth photographing and the script says so
rather than writing a blank frame.

Two framing decisions, both deliberate:

* **The window is 2400x1600.** Same 3:2 as the existing capture, so the images
  drop into the README and the showcase hero without a re-crop.
* **The model pill is read and reported, never cropped silently.** External
  copy here does not name a model, and the composer renders one, so the caller
  is told what is in frame and decides. Hiding it inside the capture would make
  the constraint invisible the next time someone runs this.

Usage:

    /opt/homebrew/bin/python3.13 tools/desktop-journeys/_marketing_capture.py

`python3` on this box is a conda 3.10 and dies on ``StrEnum`` before running a
line of product code — use the interpreter above.
"""

from __future__ import annotations

import os
import time

from _lib import DriverSession, load_env_key

#: Anthropic direct. The Virtuals gateway resolves its catalog fine and then
#: fails the completion with "Service unavailable", which photographs as an
#: error card — see the first run of this script.
PROVIDER = os.environ.get("JOURNEY_PROVIDER", "anthropic")

#: A task with a real shape: it plans, works through the plan, and leaves
#: something behind. The prime-number fixture in the old screenshots reads as a
#: toy, which is the other half of why those frames could not be used.
GOAL = (
    "Draft a one-page pre-launch checklist for shipping a desktop app: "
    "code signing, auto-update, crash reporting, and a rollback plan. "
    "Group it by phase, keep each item to one line, and save it as a document "
    "I can keep."
)

#: How long to wait for the artifact to reach the canvas before giving up on a
#: good "during" frame. This is a CEILING, not a dwell — the shot fires the
#: moment the canvas has something on it.
#:
#: A fixed dwell was the first attempt and it is the wrong instrument: at 22s
#: one run had the document half-drawn and the next was still on "Nothing to
#: review yet". In a window that empty state is a strip; in fullscreen it is
#: two thirds of the frame. Wait for the ink, not the clock.
DURING_CEILING_S = 150

#: Content size in CSS px; the capture is 2x DPI, so this ships at 2x these
#: numbers. Deliberately WIDER than this display (1512 logical): `setContentSize`
#: accepts a window larger than the screen and the screenshot is of the
#: renderer, not of what is physically visible — so the capture is not capped by
#: the panel it runs on.
#:
#: Width is the whole point. With the canvas open the composer sits in a rail
#: roughly a third of the window, and at fullscreen-on-this-machine that rail
#: was narrow enough that the bottom control row WRAPPED: [+ tools Manual] on
#: one line and [context, model, mic, send] on the next. `_control_row_rows`
#: below fails the capture if that comes back.
WIDTH = int(os.environ.get("CAPTURE_WIDTH", "1900"))
HEIGHT = int(os.environ.get("CAPTURE_HEIGHT", "1150"))

#: The run has to actually finish for the "after" frame to show a result.
COMPLETION_TIMEOUT_S = 420


#: Real macOS fullscreen, driven in the MAIN process. A Playwright viewport
#: override would only fake the renderer's size and would still photograph the
#: window's rounded corners and titlebar strip; `setFullScreen` is what a user
#: pressing the green button gets. The transition is animated, so the caller
#: has to wait for it rather than measuring immediately.
_FULLSCREEN_JS = """
({BrowserWindow}, arg) => {
  const win = BrowserWindow.getAllWindows().find(w => !w.isDestroyed());
  if (!win) throw new Error('no live BrowserWindow');
  win.setFullScreen(true);
  return {requested: true};
}
"""

_FULLSCREEN_STATE_JS = """
({BrowserWindow}, arg) => {
  const win = BrowserWindow.getAllWindows().find(w => !w.isDestroyed());
  if (!win) return null;
  const [w, h] = win.getContentSize();
  return {fullScreen: win.isFullScreen(), width: w, height: h};
}
"""


def _go_fullscreen(s: DriverSession, timeout_s: int = 20) -> dict:
    """Put the real window into macOS fullscreen and wait for it to settle."""
    s.rpc("mainEval", js=_FULLSCREEN_JS)
    deadline = time.time() + timeout_s
    state: dict = {}
    while time.time() < deadline:
        time.sleep(1)
        state = s.rpc("mainEval", js=_FULLSCREEN_STATE_JS).get("value") or {}
        if state.get("fullScreen"):
            break
    else:
        raise AssertionError(f"window never entered fullscreen within {timeout_s}s")
    # The animation finishes after isFullScreen() flips; let layout settle so
    # the first shot is not caught mid-transition.
    time.sleep(3)
    print(f"fullscreen: {state}")
    return state


#: Does the composer's bottom control row sit on ONE line?
#:
#: Measured, not eyeballed: the model pill and the execution-mode pill are both
#: in that row by the layout contract, so if their box tops differ the row has
#: wrapped. Reading the two tops is the same evidence a person gets from the
#: screenshot, minus the squinting.
#: Measure the CONTROLS THEMSELVES, not a container.
#:
#: The first version of this read `modelPill.parentElement.children` and
#: reported one row at a box width of 137px — it had walked up to a wrapper
#: holding only the pill, so a single child trivially meant "one row". It
#: returned a green on the exact frame a human could see was wrapped. Anchor on
#: the leftmost and rightmost controls of the row instead: if the send button
#: does not share a baseline with the mode pill, the row has broken.
_CONTROL_ROW_JS = """
(() => {
  const model = document.querySelector('.atlas-model-pill');
  const send  = document.querySelector('button[aria-label="Send message"]')
             || document.querySelector('button[aria-label="Stop response"]');
  const mode  = [...document.querySelectorAll('button, [role=button]')]
                  .find(el => /^(Manual|Auto|Bypass)$/i.test((el.textContent||'').trim()));
  const parts = {model, send, mode};
  const seen = {};
  const tops = [];
  for (const [name, el] of Object.entries(parts)) {
    if (!el) { seen[name] = null; continue; }
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) { seen[name] = null; continue; }
    // Compare CENTRES: the controls differ in height, so equal tops is too
    // strict a test for "same row" and would false-positive on a fine layout.
    const c = Math.round(r.top + r.height / 2);
    seen[name] = c;
    tops.push(c);
  }
  if (tops.length < 2) return {rows: 0, measured: seen, note: 'not enough controls found'};
  const spread = Math.max(...tops) - Math.min(...tops);
  return {
    // A wrapped row puts a full control height (~28px) between the clusters.
    rows: spread > 12 ? 2 : 1,
    spread,
    measured: seen,
  };
})()
"""


def _control_row_rows(s: DriverSession) -> dict:
    return s.evaluate(_CONTROL_ROW_JS) or {}


def _composer_idle(s: DriverSession) -> bool:
    return bool(
        s.evaluate(
            "!!document.querySelector('button[aria-label=\"Send message\"]') && "
            "!document.querySelector('button[aria-label=\"Stop response\"]')"
        )
    )


def _activity(s: DriverSession) -> dict:
    """What is actually on screen right now — used to prove a frame is worth keeping."""
    return (
        s.evaluate(
            """
            (() => {
              const q = sel => document.querySelectorAll(sel).length;
              return {
                messages:  q('[data-testid^=tc-chat-message-]'),
                toolCards: q('[data-testid^=tool-]') + q('[class*=tool-card]'),
                streaming: !!document.querySelector('button[aria-label="Stop response"]'),
                todos:     (document.body.innerText||'').includes('TODOS'),
                // Is there actually something drawn on the canvas? The
                // "during" frame is worthless without it — that half of the
                // screen is otherwise the "Nothing to review yet" empty state.
                canvas:    (() => {
                  const el = document.querySelector('[data-testid=artifact-document-renderer]')
                          || document.querySelector('[data-testid=artifact-frame]');
                  if (!el) return 0;
                  const r = el.getBoundingClientRect();
                  return Math.round(r.width * r.height);
                })(),
                // A failed run still renders a card, and a card photographs
                // exactly as well as a result does. The first run of this
                // script shot "Service unavailable" three times.
                failed:    /Service unavailable|We couldn't complete this run|Start a new run with this goal/
                             .test(document.body.innerText||''),
                modelPill: (document.querySelector('.atlas-model-pill')||{}).innerText || null,
              };
            })()
            """
        )
        or {}
    )


def main() -> int:
    key = load_env_key(PROVIDER)
    print(f"provider={PROVIDER} key_len={len(key)} (withheld)")

    with DriverSession(name="marketing-capture") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        applied = s.resize(WIDTH, HEIGHT)
        print(f"window: {applied}")
        time.sleep(2)

        # ── before ──────────────────────────────────────────────────────────
        # Fill without sending: this frame is the goal composed, not answered.
        assert s.wait_visible("[data-testid=composer-textarea]", 30), (
            "composer never appeared — nothing to capture"
        )
        s.fill("[data-testid=composer-textarea]", GOAL)
        time.sleep(1.5)
        s.shot("before-goal-composed")
        print("shot 1/3: before")

        # ── during ──────────────────────────────────────────────────────────
        s.click('button[aria-label="Send message"]')

        # Fire the moment the canvas has ink. Keep watching briefly after it
        # appears so the document is drawn rather than one heading tall.
        deadline = time.time() + DURING_CEILING_S
        mid: dict = {}
        while time.time() < deadline:
            time.sleep(2)
            mid = _activity(s)
            if mid.get("failed"):
                raise AssertionError(
                    "the run failed — the frame shows an error card, not the "
                    "product. Check the provider key and the logs under runs/."
                )
            if mid.get("canvas"):
                time.sleep(6)
                mid = _activity(s)
                break
            if not mid.get("streaming") and mid.get("messages", 0) >= 2:
                print("WARNING: run ended before anything reached the canvas")
                break

        print(f"mid-run state: {mid}")
        if not mid.get("messages"):
            raise AssertionError(
                "the run produced no messages — the 'during' frame would be empty"
            )
        if not mid.get("canvas"):
            print(
                "WARNING: canvas is empty — this frame shows the 'Nothing to "
                "review yet' state, which is not worth publishing"
            )
        s.shot("during-run-live")
        print("shot 2/3: during")

        # ── after ───────────────────────────────────────────────────────────
        deadline = time.time() + COMPLETION_TIMEOUT_S
        while time.time() < deadline:
            if _composer_idle(s):
                break
            time.sleep(2)
        else:
            raise AssertionError(
                f"run never completed within {COMPLETION_TIMEOUT_S}s — no 'after' frame"
            )

        time.sleep(3)
        end = _activity(s)
        print(f"end state: {end}")
        if end.get("failed"):
            raise AssertionError(
                "the run ended in an error card — nothing here is publishable"
            )

        row = _control_row_rows(s)
        print(f"composer control row: {row}")
        if row.get("rows", 1) > 1:
            raise AssertionError(
                f"the composer's bottom controls wrapped onto {row['rows']} rows "
                f"at width={WIDTH} (row box {row.get('rowWidth')}px, tops "
                f"{row.get('tops')}). Raise CAPTURE_WIDTH and re-run."
            )

        # Prove the check above can FAIL before trusting that it passed. Squeeze
        # the window until the row must break, confirm the detector says so, and
        # restore. Without this the green is indistinguishable from a detector
        # that is looking at the wrong element — which is precisely what the
        # first version of it did.
        s.resize(1200, HEIGHT)
        time.sleep(2)
        narrow = _control_row_rows(s)
        s.resize(WIDTH, HEIGHT)
        time.sleep(2)
        restored = _control_row_rows(s)
        print(f"self-check: narrow={narrow} restored={restored}")
        if narrow.get("rows") != 2:
            raise AssertionError(
                "the wrap detector did not trip at width=1200, so its pass at "
                f"width={WIDTH} proves nothing. It is measuring the wrong "
                f"elements — got {narrow}."
            )
        if restored.get("rows") != 1:
            raise AssertionError(
                f"the row did not recover at width={WIDTH}: {restored}"
            )

        s.shot("after-result")
        print("shot 3/3: after")

        pill = end.get("modelPill") or mid.get("modelPill")
        print(f"\nMODEL PILL IN FRAME: {pill!r}")
        print(
            "  External copy names no model. If these ship as-is, crop the "
            "composer's right edge or accept the pill deliberately."
        )
        print(f"\nscreenshots: {s.run_dir / 'screenshots'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
