#!/usr/bin/env python3
"""shell-overflow — the app shell must never make the DOCUMENT scrollable.

The desktop window is a fixed frame: `.desktop-window-frame` is `height: 100%`
with `overflow: hidden`, and every scroll region lives INSIDE it (the settings
content pane, the nav, the transcript). The root element must therefore never
gain scrollable overflow. When it does, the whole shell — rail, nav, content —
can be lifted out of the window by a wheel event, trackpad overscroll, or a
focus move, leaving dead background below and the rail/nav tops clipped off.

That shipped once (Settings → Model & behavior): the visually-hidden `input`
inside `.ui-switch` was `position: absolute` while `.ui-switch` was `static`, so
its containing block resolved to the INITIAL containing block. An abs-positioned
box whose containing block sits outside a clipper is not clipped by that
clipper's `overflow: hidden`, so the input escaped the window frame and reported
its static position in DOCUMENT coordinates as root overflow — measured live at
scrollHeight 1239 against clientHeight 800, dragging the shell top from +32 to
-407. Unit tests could not see it: jsdom does not lay out, so the escape only
exists in a real engine.

This journey asserts BOTH halves, on every settings section and every rail
destination:

  1. OUTCOME — `documentElement.scrollHeight <= clientHeight`.
  2. ROOT CAUSE — no laid-out, absolutely-positioned element resolves its
     containing block to the initial containing block (`offsetParent === body`).

(2) is the stronger check and is window-height independent: an escaped element
whose static position happens to fall ABOVE the fold inflates nothing today, but
becomes (1) the moment the window shrinks or the page grows. Deliberate
`position: fixed` overlays (the toast stack) report `offsetParent === null` and
are correctly ignored.

Run (from repo root, with the stack staged/built — see ../README.md):

    python3 tools/desktop-journeys/shell-overflow/shell_document_overflow.py

Exits non-zero on any violation.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("SHELL_OVERFLOW_PROVIDER", "anthropic")

# Rail destinations to sweep (solo profile labels, per destinationsForProfile).
DESTINATIONS = ["Run", "Chats", "Projects", "Activity", "Tools", "Skills"]

# ── JS probes ────────────────────────────────────────────────────────────────

# Root-element overflow, in px. 0 is the only healthy value.
JS_DOC_OVERFLOW = (
    "(()=>{const d=document.documentElement;return d.scrollHeight-d.clientHeight})()"
)

# Every laid-out abs-positioned element whose containing block escaped to the
# ICB. `offsetParent === body` is exactly that condition in Blink. Elements in a
# `display:none` subtree report `offsetParent === null` and are skipped, as are
# `position: fixed` overlays (also null).
JS_ESCAPED = """
(()=>{const out=[];
document.querySelectorAll("*").forEach(e=>{
  if(getComputedStyle(e).position!=="absolute")return;
  if(e.offsetParent!==document.body)return;
  const r=e.getBoundingClientRect();
  if(r.width===0&&r.height===0)return;
  const cls=(e.className||"").toString().split(" ").filter(Boolean).join(".");
  const pcls=(e.parentElement&&e.parentElement.className||"").toString()
    .split(" ").filter(Boolean).join(".");
  out.push({
    el:e.tagName.toLowerCase()+(cls?"."+cls:""),
    testid:e.getAttribute("data-testid")||null,
    parent:(e.parentElement?e.parentElement.tagName.toLowerCase():"?")
      +(pcls?"."+pcls:""),
    docTop:Math.round(r.top+document.documentElement.scrollTop),
  })});
return out})()
"""

JS_SETTINGS_SLUGS = (
    "Array.from(document.querySelectorAll('[role=tab][data-slug]'))"
    ".map(t=>t.getAttribute('data-slug'))"
)


def log(line: str) -> None:
    print(line, flush=True)


failures: list[str] = []


def check(s: DriverSession, where: str) -> None:
    """Assert both halves of the invariant at the current surface."""
    overflow = s.evaluate(JS_DOC_OVERFLOW)
    escaped = s.evaluate(JS_ESCAPED) or []

    if overflow and int(overflow) > 0:
        failures.append(
            f"{where}: document root scrolls by {overflow}px "
            "(the shell can be lifted out of the window)"
        )
        log(f"FAIL  {where}: root overflow {overflow}px")
    else:
        log(f"PASS  {where}: root overflow 0px")

    if escaped:
        for e in escaped:
            tid = f" data-testid={e['testid']}" if e.get("testid") else ""
            failures.append(
                f"{where}: {e['el']}{tid} (in {e['parent']}) escaped its clipper "
                f"— containing block is the ICB, laid out at document y={e['docTop']}. "
                "Give its positioned wrapper `position: relative`."
            )
            log(f"FAIL  {where}: escaped to ICB → {e['el']}{tid} in {e['parent']}")
    else:
        log(f"PASS  {where}: no element escaped to the initial containing block")


def reach_shell(s: DriverSession, timeout_s: int = 300) -> None:
    """Walk whatever gates this userData dir lands on until the shell renders.

    `DriverSession.start()` returns as soon as the CONTROL SERVER answers, which
    is well before the supervised stack has booted and the renderer has painted a
    gate. So this polls for whichever gate is showing rather than assuming an
    order: boot progress → sign-in → FTUE → shell. A reused (already signed-in)
    userData dir lands straight on the shell and skips both gates.
    """
    deadline = time.time() + timeout_s
    signed_in = False
    keyed = False
    skipped = False
    while time.time() < deadline:
        if s.present('button[aria-label="Settings"]'):
            log("PASS  reached the app shell")
            return
        if not keyed and s.present("[data-testid=first-run-add-key]"):
            s.ftue_add_key(PROVIDER, load_env_key(PROVIDER))  # never printed
            keyed = True
            continue
        # A runtime that already holds a key lands on the FTUE composer with no
        # add-key step; "skip — open the workspace" is the only way through.
        if not skipped and s.present("[data-testid=first-run-skip]"):
            s.click("[data-testid=first-run-skip]")
            skipped = True
            time.sleep(2)
            continue
        if not signed_in and s.present("[data-testid=sign-in-button]"):
            s.sign_in_local()
            signed_in = True
            time.sleep(2)
            continue
        time.sleep(2)
    s.shot("fail-never-reached-shell")
    raise AssertionError(
        f"never reached the app shell within {timeout_s}s "
        "(rail-foot Settings button absent)"
    )


def sweep_settings(s: DriverSession) -> None:
    s.click('button[aria-label="Settings"]')
    assert s.wait_for("[data-testid=settings-surface]"), "Settings never mounted"
    # Advanced is collapsed by default; expand it so its sections are reachable.
    if s.present("[data-testid=settings-group-toggle-advanced]"):
        s.click("[data-testid=settings-group-toggle-advanced]")
        time.sleep(0.4)

    slugs = s.evaluate(JS_SETTINGS_SLUGS) or []
    assert slugs, "no settings tabs found"
    log(f"── settings sections ({len(slugs)}) ─────────────────────────")
    for slug in slugs:
        s.click(f"[role=tab][data-slug={slug}]")
        time.sleep(0.5)
        check(s, f"settings/{slug}")
        if failures:
            s.shot(f"fail-settings-{slug}")


def sweep_destinations(s: DriverSession) -> None:
    log("── rail destinations ────────────────────────────────────────")
    for label in DESTINATIONS:
        if not s.present(f'[aria-label="{label}"][data-destination]'):
            log(f"SKIP  destination/{label}: not in this profile's rail")
            continue
        s.open_destination(label)
        check(s, f"destination/{label}")


def main() -> int:
    with DriverSession(name="shell-overflow") as s:
        reach_shell(s)
        s.shot("00-shell")
        sweep_settings(s)
        sweep_destinations(s)

    if failures:
        log("")
        log(f"── {len(failures)} VIOLATION(S) ─────────────────────────────")
        for f in failures:
            log(f"  • {f}")
        return 1
    log("")
    log("ALL HARD ASSERTIONS PASSED — the shell never scrolls the document")
    return 0


if __name__ == "__main__":
    sys.exit(main())
