#!/usr/bin/env python3
"""Journey A — Chats "New chat" opens a FRESH cockpit (not the current run).

Bug (before PR #260): bootstrap.tsx's onNewChat called handleNavigate('run')
and never cleared activeConversationId, so "New chat" re-opened the run you were
already in. Fix (#260, 548d064f): onNewChat now calls openNewRun (same as ⌘N /
the palette) → a fresh empty cockpit.

This driver first reaches a BOUND run (FTUE add-key + send a message so a
conversation binds), then opens Chats and clicks New chat, and asserts the
cockpit is a clean slate: the empty composer is present, the transcript is
empty, and the header does not claim "ACTIVE RUN".

    python3 tools/desktop-journeys/chat-nav-model/new_chat.py

The Anthropic key is read ONLY from services/ai-backend/.env via load_env_key
and is never printed or logged. Exits non-zero on any failed assertion.

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


def _body_has_active_run(s: DriverSession) -> bool:
    return bool(
        s.evaluate('(document.body.innerText||"").toUpperCase().includes("ACTIVE RUN")')
    )


def main() -> int:
    key = load_env_key("anthropic")  # value never printed
    print(f"[new-chat] anthropic key_len={len(key)} (value withheld)")

    with DriverSession(name="chat-nav-new-chat") as s:
        # 1. Reach a BOUND run: sign in → FTUE add-key → send a first message ----
        s.sign_in_local()
        s.ftue_add_key("anthropic", key)  # reveals first-run-composer
        s.send_first_run_message("say hello in one short sentence")
        s.shot("first-run-sent")

        # Wait for the run to bind + start streaming (a real assistant reply).
        deadline = time.time() + 120
        while time.time() < deadline:
            if s.on_run() and _message_count(s) >= 2:
                break
            time.sleep(1)
        assert s.on_run(), "expected to land on a bound run after the first message"
        first_convo = s.evaluate("window.location.hash")
        s.shot("bound-run")
        print(
            f"PASS: first message bound a run (hash={first_convo!r}, "
            f"messages={_message_count(s)})"
        )

        # 2. Open the Chats archive ---------------------------------------------
        s.open_destination("Chats")
        assert s.wait_for("[data-testid=chats-new-chat]"), (
            "Chats archive New-chat CTA (chats-new-chat) never appeared"
        )
        s.shot("chats-archive")
        print("PASS: Chats archive shows the New-chat CTA (chats-new-chat)")

        # 3. Click "New chat" ----------------------------------------------------
        s.click("[data-testid=chats-new-chat]")
        assert s.wait_for("[data-testid=run-empty-composer]"), (
            "New chat did not open the empty cockpit (run-empty-composer) — "
            "regression: it likely re-opened the previously-bound run"
        )
        time.sleep(1)
        s.shot("new-chat-empty-cockpit")

        # 4. Assert a FRESH empty cockpit, NOT the previous run ------------------
        assert s.present("[data-testid=run-empty-composer]"), (
            "run-empty-composer not present after New chat"
        )
        msgs = _message_count(s)
        assert msgs == 0, (
            f"expected a clean transcript after New chat, found {msgs} messages — "
            f"the previously-bound run was re-opened (BUG A)"
        )
        assert not _body_has_active_run(s), (
            'header still claims "ACTIVE RUN" after New chat — the fresh cockpit '
            "must be idle (STANDBY), not the previous run"
        )
        print(
            "PASS: New chat → FRESH empty cockpit (run-empty-composer, "
            "0 messages, no ACTIVE RUN)"
        )

    print("\nALL PASS — Journey A (Chats New-chat opens a fresh cockpit) — PR #260")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
