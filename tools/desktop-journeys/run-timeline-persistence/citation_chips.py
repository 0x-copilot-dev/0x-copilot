#!/usr/bin/env python3
"""Citation chips render, don't leak the raw token, and follow on click.

Drives the REAL supervised desktop app and asserts the four faults behind the
reported `[[8]]` + "Open external link?" symptom are gone:

  1. a chip element exists at all (desktop had no chip renderer),
  2. no `[[N]]` token survives in the transcript,
  3. no Streamdown untrusted-link popover copy is present,
  4. clicking a chip reveals the Sources rail.

Needs a run that actually cites something, so it asks for a web search. If the
model answers without citing, the citation assertions report BLOCKED rather than
PASS — a run with nothing to cite proves nothing either way.

    python3 tools/desktop-journeys/run-timeline-persistence/citation_chips.py
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("JOURNEY_PROVIDER", "anthropic")
P_CITE = "Search the web for what LangGraph is and summarise it in two sentences with sources."

CHIP = "[data-testid=tc-chat] .citation-chip"

# Streamdown's untrusted-link popover copy (from its dist bundle). Any of these
# appearing over a citation means the chip renderer is not wired.
POPOVER_STRINGS = [
    "Open external link?",
    "You're about to visit an external website",
]

JS_CHIPS = """
(() => {
  const chips = [...document.querySelectorAll('[data-testid=tc-chat] .citation-chip')];
  const chat = document.querySelector('[data-testid=tc-chat]');
  const text = chat ? chat.innerText : '';
  return JSON.stringify({
    count: chips.length,
    labels: chips.map(c => c.textContent),
    hrefs: chips.map(c => c.getAttribute('href')),
    targets: chips.map(c => c.getAttribute('target')),
    fontSizes: chips.map(c => getComputedStyle(c).fontSize),
    tops: chips.map(c => getComputedStyle(c).top),
    proseFontSize: chat ? getComputedStyle(chat).fontSize : null,
    rawToken: /\\[\\[\\d+\\]\\]/.test(text),
    text: text.slice(0, 400),
  });
})()
"""

JS_ACTIVE_TAB = (
    "(document.querySelector('[data-testid=run-workspace-rail]')||{})"
    ".getAttribute && document.querySelector("
    "'[data-testid=run-workspace-rail]').getAttribute('data-active-tab')"
)


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
    failures: list[str] = []
    blocked: list[str] = []

    with DriverSession(name="citation-chips") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, load_env_key(PROVIDER))
        log(f"  model = {await_model_pill(s)!r}")

        log("→ sending a prompt that should cite web sources")
        s.send_first_run_message(P_CITE)
        assert s.wait_for("[data-testid=thread-canvas]", 120), "no cockpit"

        # Give the search + answer time to stream and the citations to bind.
        for _ in range(60):
            time.sleep(1)
            probe = json.loads(s.evaluate(JS_CHIPS) or "{}")
            if probe.get("count", 0) > 0:
                break
        probe = json.loads(s.evaluate(JS_CHIPS) or "{}")
        s.shot("transcript-with-citations")

        log(f"  chips={probe.get('count')} labels={probe.get('labels')}")
        log(f"  hrefs={probe.get('hrefs')}")
        log(f"  chip font={probe.get('fontSizes')} prose={probe.get('proseFontSize')}")
        log(f"  superscript offset top={probe.get('tops')}")

        # (2) The reported symptom, regardless of whether any chip rendered.
        if probe.get("rawToken"):
            failures.append(f"raw [[N]] token in transcript: {probe.get('text')!r}")

        # (3) No Streamdown popover copy anywhere on the page.
        body = s.evaluate("document.body.innerText") or ""
        for needle in POPOVER_STRINGS:
            if needle in body:
                failures.append(f"Streamdown link popover present: {needle!r}")

        if probe.get("count", 0) == 0:
            blocked.append("model answered without citing — chip render/click unproven")
        else:
            # (1) shape: smaller than prose and lifted above the baseline.
            chip_px = float(probe["fontSizes"][0].removesuffix("px"))
            prose_px = float((probe["proseFontSize"] or "13px").removesuffix("px"))
            if chip_px >= prose_px:
                failures.append(
                    f"chip font {chip_px}px not smaller than prose {prose_px}px"
                )
            top = probe["tops"][0]
            if not top.startswith("-"):
                failures.append(f"chip not lifted above baseline (top={top})")
            # A citation href must stay an in-page fragment, never a new tab.
            for href, target in zip(probe["hrefs"], probe["targets"]):
                if href is not None and not href.startswith("#"):
                    failures.append(f"chip href is not in-page: {href!r}")
                if target is not None:
                    failures.append(f"chip opens a new tab (target={target!r})")

            # (4) Click follows through to Sources.
            before = s.evaluate(JS_ACTIVE_TAB)
            s.click(f"{CHIP}")
            time.sleep(1.5)
            after = s.evaluate(JS_ACTIVE_TAB)
            log(f"  rail tab: {before!r} → {after!r}")
            s.shot("after-chip-click-sources")
            if after != "sources":
                failures.append(f"chip click did not reveal Sources (tab={after!r})")

        log(f"\nscreenshots → {s.run_dir}")

    for b in blocked:
        log(f"  ~ BLOCKED: {b}")
    if failures:
        log("\n=== FAIL ===")
        for f in failures:
            log(f"  ✗ {f}")
        return 1
    log("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
