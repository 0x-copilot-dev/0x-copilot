"""Screenshot + measure the finished todo panel pinned above the composer.

Not a journey — a proof for a specific UI change: a completed, folded todo
list used to render three stacked rows (header count, a bar at 100%, the
summary line) and Focus carried no gap between the transcript, that panel and
the composer. This drives a real run that produces exactly three todos, then
reports the panel's measured height and its real gap to the composer in BOTH
modes, with a screenshot each.
"""

from __future__ import annotations

import json
import time

from _lib import DriverSession, load_env_key

PROVIDER = "anthropic"
PROMPT = (
    "Use the write_todos tool to plan this work as exactly THREE todos, then "
    "carry them out one at a time, marking each todo completed before you "
    "start the next one: (1) state the definition of a prime number, "
    "(2) determine whether 97 is prime by trial division, (3) determine "
    "whether 91 is prime by trial division. Do not delegate to subagents. "
    "Give all three answers."
)

# The ink, not the intent: real boxes, real gap, counted rows.
JS_MEASURE = """
(() => {
  const panel = document.querySelector('[data-testid=tc-todo-list]');
  if (!panel) return {panel: false};
  const chat = document.querySelector('[data-testid=tc-chat]');
  // The composer SLOT is the box the panel is pinned above; compare against
  // that, not an inner wrapper a `closest()` happens to land on.
  const composer = document.querySelector('[data-testid=tc-chat-composer-slot]');
  const p = panel.getBoundingClientRect();
  const c = composer ? composer.getBoundingClientRect() : null;
  // Direct element children that actually paint (the <style> tag does not).
  const rows = [...panel.children].filter(n =>
    n.tagName !== 'STYLE' && n.getBoundingClientRect().height > 0).length;
  return {
    panel: true,
    complete: panel.getAttribute('data-complete'),
    collapsed: panel.getAttribute('data-collapsed'),
    panelHeight: Math.round(p.height),
    paintedRows: rows,
    hasProgressBar: !!panel.querySelector('[aria-hidden=true] > span'),
    gapToComposer: c ? Math.round(c.top - p.bottom) : null,
    // The reported bug: the panel was wider than the box it is pinned above.
    panelWidth: Math.round(p.width),
    composerWidth: c ? Math.round(c.width) : null,
    widthDelta: c ? Math.round(p.width - c.width) : null,
    leftDelta: c ? Math.round(p.left - c.left) : null,
    rightDelta: c ? Math.round(p.right - c.right) : null,
    viewport: Math.round(document.documentElement.clientWidth),
    // Measure the panel's ACTUAL parent — the flex container that owns the
    // stack gap. Reading a testid and assuming it carries the style is how the
    // first pass reported `0px` for a padding that was really applied.
    stackTag: panel.parentElement ? panel.parentElement.getAttribute('data-testid') : null,
    stackRowGap: panel.parentElement ? getComputedStyle(panel.parentElement).rowGap : null,
    stackPadding: panel.parentElement ? getComputedStyle(panel.parentElement).paddingTop : null,
    stackDisplay: panel.parentElement ? getComputedStyle(panel.parentElement).display : null,
    tcChatMatches: document.querySelectorAll('[data-testid="tc-chat"]').length,
    chatRowGap: chat ? getComputedStyle(chat).rowGap : null,
    mode: document.querySelector('[data-testid=run-workspace-rail]')?.getAttribute('data-mode'),
  };
})()
"""


def report(s: DriverSession, label: str) -> None:
    m = s.evaluate(JS_MEASURE)
    print(f"\n=== {label} ===")
    print(json.dumps(m, indent=2))
    s.shot(f"todo-density-{label}")


def main() -> int:
    key = load_env_key(PROVIDER)
    print(f"key_len={len(key)} (withheld)")
    with DriverSession(name="todo-density-proof") as s:
        # A wide window is where the content-rail cap bites — the panel
        # overhanging the composer is invisible in a narrow one.
        try:
            print("resize:", s.resize(1800, 1150))
        except Exception as exc:  # the WM may refuse; the run is still valid
            print(f"resize unavailable ({exc}); continuing at default size")
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        s.send(PROMPT, timeout_s=300)

        # `send` returns on the run's terminal beat, but the panel is painted
        # from `todo_list_updated` snapshots and settles a moment later. Poll
        # for the finished list rather than sleeping a guess.
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            m = s.evaluate(JS_MEASURE)
            if m.get("panel") and m.get("complete") == "true":
                break
            time.sleep(2)
        else:
            print("NOTE: no completed todo panel appeared within 180s")

        report(s, "studio")

        s.evaluate(
            """
            (() => {
              const b = [...document.querySelectorAll('button, [role=tab], [role=radio]')]
                .find(x => (x.textContent||'').trim() === 'Focus');
              if (b) { b.click(); return 'clicked'; }
              return 'not-found';
            })()
            """
        )
        time.sleep(2.5)
        report(s, "focus")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
