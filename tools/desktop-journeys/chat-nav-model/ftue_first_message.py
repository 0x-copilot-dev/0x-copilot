#!/usr/bin/env python3
"""Journey B — the FTUE first message lands on its RUN, not an empty standby.

Bug (before PR #260): the FTUE created the conversation + run but the hand-off
into the shell discarded the {conversationId, runId}, so the very first message
vanished onto the empty standby composer. Fix (#260, 548d064f): FirstRunLaunch-
Result is threaded end-to-end and the gate navigates the HashRouter to
#/convo/{conversationId} BEFORE revealing the shell, so Run binds the freshly-
created run.

    python3 tools/desktop-journeys/chat-nav-model/ftue_first_message.py

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


def _has_error(s: DriverSession) -> bool:
    return bool(s.evaluate('!!document.querySelector("[data-testid*=error]")'))


def main() -> int:
    key = load_env_key("anthropic")  # value never printed
    print(f"[ftue-first-msg] anthropic key_len={len(key)} (value withheld)")

    with DriverSession(name="chat-nav-ftue-first-message") as s:
        # 1. Sign in → FTUE add Anthropic key -----------------------------------
        s.sign_in_local()
        s.ftue_add_key("anthropic", key)  # reveals first-run-composer
        s.shot("ftue-composer")

        # FTUE model pill must lead with a Claude model (preselect, journey C).
        pill = (
            s.evaluate(
                '(document.querySelector(".atlas-model-pill")||{}).innerText||null'
            )
            or ""
        ).strip()
        print(f"[ftue-first-msg] FTUE model pill = {pill!r}")
        assert pill, "FTUE composer has no model pill text"
        assert "claude" in pill.lower(), (
            f"FTUE pill should show a Claude model for an Anthropic-only key, got {pill!r}"
        )
        assert "gpt-5.4" not in pill.lower(), (
            f"FTUE pill must not preselect the keyless GPT-5.4 default, got {pill!r}"
        )
        print(f"PASS: FTUE pill shows a Claude model ({pill!r})")

        # 2. Send the very first message ----------------------------------------
        s.send_first_run_message("write a haiku about the sea")
        s.shot("ftue-sent")

        # 3. Within ~10s it must land on the RUN (bound convo), NOT standby ------
        landed = False
        deadline = time.time() + 10
        while time.time() < deadline:
            if s.on_run():
                landed = True
                break
            time.sleep(0.5)
        assert landed, (
            "the first message did NOT land on a run within ~10s — it vanished "
            "onto the empty standby screen (BUG B)"
        )

        hash_ = s.evaluate("window.location.hash") or ""
        assert "#/convo/" in hash_, (
            f"expected the route to bind a conversation (#/convo/...), got {hash_!r}"
        )
        assert not s.present("[data-testid=run-empty-composer]"), (
            "run-empty-composer is still showing — the message sat on standby (BUG B)"
        )
        print(f"PASS: first message landed on its run (hash={hash_!r})")

        # 4. And a real assistant reply streams in ------------------------------
        streamed = False
        deadline = time.time() + 120
        while time.time() < deadline:
            assert not _has_error(s), "an error surface appeared during the run"
            if _message_count(s) >= 2:
                streamed = True
                break
            time.sleep(1)
        s.shot("ftue-reply")
        assert streamed, (
            f"the bound run did not stream an assistant reply within 120s "
            f"(messages={_message_count(s)})"
        )
        print(
            f"PASS: the run streamed a real assistant reply "
            f"({_message_count(s)} messages, no error)"
        )

    print("\nALL PASS — Journey B (FTUE first message lands on its run) — PR #260")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
