#!/usr/bin/env python3
"""Journey — the desktop DOCUMENT never scrolls, and no surface is unreachable.

The desktop window is a fixed application frame: `.desktop-window-frame` is
`height: 100%; overflow: hidden` and every scroll region lives inside it. So two
invariants must hold together, and each one is only safe because of the other:

  A. The document itself must NEVER be scrollable. If it is, the reserved
     titlebar strip and the frame's border scroll away with it and the whole
     shell can be dragged out of the window. `desktop.css` declares
     `body { overflow: hidden }` (the web host declares the same in
     `apps/frontend/src/styles.css`), so an escaped absolutely-positioned
     descendant — one whose containing block resolved to the initial containing
     block instead of its own component, which the frame's `overflow: hidden`
     therefore does NOT clip — degrades to an invisible layout artefact instead
     of a scrollable, broken window.

  B. Because of A, every full-window surface must own its OWN internal scroll.
     Anything that sizes itself past the frame and leans on the document to
     scroll becomes permanently unreachable once A is enforced. This is the
     trap this journey exists to catch: `.loginx-shell` used to be
     `min-height: 100vh` with no internal overflow, so on a short window the
     sign-in card relied on document scrolling.

Both are asserted at a SHORT window height, where B actually bites — at the
1200x800 default the surfaces fit and every check passes trivially. The journey
runs the whole walk twice: once at 1200x600, then again at 1200x420 where the
sign-in card and the FTUE surface genuinely overflow.

Coverage: the two pre-shell surfaces (sign-in gate, FTUE), then every rail
destination and every Settings section, enumerated from the live DOM so a newly
added destination or settings section is covered automatically. Settings is the
important half — its `.ui-switch` toggles are where the escaping abspos element
was found.

    python3 tools/desktop-journeys/shell-overflow/shell_document_overflow.py

Needs no provider key and starts no run: it is pure layout truth. Exits non-zero
on any failed assertion.
"""

from __future__ import annotations

import os as _os
import sys as _sys
import time

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _lib import DriverSession  # noqa: E402

# The two short window sizes the walk is repeated at. 600 is the height a user
# gets by halving the default window; 420 is short enough that the sign-in card
# and the FTUE body must scroll internally, which is the case that regressed.
SIZES: list[tuple[int, int]] = [(1200, 600), (1200, 420)]

FRAME = "[data-testid=desktop-window-frame]"

failures: list[str] = []


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    failures.append(msg)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


# ── diagnostics — name the element that grew the document ───────────────────
CULPRITS_JS = """
(() => {
  const d = document.documentElement;
  const limitY = d.clientHeight, limitX = d.clientWidth;
  const out = [];
  for (const el of document.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const overY = Math.round(r.bottom - limitY);
    const overX = Math.round(r.right - limitX);
    if (overY <= 1 && overX <= 1) continue;
    // Only report the elements that CAUSE the overflow: an element inside a
    // clipping ancestor is already contained, so walk up looking for a clipper
    // whose containing-block chain actually holds it.
    const cs = getComputedStyle(el);
    let clipped = false;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const ps = getComputedStyle(p);
      const clips = ps.overflowX !== "visible" || ps.overflowY !== "visible";
      const positioned = ps.position !== "static";
      if (clips && (cs.position !== "absolute" || positioned)) { clipped = true; break; }
      if (cs.position === "fixed") break;
    }
    if (clipped) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || "").toString().slice(0, 60),
      testid: el.getAttribute("data-testid"),
      position: cs.position,
      overY, overX,
    });
  }
  // Deepest/worst offenders first, capped so the log stays readable.
  return out.sort((a, b) => b.overY - a.overY).slice(0, 6);
})()
"""


def culprits(s: DriverSession) -> list[str]:
    """Describe the elements whose boxes extend past the document's client box."""
    rows = s.evaluate(CULPRITS_JS) or []
    return [
        f"{r['tag']}.{r['cls'] or '(no class)'}"
        + (f" [{r['testid']}]" if r["testid"] else "")
        + f" position:{r['position']} overflowsY={r['overY']}px overflowsX={r['overX']}px"
        for r in rows
    ]


