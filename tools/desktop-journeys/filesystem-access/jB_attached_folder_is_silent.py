#!/usr/bin/env python3
"""FS-B — an ATTACHED folder stops asking, in the lane the desktop actually runs.

``attached_folder_stops_asking.py`` (FS3) proves this under
``WORKSPACE_EFFECT_MODE=enforce``. That is an OPERATOR OPT-IN, not what a user
gets: ``apps/desktop/main/services/service-env.ts`` defaults
``OPERATION_GATEWAY_MODE`` to ``off`` and lets ``WORKSPACE_EFFECT_MODE`` follow
it, so the shipped desktop composes the OTHER workspace object entirely. A claim
verified only in the opt-in lane says nothing about the install.

So this runs the same two turns with the flags UNSET (``FS_LANE=default``, the
default) and can be re-run with ``FS_LANE=enforce`` to produce the differential.

Turn 1 — read a file INSIDE the attached folder:
    no consent card at all, and the canary contents come back.
Turn 2 — read a file inside a sibling folder nobody attached:
    the run must PARK. Nothing is clicked, deliberately: if the run resolves its
    own approval with no user action, that is the finding, and the only way to
    see it is to sit still and watch.

Only ``read_file`` is claimed for turn 1. ``ls`` / ``glob`` / ``grep`` take
deepagents' BULK interrupt predicate, which fires on any overlap with an
interrupt anchor, and the catch-all read rule is anchored at ``/`` — those ask
whether or not a folder is attached, by design
(``capabilities/desktop/host_floor.py``). Asserting "a listing does not prompt"
would be asserting a bug, so the prompt forbids listing.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    ENFORCE_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    approval_events,
    assistant_text,
    attach_folder,
    dump,
    events,
    lane,
    result,
    run_status,
    runs_for,
    settle_run,
    tool_calls,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "FS-B"
CARD_SELECTOR = "[data-testid^=tc-chat-approval-]"

#: Mirrors ``WORKSPACE_GRANT_PAYLOAD_KEY`` in
#: ``packages/chat-surface/src/approvals/presentation.ts``. Python cannot import
#: it and a drift would make this journey blind to every folder ask.
GRANT_BLOCK_KEY = "workspace_grant"


def read_prompt(path: Path) -> str:
    return (
        f"Read the file {path} and reply with its exact contents and nothing "
        "else. Read that exact path directly — do not list the directory, do "
        "not search, and do not guess."
    )


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3
    if sys.platform != "darwin":
        result(
            JOURNEY, "skipped", reason="the native folder picker driver is macOS-only"
        )
        return 3

    lane_name = os.environ.get("FS_LANE", "default").strip().lower()
    overrides = ENFORCE_LANE if lane_name == "enforce" else DEFAULT_LANE

    nonce = uuid.uuid4().hex[:12]
    evidence: dict[str, Any] = {"lane": lane_name, "lane_overrides": dict(overrides)}
    with (
        lane(overrides),
        tempfile.TemporaryDirectory(prefix="fsb-attached-") as attached_raw,
        tempfile.TemporaryDirectory(prefix="fsb-ungranted-") as ungranted_raw,
    ):
        attached = Path(attached_raw).resolve()
        ungranted = Path(ungranted_raw).resolve()
        attached_canary = f"fsb-attached-{nonce}"
        ungranted_canary = f"fsb-ungranted-{nonce}"
        (attached / "canary.txt").write_text(attached_canary, encoding="utf-8")
        (ungranted / "canary.txt").write_text(ungranted_canary, encoding="utf-8")
        evidence["attached_root"] = str(attached)
        evidence["ungranted_root"] = str(ungranted)

        session = DriverSession(name=f"fs-b-attached-silent-{lane_name}")
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                session.sign_in_local()
                session.ftue_add_key(provider, key)

                # Attaching is the ONLY way in, and it is a NATIVE dialog. When
                # the host denies the controlling process Accessibility, no
                # keystroke can reach that sheet — so record the block and still
                # run turn 2, which needs no grant and answers the other half.
                try:
                    evidence["grant_id"] = attach_folder(
                        session, attached, mode="read_only", label="FS-B fixture"
                    )
                    session.shot("b-01-folder-attached")
                except Exception as exc:  # noqa: BLE001
                    evidence["attach_blocked"] = repr(exc)[:300]
                    session.shot("b-01-attach-blocked")

                conversation_id: str | None = None
                if evidence.get("grant_id"):
                    # ---- turn 1: inside the attached folder ---------------
                    session.send_first_run_message(read_prompt(attached / "canary.txt"))
                    conversation_id = wait_for_conversation_id(session)
                    run_id = wait_for_new_run(session, conversation_id, 0)
                    final = settle_run(session, run_id)
                    time.sleep(1.5)
                    session.shot("b-02-attached-read")
                    stream = events(session, run_id)
                    answer = assistant_text(session, run_id)
                    evidence["attached"] = {
                        "run_id": run_id,
                        "status": final.get("status"),
                        "tools": tool_calls(stream),
                        "approvals": approval_events(stream),
                        "grant_asks": [
                            a
                            for a in approval_events(stream)
                            if GRANT_BLOCK_KEY in a.get("payload", {})
                        ],
                        "canary_returned": attached_canary in answer,
                        "answer": answer[-600:],
                    }

                # ---- turn 2: the folder nobody attached -------------------
                before = 0
                if conversation_id is not None:
                    before = len(runs_for(session, conversation_id))
                session.send_first_run_message(read_prompt(ungranted / "canary.txt"))
                if conversation_id is None:
                    conversation_id = wait_for_conversation_id(session)
                    # Rider (FS-C): WHEN does the composer's folder bar go away?
                    # FS-C sampled two points and saw it still on screen shortly
                    # after the first send, which is either a slow FTUE→cockpit
                    # handoff or a bar that outlives the message. Sample it.
                    timeline: list[dict[str, Any]] = []
                    start = time.time()
                    for _ in range(45):
                        timeline.append(
                            {
                                "t": round(time.time() - start, 1),
                                "bar": bool(
                                    session.evaluate(
                                        "!!document.querySelector('.aui-folder-bar')"
                                    )
                                ),
                                "cockpit": bool(
                                    session.evaluate(
                                        "!!document.querySelector("
                                        "'[data-testid=thread-canvas]')"
                                    )
                                ),
                            }
                        )
                        if timeline[-1]["cockpit"] and not timeline[-1]["bar"]:
                            break
                        time.sleep(1)
                    evidence["folder_bar_timeline"] = timeline
                run_id = wait_for_new_run(session, conversation_id, before)
                # Deliberately click NOTHING. Sit and watch: a run that resolves
                # its own approval with no user present is the thing worth
                # catching, and only stillness can catch it.
                observed: list[str] = []
                card_seen = False
                deadline = time.time() + 150
                while time.time() < deadline:
                    state = str(run_status(session, run_id).get("status"))
                    if not observed or observed[-1] != state:
                        observed.append(state)
                    if session.present(CARD_SELECTOR):
                        card_seen = True
                    if state in {"completed", "failed", "cancelled", "expired"}:
                        break
                    time.sleep(1)
                session.shot("b-03-ungranted-read")
                stream = events(session, run_id)
                answer = assistant_text(session, run_id)
                evidence["ungranted"] = {
                    "run_id": run_id,
                    "status_trace": observed,
                    "final_status": observed[-1] if observed else None,
                    "card_rendered": card_seen,
                    "tools": tool_calls(stream),
                    "approvals": approval_events(stream),
                    "canary_returned": ungranted_canary in answer,
                    "answer": answer[-600:],
                }
        finally:
            out = dump(session.run_dir, f"fs-b-{lane_name}-evidence.json", evidence)
            print(f"[fs-b] evidence -> {out}", flush=True)

    attached_ev = evidence.get("attached", {})
    ungranted_ev = evidence.get("ungranted", {})
    failures: list[str] = []
    if evidence.get("attach_blocked"):
        # Not a pass and not a failure of the product: the half that needed a
        # grant never ran. Reported with its own outcome so no caller can read
        # silence as success.
        result(
            JOURNEY,
            "BLOCKED",
            lane=lane_name,
            reason=(
                "the native folder picker could not be driven: "
                + str(evidence["attach_blocked"])
            ),
            ungranted_final=ungranted_ev.get("final_status"),
            ungranted_canary=ungranted_ev.get("canary_returned"),
            ungranted_approvals=len(ungranted_ev.get("approvals") or []),
        )
        return 2
    if attached_ev.get("approvals"):
        failures.append(
            "reading INSIDE the attached folder still raised an approval — "
            "attaching bought the user nothing in this lane"
        )
    if not attached_ev.get("canary_returned"):
        failures.append("the attached file did not come back readable")
    if ungranted_ev.get("canary_returned"):
        failures.append("an UNGRANTED file was read without any consent")
    if not ungranted_ev.get("approvals"):
        failures.append("the ungranted read raised no approval at all")

    if failures:
        result(JOURNEY, "FAILED", lane=lane_name, reasons=failures)
        return 1
    result(
        JOURNEY,
        "passed",
        lane=lane_name,
        attached_approvals=len(attached_ev.get("approvals") or []),
        attached_canary=attached_ev.get("canary_returned"),
        ungranted_final=ungranted_ev.get("final_status"),
        ungranted_canary=ungranted_ev.get("canary_returned"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
