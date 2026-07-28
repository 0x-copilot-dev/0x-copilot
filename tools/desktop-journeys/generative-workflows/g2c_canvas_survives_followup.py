#!/usr/bin/env python3
"""G2C — the Studio canvas survives a chat-only follow-up turn (PRD-02).

G2A opens the artifact via the Sources rail; G2B asserts the canvas presents it
without that click. Neither sends a SECOND message, which is where the surface
was being lost: the canvas folds ``session.events`` and binding a new run
replaces that stream, so an artifact published on turn 1 stopped existing as far
as the canvas was concerned on turn 2.

That produces the identical "This run completed in chat. No artifact was
created." string PR #413 fixed, which is why it reads as a regression of that fix
rather than the next defect.

Asserted here, in the assembled app — because the client parses a wire shape the
server produces, and verifying the two halves separately is exactly how #413's
second bug survived:

1. turn 1 publishes a CSV and the dataset table renders;
2. a chat-only turn 2 completes;
3. the table is STILL rendered, with no Sources click and no navigation;
4. the conversation canvas endpoint agrees the subject belongs to the
   conversation, and attributes it to the run that produced it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession
from g2_csv_lifecycle import (
    PreflightSkip,
    _assert_no_plaintext_secret,
    _byok_provider,
    _events,
    _journey_environment,
    _preflight_staged_runtime,
    _wait_for_conversation_id,
    _wait_for_new_run,
    _wait_for_terminal_run,
)

CREATE_PROMPT = (
    "Call the publish_artifact tool right now with kind=dataset, "
    "media_type=text/csv, title=forecast, suggested_filename=forecast.csv, "
    "presentation_preference=canvas, and content set to a CSV whose header row "
    "is exactly month,region,bookings,forecast followed by three data rows with "
    "integer values. Do not ask questions. Do not write any local file, stage "
    "any effect, browse, or use connectors."
)

FOLLOW_UP_PROMPT = (
    "In one short sentence, and using no tools at all, what does the region "
    "column mean?"
)


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "G2C", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def _canvas_state(session: DriverSession) -> dict:
    raw = session.evaluate(
        "(function(){"
        "var p=document.querySelector('[data-testid=canvas-lifecycle-panel]');"
        "return JSON.stringify({"
        "frame:!!document.querySelector('[data-testid=artifact-frame]'),"
        "table:!!document.querySelector('[data-testid=artifact-dataset-renderer]'),"
        "emptyState:p?p.getAttribute('data-lifecycle'):null,"
        "tabs:Array.from(document.querySelectorAll('[data-testid=tc-tabs] [role=tab]'))"
        ".map(function(t){return (t.textContent||'').trim();})"
        "});})()"
    )
    return json.loads(str(raw))


def _assert_canvas_shows_the_dataset(state: dict, *, when: str) -> None:
    assert state["frame"], f"{when}: Studio showed no artifact frame"
    assert state["table"], f"{when}: Studio showed no dataset table"
    assert state["emptyState"] is None, (
        f"{when}: the canvas empty state ({state['emptyState']!r}) was showing "
        "instead of the dataset"
    )


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"canvas survives follow-up; provider={provider}")
    with _journey_environment():
        session = DriverSession(name="generative-workflows-g2c-canvas-survives")
        completed = False
        try:
            with session:
                session.sign_in_local()
                session.ftue_add_key(provider, key)

                # --- turn 1: publish -------------------------------------
                session.send_first_run_message(CREATE_PROMPT)
                conversation_id = _wait_for_conversation_id(session)
                run_one = _wait_for_new_run(session, conversation_id, 0)
                _wait_for_terminal_run(session, run_one)
                assert session.wait_for(
                    "[data-testid=artifact-dataset-renderer]", 60
                ), (
                    "turn 1 never rendered the dataset — PRD-02 cannot be "
                    "assessed until the artifact reaches the canvas at all"
                )
                _assert_canvas_shows_the_dataset(_canvas_state(session), when="turn 1")
                session.shot("g2c-turn1-dataset-open")

                # --- turn 2: a plain answer ------------------------------
                session.fill("[data-testid=composer-textarea]", FOLLOW_UP_PROMPT)
                session.click('button[aria-label="Send message"]')
                run_two = _wait_for_new_run(session, conversation_id, 1)
                assert run_two != run_one, "turn 2 did not bind a new run"
                _wait_for_terminal_run(session, run_two)

                # The defect: the tab vanishes here.
                after = _canvas_state(session)
                _assert_canvas_shows_the_dataset(after, when="after the follow-up")
                session.shot("g2c-turn2-dataset-still-open")

                # --- the run's own ledger stays honest --------------------
                # Turn 2 produced no artifact; identity widened, run state did not.
                turn_two_events = {
                    e.get("event_type") for e in _events(session, run_two)
                }
                assert "artifact.created" not in turn_two_events, (
                    "turn 2 unexpectedly produced an artifact; this journey no "
                    "longer tests a chat-only follow-up"
                )

                # --- the server agrees ------------------------------------
                canvas = session.transport(
                    "GET", f"/v1/agent/conversations/{conversation_id}/canvas"
                )
                subjects = canvas.get("subjects", [])
                assert subjects, "conversation canvas returned no subjects"
                assert any(s.get("run_id") == run_one for s in subjects), (
                    "the subject was not attributed to the run that produced it"
                )
                completed = True
        finally:
            _assert_no_plaintext_secret(
                key,
                (session.run_dir, session._user_data_dir),
            )

    if completed:
        _result("passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
