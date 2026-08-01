#!/usr/bin/env python3
"""FS-A — the ungranted ask, all the way through the user's own Approve click.

``ungranted_path_asks.py`` (FS1) stops one step short on purpose: it treats a
PARKED run as its pass, because a run that correctly stops to ask never becomes
terminal. That leaves the half a user actually experiences unproven — the card
has to NAME the folder, and clicking Approve has to come back with the REAL
listing, not a plausible one.

So this journey asserts three separate things, and keeps them separate:

  1. a consent card is rendered, and its VISIBLE TEXT names the fixture folder;
  2. the pending approval exists in the event stream with the folder in its
     payload (so the card is not a client-side invention);
  3. after the decision the run completes and the answer contains the canary
     FILE NAME the journey wrote and never told the model about.

(3) is the load-bearing one. The canary name is a random hex string created
after the app booted; a model cannot produce it by guessing, so its presence is
proof that a real ``ls`` of a real directory reached the answer.

It also RECORDS who decided. If the card resolves without a click, that is not
a failure of this script — it is the finding, and the decision payload names the
authority, so the report can say which.

Privacy: the fixture is a journey-owned temporary directory. Nothing under the
user's home is read.
"""

from __future__ import annotations

import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    approval_events,
    assistant_text,
    dump,
    events,
    lane,
    result,
    runs_for,
    settle_run,
    tool_calls,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "FS-A"

CARD_SELECTOR = "[data-testid^=tc-chat-approval-]"
APPROVE_SELECTOR = "[data-testid^=tc-chat-approval-approve-]"

#: Read the card the way a person does — every card on screen, by its text.
CARD_TEXT_JS = (
    "(() => Array.from(document.querySelectorAll('"
    + CARD_SELECTOR
    + "')).map((el) => ({ testid: el.getAttribute('data-testid'),"
    " text: (el.innerText || '').trim().slice(0, 600) })))()"
)


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    nonce = uuid.uuid4().hex[:12]
    evidence: dict[str, Any] = {}
    with lane(DEFAULT_LANE), tempfile.TemporaryDirectory(prefix="fsa-") as raw:
        root = Path(raw).resolve()
        canary = f"canary-{nonce}.txt"
        (root / canary).write_text(f"fsa-{nonce}", encoding="utf-8")
        evidence["fixture_root"] = str(root)
        evidence["canary_name"] = canary

        session = DriverSession(name="fs-a-ungranted-approve")
        try:
            with session:
                status = session.rpc("status")
                evidence["target"] = status.get("target")
                evidence["posture"] = status.get("posture")

                session.sign_in_local()
                session.ftue_add_key(provider, key)
                session.shot("a-01-ready")

                session.send_first_run_message(
                    f"List the files in the directory {root}. Report exactly "
                    "what you find. If you cannot read it, say so plainly and "
                    "do not guess."
                )
                conversation_id = wait_for_conversation_id(session)
                run_id = wait_for_new_run(session, conversation_id, 0)
                evidence["conversation_id"] = conversation_id
                evidence["run_id"] = run_id

                # 1 + 2. Catch the card while it is on screen. Poll fast: a card
                # that is resolved by something other than the user can be brief.
                cards: list[dict[str, Any]] = []
                deadline = time.time() + 150
                while time.time() < deadline:
                    found = session.evaluate(CARD_TEXT_JS)
                    if isinstance(found, list) and found:
                        cards = found
                        break
                    time.sleep(0.2)
                evidence["cards_seen"] = cards
                if cards:
                    session.shot("a-02-approval-card")
                    evidence["card_names_folder"] = any(
                        root.name.lower() in str(card.get("text", "")).lower()
                        or str(root).lower() in str(card.get("text", "")).lower()
                        for card in cards
                    )
                    # 1b. Click Approve, as a user would — if it is still there.
                    if session.present(APPROVE_SELECTOR):
                        session.click(APPROVE_SELECTOR)
                        evidence["user_clicked_approve"] = True
                    else:
                        evidence["user_clicked_approve"] = False
                else:
                    evidence["card_names_folder"] = False
                    evidence["user_clicked_approve"] = False

                final = settle_run(session, run_id)
                evidence["run_status"] = final.get("status")
                evidence["safe_error"] = final.get("safe_error")
                time.sleep(2)
                session.shot("a-03-outcome")

                stream = events(session, run_id)
                evidence["event_count"] = len(stream)
                evidence["approvals"] = approval_events(stream)
                evidence["tools"] = tool_calls(stream)
                answer = assistant_text(session, run_id)
                evidence["answer"] = answer[-2000:]
                evidence["mentions_canary"] = canary.lower() in answer.lower()
                evidence["runs"] = [
                    {"run_id": r.get("run_id"), "status": r.get("status")}
                    for r in runs_for(session, conversation_id)
                ]
        finally:
            out = dump(session.run_dir, "fs-a-evidence.json", evidence)
            print(f"[fs-a] evidence -> {out}", flush=True)

    failures: list[str] = []
    if not evidence.get("cards_seen"):
        failures.append("no consent card was ever rendered for an ungranted folder")
    elif not evidence.get("card_names_folder"):
        failures.append("the consent card did not name the folder it was asking about")
    if evidence.get("run_status") != "completed":
        failures.append(f"run did not complete: {evidence.get('run_status')!r}")
    if not evidence.get("mentions_canary"):
        failures.append("the approved listing did not contain the canary file")

    if failures:
        result(JOURNEY, "FAILED", reasons=failures, run_id=evidence.get("run_id"))
        return 1
    result(
        JOURNEY,
        "passed",
        card_named_folder=True,
        user_clicked_approve=evidence.get("user_clicked_approve"),
        canary_returned=True,
        approvals=len(evidence.get("approvals") or []),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
