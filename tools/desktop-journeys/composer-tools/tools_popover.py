#!/usr/bin/env python3
"""Journey CT-01/02/05/10 — the desktop Tools popover is truly interactive.

This launches the real packaged Electron app through the standard desktop
journey driver. It needs no provider API key and never sends a model request:
sign in locally, skip the key setup, open the empty Run composer, then prove
that the visible Web search row receives the pointer hit (rather than the
transparent click-out scrim). It also proves toggle, Escape, and click-out.

    APP_DIR="$PWD/apps/desktop" COPILOT_HOME=/path/to/resources \
      python3 tools/desktop-journeys/composer-tools/tools_popover.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession  # noqa: E402


TOOLS_BUTTON = "[data-testid=first-run-tools-button]"
PANEL = "[data-testid=first-run-tools-popover]"
WEB_SEARCH = "[data-testid=first-run-tools-websearch]"
SCRIM = ".ui-pop-scrim"


def wait_until_absent(
    session: DriverSession, selector: str, timeout_s: int = 8
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not session.present(selector):
            return True
        time.sleep(0.15)
    return False


def web_search_row_receives_pointer(session: DriverSession) -> bool:
    return bool(
        session.evaluate(
            """(() => {
              const row = document.querySelector('[data-testid=first-run-tools-websearch]');
              if (!row) return false;
              const rect = row.getBoundingClientRect();
              const target = document.elementFromPoint(rect.left + 12, rect.top + 12);
              return target !== null && row.contains(target);
            })()"""
        )
    )


def main() -> int:
    with DriverSession(name="composer-tools-popover") as session:
        session.sign_in_local()
        assert session.wait_for("[data-testid=first-run-shell]", timeout_s=60), (
            "first-run shell never appeared"
        )
        session.click("[data-testid=first-run-skip]")
        assert session.wait_for("[data-testid=run-empty-composer]", timeout_s=60), (
            "skip did not land on the empty Run composer"
        )
        session.shot("empty-run")

        session.click(TOOLS_BUTTON)
        assert session.wait_for(PANEL, timeout_s=10), "Tools dialog did not open"
        # Wait for the design recipe's 0.14s open animation before sampling the
        # real compositor hit target.
        time.sleep(0.25)
        assert web_search_row_receives_pointer(session), (
            "Web search row is covered by the click-out scrim or clipped"
        )
        session.shot("tools-open-interactive")

        assert (
            session.evaluate(
                "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
            )
            == "true"
        ), "Web search should default on"
        session.click(WEB_SEARCH)
        assert (
            session.evaluate(
                "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
            )
            == "false"
        ), "Web search toggle did not change selection"
        session.shot("web-search-paused")

        session.press(PANEL, "Escape")
        assert wait_until_absent(session, PANEL), "Escape did not close Tools"

        session.click(TOOLS_BUTTON)
        assert session.wait_for(PANEL, timeout_s=10), "Tools did not reopen"
        session.click(SCRIM)
        assert wait_until_absent(session, PANEL), "click-out scrim did not close Tools"

    print(
        "PASS: composer Tools popover is visible, interactive, toggleable, and dismissible"
    )
    return 0


if __name__ == "__main__":
    # This run does not need a provider key. Avoid changing the journey's scope
    # if a caller happens to have one configured.
    os.environ.pop("OPENAI_API_KEY", None)
    raise SystemExit(main())
