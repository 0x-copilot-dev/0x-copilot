#!/usr/bin/env python3
"""T6 — the checklist while the run is parked on a filesystem-access ask.

A multi-step task that touches an ungranted folder produces both surfaces at
once. This pins the three decisions that came out of seeing it live:

  1. the consent card is IN the transcript, anchored where it was asked — not in
     a pinned strip that pushes the conversation around;
  2. the checklist is the ONLY pinned element above the composer, so it never
     shifts when a gate opens;
  3. the in-progress row reads *waiting*, not *working*. A spinner asserts
     motion, and a parked run has none — it is stopped on the user.

Privacy: the fixture is a journey-owned temporary directory with one canary
file. Nothing under the user's home is read.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("AGENT_TODOS_PROVIDER", "openai")
APPROVAL_CARD = '[data-testid^="tc-chat-approval-item-"]'
WAITING_LINE = "[data-testid=tc-chat-approvals-waiting]"


def log(line: str) -> None:
    print(line, flush=True)


def observe(s: DriverSession) -> dict:
    """Read the layout and the checklist exactly as the user sees them.

    ``approvalsOutsideTranscript`` replaces the two dead strip probes
    (``tc-chat-approvals`` / ``tc-chat-conf-cards``). Nothing in the product
    emits either id any more, so asserting their absence asserted nothing at
    all. The invariant they stood for is still real, so it is checked directly:
    NO approval node may render outside the transcript. A pinned strip that grew
    back — under ANY name — puts one here.

    The prefix is deliberately ``tc-chat-approval-`` with the trailing hyphen.
    It matches the stream ``<li>`` (``tc-chat-approval-item-<id>``), the ask
    wrapper (``tc-chat-approval-<id>``) and its three decision controls
    (``-approve-`` / ``-reject-`` / ``-body-approve-<id>``), and it does NOT
    match the reachability line ``tc-chat-approvals-waiting``, which lives above
    the composer on purpose and is asserted present separately.
    """
    js = """(()=>{
      const chat=document.querySelector('[data-testid=tc-chat]');
      if(!chat) return "null";
      const messages=document.querySelector('[data-testid=tc-chat-messages]');
      const card=document.querySelector('[data-testid^="tc-chat-approval-item-"]');
      const root=document.querySelector('[data-testid=tc-todo-list]');
      const rows=root?[...root.querySelectorAll('[data-testid=tc-todo-row]')].map((r)=>({
        status:r.getAttribute('data-status'),
        waiting:r.getAttribute('data-waiting'),
        text:r.innerText,
        spinner:!!r.querySelector('[data-testid=tc-todo-spinner]'),
        stillGlyph:!!r.querySelector('[data-testid=tc-todo-waiting]'),
      })):[];
      return JSON.stringify({
        order:[...chat.children].map((n)=>n.getAttribute('data-testid')).filter(Boolean),
        cardPresent:!!card,
        cardInTranscript:!!(card&&messages&&messages.contains(card)),
        cardPending:card&&card.getAttribute('data-approval-pending'),
        approvalsOutsideTranscript:[...document.querySelectorAll('[data-testid^="tc-chat-approval-"]')]
          .filter((n)=>!(messages&&messages.contains(n)))
          .map((n)=>n.getAttribute('data-testid')),
        waitingLine:(document.querySelector('[data-testid=tc-chat-approvals-waiting]')||{}).innerText||null,
        todoBlocked:root&&root.getAttribute('data-blocked'),
        todoRows:rows,
        toolCards:[...document.querySelectorAll('.tc-activity-card[data-tool-status]')].map((n)=>({
          status:n.getAttribute('data-tool-status'),
          waiting:n.getAttribute('data-tool-waiting'),
          spinner:!!n.querySelector('.tc-tool-card__spinner'),
          stillGlyph:!!n.querySelector('[data-testid=tc-tool-card-waiting]'),
          text:n.innerText,
        })),
      });
    })()"""
    raw = s.evaluate(js)
    return json.loads(raw) if raw and raw != "null" else {}


def main() -> int:
    key = load_env_key(PROVIDER)
    fixture = Path(tempfile.mkdtemp(prefix="todo-gate-"))
    (fixture / f"canary-{uuid.uuid4().hex[:10]}.txt").write_text("ok\n")

    with DriverSession(name="agent-todos-gate") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}")

        s.send_first_run_message(
            "Use the write_todos tool to plan this as exactly THREE todos, then "
            "do them one at a time, marking each completed before the next: "
            f"(1) list the files in the directory {fixture}, (2) report the exact "
            "file names you found, (3) state how many files there were. If you "
            "cannot read the directory, say so plainly and do not guess."
        )
        assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"

        # Catch the parked moment: a checklist row plus a pending consent card.
        deadline = time.time() + 180
        view: dict = {}
        while time.time() < deadline:
            view = observe(s)
            if view.get("todoRows") and view.get("cardPending") == "true":
                break
            time.sleep(0.25)
        else:
            log("INCONCLUSIVE  the gate and the checklist never overlapped")
            log(f"      last observed: {json.dumps(view)}")
            s.shot("t6-no-overlap")
            return 2

        s.shot("t6-gate-and-checklist")
        log("── T6 parked on a folder ask ───────────────────────────────")
        log(f"      DOM order: {view['order']}")

        # 1. The card is in the transcript, and NOTHING approval-shaped is
        #    anywhere else — which is the claim the two dead strip ids used to
        #    stand in for, stated so that a strip under a new name fails too.
        assert view["cardInTranscript"], (
            f"the consent card is not anchored in the transcript: {view!r}"
        )
        assert view["approvalsOutsideTranscript"] == [], (
            "approval nodes rendered outside the transcript — a pinned strip is "
            f"back: {view['approvalsOutsideTranscript']!r}"
        )
        log("PASS  T6a consent card anchored in the transcript, no strips")

        # 2. The checklist is the last thing before the composer, always.
        order = view["order"]
        assert order[-1] == "tc-chat-composer-slot", order
        assert order[-2] == "tc-todo-list", (
            f"the checklist must sit directly above the composer; got {order!r}"
        )
        assert view["waitingLine"], "no reachability affordance while parked"
        log(f"PASS  T6b checklist pinned last; notice reads {view['waitingLine']!r}")

        # 3. The blocked row waits rather than spins.
        assert view["todoBlocked"] == "true", view
        active = [r for r in view["todoRows"] if r["status"] == "in_progress"]
        assert active, f"no in-progress row to check: {view['todoRows']!r}"
        for row in active:
            assert row["waiting"] == "true", row
            assert not row["spinner"], f"a parked row is still spinning: {row!r}"
            assert row["stillGlyph"], f"the waiting glyph is missing: {row!r}"
            assert "waiting for you" in row["text"], row
        log(f"PASS  T6c {len(active)} parked row(s) read waiting, not working")

        # 4. The tool card above tells the same truth. It was the last surface
        #    still saying "Running" over a run that was doing nothing.
        stalled = [c for c in view["toolCards"] if c["status"] == "running"]
        assert stalled, f"no running tool card to check: {view['toolCards']!r}"
        for card in stalled:
            assert card["waiting"] == "true", card
            assert not card["spinner"], f"a parked tool card still spins: {card!r}"
            assert card["stillGlyph"], f"the waiting glyph is missing: {card!r}"
            assert "Waiting" in card["text"], card
        log(f"PASS  T6d {len(stalled)} parked tool card(s) read Waiting, not Running")

        log("")
        log("JOURNEY PASSED — checklist and consent coexist honestly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
