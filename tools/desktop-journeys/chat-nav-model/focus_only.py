#!/usr/bin/env python3
"""Journey D — the cockpit opens in Focus with no Studio/Focus toggle.

Change (PR #260, 7369f2cc): Studio is temporarily disabled behind a single
revertable flag — STUDIO_ENABLED = false in
packages/chat-surface/src/destinations/run/useRunMode.ts. With it off: the
default/persisted mode coerces to "focus", the ⌘M toggle listener never
attaches, and RunHeader hides the segmented switcher (run-mode-switcher).
Flipping the flag back to true restores Studio (and the switcher) in one line.

    python3 tools/desktop-journeys/chat-nav-model/focus_only.py

The Anthropic key is read ONLY from services/ai-backend/.env via load_env_key
and is never printed or logged. Exits non-zero on any failed assertion.

Revertable: this journey's "no switcher" assertion inverts if STUDIO_ENABLED is
flipped back to true — a deliberate one-line change, not a regression.

Fixed in PR #260.
"""

from __future__ import annotations

import time

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _lib import DriverSession, load_env_key  # noqa: E402


def _message_count(s: DriverSession) -> int:
    return int(
        s.evaluate(
            'document.querySelectorAll("[data-testid^=tc-chat-message-]").length'
        )
        or 0
    )


def main() -> int:
    key = load_env_key("anthropic")  # value never printed
    print(f"[focus-only] anthropic key_len={len(key)} (value withheld)")

    with DriverSession(name="chat-nav-focus-only") as s:
        # 1. Reach the cockpit: FTUE add-key + send a message -------------------
        s.sign_in_local()
        s.ftue_add_key("anthropic", key)
        s.send_first_run_message("hi")
        s.shot("focus-sent")

        deadline = time.time() + 120
        while time.time() < deadline:
            if s.on_run() and _message_count(s) >= 2:
                break
            time.sleep(1)
        assert s.on_run(), "expected to reach a bound run cockpit"
        s.shot("focus-cockpit")

        # 2. The resolved layout mode is Focus ----------------------------------
        mode = s.run_mode()
        print(f"[focus-only] thread-canvas data-mode = {mode!r}")
        assert mode == "focus", (
            f'cockpit opened in {mode!r}, expected "focus" '
            "(STUDIO_ENABLED=false ⇒ Focus-only)"
        )
        print('PASS: cockpit opened in Focus (thread-canvas data-mode="focus")')

        # 3. The Studio/Focus segmented switcher is NOT rendered -----------------
        has_switcher = s.present("[data-testid=run-mode-switcher]")
        assert not has_switcher, (
            "run-mode-switcher is present — the Studio/Focus toggle must be hidden "
            "while STUDIO_ENABLED=false"
        )
        print("PASS: no run-mode-switcher rendered (Studio toggle hidden)")

    print("\nALL PASS — Journey D (Focus-only cockpit, no Studio toggle) — PR #260")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