# ── invariant A — the document cannot scroll ────────────────────────────────
def assert_document_cannot_scroll(s: DriverSession, where: str) -> None:
    """The document must have ZERO scrollable overflow, in both axes.

    Note this is deliberately stricter than "the user cannot scroll the window".
    `overflow: hidden` clips and removes the scrollbar, but the box remains a
    scroll container: `scrollHeight` still reports the full scrollable overflow
    region, and a programmatic `scrollTop` write is still honoured. So the
    document not scrolling for a *user* does not prove nothing escaped — only
    `scrollHeight === clientHeight` does. Both axes are checked, because a
    vertical-only check passes while the document still scrolls horizontally.

    When it fails, `culprits()` names the offending elements, since the whole
    class of bug here is "which element escaped its clipping ancestor".
    """
    m = s.document_scroll()
    if m is None:
        fail(f"{where}: could not measure document scroll")
        return

    if m["scrollHeight"] != m["clientHeight"]:
        fail(
            f"{where}: document scrolls vertically — "
            f"scrollHeight={m['scrollHeight']} clientHeight={m['clientHeight']} "
            f"(overflow {m['scrollHeight'] - m['clientHeight']}px)"
        )
    if m["scrollWidth"] != m["clientWidth"]:
        fail(
            f"{where}: document scrolls horizontally — "
            f"scrollWidth={m['scrollWidth']} clientWidth={m['clientWidth']} "
            f"(overflow {m['scrollWidth'] - m['clientWidth']}px)"
        )

    if m["scrollHeight"] != m["clientHeight"] or m["scrollWidth"] != m["clientWidth"]:
        for c in culprits(s):
            print(f"      ↳ {c}")


# ── invariant B — a full-window surface is fully reachable inside itself ─────
REACHABILITY_JS = """
(() => {
  const surface = document.querySelector(%(sel)s);
  if (!surface) return { missing: true };
  const frame = document.querySelector(%(frame)s);
  if (!frame) return { noFrame: true };

  const cs = getComputedStyle(surface);
  const fr = frame.getBoundingClientRect();

  // 1. The surface box must sit INSIDE the frame's content box. A surface
  //    sized past the frame (e.g. `min-height: 100vh` against a frame that is
  //    a titlebar inset shorter) has its tail clipped by the frame's
  //    `overflow: hidden`, and no amount of internal scrolling reveals it.
  const sr = surface.getBoundingClientRect();
  const clippedBottom = Math.round(sr.bottom - fr.bottom);
  const clippedTop = Math.round(fr.top - sr.top);

  // 2. Scroll the surface to its extremes and measure whether its own content
  //    is reachable at each end. Scrollable overflow never extends above the
  //    start edge, so a centred child that overflows a row flex container is
  //    permanently cut off at the top — check both ends, not just the bottom.
  const children = Array.from(surface.children).filter(
    (c) => c.getBoundingClientRect().height > 0,
  );
  const visibleTop = Math.max(sr.top, fr.top);
  const visibleBottom = Math.min(sr.bottom, fr.bottom);

  surface.scrollTop = 0;
  const firstTop = children.length
    ? Math.round(children[0].getBoundingClientRect().top - visibleTop)
    : 0;

  surface.scrollTop = surface.scrollHeight;
  const lastBottom = children.length
    ? Math.round(
        children[children.length - 1].getBoundingClientRect().bottom -
          visibleBottom,
      )
    : 0;
  const scrolledTo = surface.scrollTop;
  surface.scrollTop = 0;

  return {
    overflowY: cs.overflowY,
    height: Math.round(sr.height),
    scrollHeight: surface.scrollHeight,
    clientHeight: surface.clientHeight,
    overflows: surface.scrollHeight > surface.clientHeight,
    scrolledTo,
    clippedTop,
    clippedBottom,
    firstTop,
    lastBottom,
  };
})()
"""


def assert_surface_self_scrolls(s: DriverSession, selector: str, where: str) -> None:
    """A full-window surface must be fully reachable by scrolling INSIDE itself."""
    before = len(failures)
    m = s.evaluate(REACHABILITY_JS % {"sel": f'"{selector}"', "frame": f'"{FRAME}"'})
    if m is None or m.get("missing") or m.get("noFrame"):
        fail(f"{where}: could not measure {selector} (result={m})")
        return

    if m["overflowY"] not in ("auto", "scroll"):
        fail(
            f"{where}: {selector} has overflow-y: {m['overflowY']} — it owns no "
            "internal scroll, so on a short window its content is unreachable"
        )

    # Sized past the frame → the tail is clipped away, unreachable by scrolling.
    if m["clippedBottom"] > 1:
        fail(
            f"{where}: {selector} extends {m['clippedBottom']}px past the frame's "
            f"bottom (height={m['height']}) — that tail is clipped, not scrollable"
        )
    if m["clippedTop"] > 1:
        fail(
            f"{where}: {selector} starts {m['clippedTop']}px above the frame's top "
            "— that head is clipped, not scrollable"
        )

    # Content must be reachable at BOTH ends of the surface's own scroll range.
    if m["firstTop"] < -1:
        fail(
            f"{where}: {selector} content starts {abs(m['firstTop'])}px above its "
            "own visible top at scrollTop=0 — permanently unreachable "
            "(centred overflow in a row flex container)"
        )
    if m["lastBottom"] > 1:
        fail(
            f"{where}: {selector} content still ends {m['lastBottom']}px below its "
            f"visible bottom when scrolled fully (scrolledTo={m['scrolledTo']}) — "
            "unreachable"
        )

    if len(failures) > before:
        return
    detail = (
        "overflows→scrolls internally" if m["overflows"] else "fits, no scroll needed"
    )
    ok(f"{where}: {selector} fully reachable ({detail}, height={m['height']})")


