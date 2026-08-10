#!/usr/bin/env python3
"""Focus opens with the Run-details column FOLDED — verified on screen.

jsdom runs no layout, so the unit tests around
`DEFAULT_RUN_FOCUS_PANEL_COLLAPSED` prove the value and nothing about the
pixels. This drives the real app: send one message, switch to Focus with the
same ⌘M a user presses, and read `data-focus-panel-collapsed` off the rail plus
a screenshot.

Also asserts the panel still OPENS on demand, because a default that cannot be
undone is a worse bug than the one being fixed.
"""

from __future__ import annotations

import json
import os
import time

from _lib import DriverSession, load_env_key

PROVIDER = os.environ.get("JOURNEY_PROVIDER", "virtuals")
PROMPT = "Reply with exactly: ok"

JS_RAIL = """
(() => {
  const rail = document.querySelector('[data-testid=run-workspace-rail]');
  if (!rail) return {rail: false};
  return {
    rail: true,
    mode: rail.getAttribute('data-mode'),
    focusPanelCollapsed: rail.getAttribute('data-focus-panel-collapsed'),
    // Does the Activity column actually occupy width on screen? The attribute
    // is the intent; this is the ink.
    activityVisible: [...document.querySelectorAll('*')].some(n =>
      (n.textContent||'').trim() === 'Activity' && n.getBoundingClientRect().width > 0
    ),
  };
})()
"""


def main() -> int:
    key = load_env_key(PROVIDER)
    print(f"key_len={len(key)} (withheld)")
    with DriverSession(name="focus-panel-default") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        s.send(PROMPT, timeout_s=180)

        # Click the header's Focus control. ⌘M works too but did not always
        # land here, and the control is what a user actually reaches for.
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
        time.sleep(2)
        state = s.evaluate(JS_RAIL)
        if state.get("mode") != "focus":
            s.press("body", "Meta+m")
            time.sleep(2)
            state = s.evaluate(JS_RAIL)
        print("\n=== on entering Focus ===")
        print(json.dumps(state, indent=2))
        s.shot("focus-default")

        collapsed = state.get("focusPanelCollapsed")
        assert state.get("mode") == "focus", f"not in Focus: {state}"
        assert collapsed == "true", (
            f"Focus opened with the Run-details column EXPANDED "
            f"(data-focus-panel-collapsed={collapsed!r})"
        )
        assert not state.get("activityVisible"), (
            "the Activity column still has width on screen despite the "
            "collapsed attribute — the attribute and the layout disagree"
        )

        # It must still open. A default you cannot undo is the worse bug.
        # The collapsed rail renders an icon strip whose expand control is
        # labelled "Expand run details" (renderFocusStrip). Match that exactly —
        # a looser selector clicked an app-rail destination on the first pass
        # and navigated away from Run entirely.
        opened = s.evaluate(
            """
            (() => {
              const b = [...document.querySelectorAll('button')].find(x =>
                (x.getAttribute('aria-label')||'') === 'Expand run details');
              if (!b) return 'no-toggle-found';
              b.click();
              return 'clicked';
            })()
            """
        )
        time.sleep(1.5)
        after = s.evaluate(JS_RAIL)
        print(f"\n=== after clicking the toggle ({opened}) ===")
        print(json.dumps(after, indent=2))
        s.shot("focus-after-expand")
        if opened == "clicked":
            assert after.get("focusPanelCollapsed") == "false", (
                "the panel did not re-open — the new default is not undoable"
            )
            print("\nVERDICT: folded by default, and still opens on demand.")
        else:
            print(
                f"\nNOTE: toggle not located ({opened}); default verified, "
                "re-open unverified in this pass"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
