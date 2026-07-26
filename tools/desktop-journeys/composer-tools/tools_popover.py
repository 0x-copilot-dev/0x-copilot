#!/usr/bin/env python3
"""Journey CT-01/02/03/05/06/10 — composer `+` menu Tools, desktop only.

The rich per-run Tools controls have ONE entry point: the composer `+` menu.
This drives the packaged Electron app through all desktop composer placements:

* first-run composer,
* a bound run in Studio and Focus, and
* a fresh New Chat in Studio and Focus.

Each placement proves real pointer disclosure and Web Search interaction; the
bound Studio path additionally proves keyboard Enter/Space, Back, Escape, and
click-out. No selector assertion is accepted until the row is the actual hit
target at its rendered coordinates.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession, load_env_key  # noqa: E402


PLUS = 'button[aria-label="Open attachment and tools menu"]'
TOOLS_ENTRY = "[data-testid=composer-plus-menu-tools]"
TOOLS_MENU = '[role=menu][aria-label="Tools menu"]'
ROOT_MENU = '[role=menu][aria-label="Attachment and tools menu"]'
WEB_SEARCH = "[data-testid=first-run-tools-websearch]"
BACK = "[data-testid=first-run-tools-back]"
OLD_TOOLS_BUTTON = "[data-testid=first-run-tools-button]"
OLD_TOOLS_DIALOG = "[data-testid=first-run-tools-popover]"
RUN_HEADER = "[data-testid=run-header]"


def wait_until_absent(
    session: DriverSession, selector: str, timeout_s: int = 8
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not session.present(selector):
            return True
        time.sleep(0.15)
    return False


def row_receives_pointer(session: DriverSession) -> bool:
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


def open_tools_with_pointer(session: DriverSession) -> None:
    session.click(PLUS)
    assert session.wait_for(ROOT_MENU, timeout_s=10), "`+` menu did not open"
    session.click(TOOLS_ENTRY)
    assert session.wait_for(TOOLS_MENU, timeout_s=10), "`+ → Tools` did not open"


def assert_single_entry_point(session: DriverSession, label: str) -> None:
    assert not session.present(OLD_TOOLS_BUTTON), (
        f"{label}: retired standalone Tools pill is still rendered"
    )
    assert not session.present(OLD_TOOLS_DIALOG), (
        f"{label}: retired standalone Tools dialog is still rendered"
    )


def exercise_tools(
    session: DriverSession,
    label: str,
    *,
    keyboard: bool = False,
    outside_selector: str = RUN_HEADER,
) -> None:
    """Pointer-open, verify hit target, toggle, Back, click-out; optionally keys."""
    assert_single_entry_point(session, label)
    open_tools_with_pointer(session)
    time.sleep(0.2)  # let the shared pop recipe settle before compositor sampling
    assert row_receives_pointer(session), f"{label}: Web Search row is clipped/covered"
    session.shot(f"{label}-tools-open")

    before = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    assert before in ("true", "false"), f"{label}: Web Search has no aria state"
    session.click(WEB_SEARCH)
    after = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    assert after != before, f"{label}: pointer click did not toggle Web Search"

    session.click(BACK)
    assert session.wait_for(ROOT_MENU, timeout_s=5), (
        f"{label}: Back did not return to + root"
    )
    session.click(outside_selector)
    assert wait_until_absent(session, ROOT_MENU), (
        f"{label}: click-out did not close + menu"
    )

    if not keyboard:
        return

    # Use real Enter/Space events, not synthetic DOM state changes.
    session.press(PLUS, "Enter")
    assert session.wait_for(ROOT_MENU, timeout_s=5), f"{label}: Enter did not open +"
    session.press(TOOLS_ENTRY, "Enter")
    assert session.wait_for(TOOLS_MENU, timeout_s=5), (
        f"{label}: Enter did not open Tools"
    )
    before = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    session.press(WEB_SEARCH, "Space")
    after = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    assert after != before, f"{label}: Space did not toggle Web Search"
    session.press(WEB_SEARCH, "Escape")
    assert wait_until_absent(session, TOOLS_MENU), (
        f"{label}: Escape did not close Tools"
    )


def set_mode(session: DriverSession, mode: str) -> None:
    session.click(f"[data-testid=run-mode-{mode}]")
    deadline = time.time() + 8
    while time.time() < deadline:
        # A blank New Chat has no ThreadCanvas yet, so `data-mode` is absent
        # until its first send. The segmented control is the source of truth in
        # both blank and bound states; when a canvas exists, assert it agrees.
        selected = session.evaluate(
            f"document.querySelector('[data-testid=run-mode-{mode}]')?.getAttribute('aria-selected')"
        )
        canvas_mode = session.run_mode()
        if selected == "true" and (canvas_mode is None or canvas_mode == mode):
            return
        time.sleep(0.15)
    raise AssertionError(
        f"mode did not become {mode!r}; selected={selected!r}; canvas={session.run_mode()!r}"
    )


def wait_for_bound_run(session: DriverSession) -> None:
    deadline = time.time() + 120
    while time.time() < deadline:
        if session.on_run() and session.present("[data-testid=run-mode-switcher]"):
            return
        time.sleep(0.5)
    raise AssertionError("first message never reached a bound Run cockpit")


def main() -> int:
    key = load_env_key("anthropic")
    print(f"[composer-tools] anthropic key_len={len(key)} (value withheld)")

    with DriverSession(name="composer-tools-plus-menu") as session:
        # First-run composer: the same shared `+` menu, before a conversation
        # exists. It has no Studio/Focus shell yet but must not retain the pill.
        session.sign_in_local()
        session.ftue_add_key("anthropic", key)
        exercise_tools(
            session,
            "first-run",
            # The hero heading lies behind the upward-opening panel at the
            # current desktop viewport; use the inert top brand to exercise a
            # genuine, visible click-out target instead.
            outside_selector="[data-testid=first-run-brand]",
        )

        # Bound (in-chat) Run composer in both modes.
        session.send_first_run_message("Reply with exactly: ready")
        wait_for_bound_run(session)
        set_mode(session, "studio")
        exercise_tools(session, "bound-studio", keyboard=True)
        set_mode(session, "focus")
        exercise_tools(session, "bound-focus")

        # New Chat is the empty Run composer, again in both modes.
        session.open_destination("Chats")
        assert session.wait_for("[data-testid=chats-new-chat]", timeout_s=30), (
            "Chats did not expose New chat"
        )
        session.click("[data-testid=chats-new-chat]")
        assert session.wait_for("[data-testid=run-empty-composer]", timeout_s=30), (
            "New chat did not open the empty Run composer"
        )
        set_mode(session, "studio")
        exercise_tools(session, "new-chat-studio")
        set_mode(session, "focus")
        exercise_tools(session, "new-chat-focus")

    print(
        "PASS: + → Tools is interactive in first-run, bound Studio/Focus, and "
        "New Chat Studio/Focus; no standalone Tools pill remains"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