# ── walk ────────────────────────────────────────────────────────────────────
def apply_size(s: DriverSession, width: int, height: int) -> None:
    r = s.resize(width, height)
    vp = r.get("viewport") or {}
    got_h = vp.get("innerHeight")
    print(f"[size] requested {width}x{height} → viewport {vp}")
    # The invariants are only meaningfully tested if the short size really took.
    if got_h is None or abs(int(got_h) - height) > 24:
        fail(
            f"window did not accept height {height} (innerHeight={got_h}) — "
            "the short-window assertions below would be vacuous"
        )


def walk_destinations(s: DriverSession, label: str) -> None:
    slugs = (
        s.evaluate(
            'Array.from(document.querySelectorAll("button[data-destination]"))'
            '.map(b=>b.getAttribute("data-destination"))'
        )
        or []
    )
    if not slugs:
        fail(f"{label}: no rail destinations found — shell did not mount")
        return
    print(f"[destinations] {len(slugs)} found: {', '.join(slugs)}")
    for slug in slugs:
        s.click(f'button[data-destination="{slug}"]')
        time.sleep(1.2)
        assert_document_cannot_scroll(s, f"{label} destination:{slug}")


def walk_settings(s: DriverSession, label: str) -> None:
    s.click('[aria-label="Settings"]')
    if not s.wait_for("[data-testid=settings-surface]", 30):
        fail(f"{label}: settings surface never opened")
        return
    slugs = (
        s.evaluate(
            'Array.from(document.querySelectorAll("[role=tab][data-slug]"))'
            '.map(t=>t.getAttribute("data-slug"))'
        )
        or []
    )
    if not slugs:
        fail(f"{label}: no settings sections found")
        return
    print(f"[settings] {len(slugs)} sections: {', '.join(slugs)}")
    for slug in slugs:
        s.click(f'[role=tab][data-slug="{slug}"]')
        time.sleep(0.9)
        assert_document_cannot_scroll(s, f"{label} settings:{slug}")
    s.shot(f"settings-{label}")


def main() -> int:
    with DriverSession(name="shell-document-overflow") as s:
        # ── 1. sign-in gate, at each short size ─────────────────────────────
        assert s.wait_for("[data-testid=sign-in-gate]"), "sign-in gate never appeared"
        for width, height in SIZES:
            print(f"\n=== sign-in gate @ {width}x{height} ===")
            apply_size(s, width, height)
            time.sleep(0.4)
            assert_document_cannot_scroll(s, f"sign-in @{height}")
            assert_surface_self_scrolls(s, ".loginx-shell", f"sign-in @{height}")
            s.shot(f"signin-{height}")

        # ── 2. FTUE surface, at each short size ─────────────────────────────
        # Back to a normal size to click through, then shrink again: the gate is
        # driven at a size where its controls are certainly in view.
        apply_size(s, 1200, 800)
        time.sleep(0.3)
        s.sign_in_local()
        assert s.wait_for("[data-testid=first-run-surface],.fr", 90), (
            "FTUE surface never appeared after sign-in"
        )
        time.sleep(1.0)
        for width, height in SIZES:
            print(f"\n=== FTUE surface @ {width}x{height} ===")
            apply_size(s, width, height)
            time.sleep(0.4)
            assert_document_cannot_scroll(s, f"ftue @{height}")
            assert_surface_self_scrolls(s, ".fr", f"ftue @{height}")
            s.shot(f"ftue-{height}")

        # ── 3. the shell: every destination + every settings section ─────────
        apply_size(s, 1200, 800)
        time.sleep(0.3)
        assert s.wait_for("[data-testid=first-run-skip]", 30), "FTUE skip missing"
        s.click("[data-testid=first-run-skip]")
        assert s.wait_for("[data-component=chat-shell]", 60), (
            "shell never mounted after skipping FTUE"
        )
        time.sleep(1.5)
        for width, height in SIZES:
            print(f"\n=== shell @ {width}x{height} ===")
            apply_size(s, width, height)
            time.sleep(0.5)
            assert_document_cannot_scroll(s, f"shell @{height}")
            walk_destinations(s, f"shell @{height}")
            walk_settings(s, f"shell @{height}")

    print()
    if failures:
        print(f"FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — document never scrolled; every surface reachable inside itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
