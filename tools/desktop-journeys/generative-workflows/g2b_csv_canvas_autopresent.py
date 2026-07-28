#!/usr/bin/env python3
"""G2B — the Studio canvas presents a model-created CSV *on its own*.

G2A covers the same artifact but reaches it via ``_open_artifact_from_sources``:
it clicks into the Sources rail and opens the artifact by hand. That is a real
path, but it is not the path a user takes. The reported failure was simply
"finish a run, look at Studio, the table is not there" — and G2A passed
throughout, because clicking Sources routes around the defect.

Root cause (fixed by ``agent_runtime.api.ledger_seal``): ``publish_artifact``
committed its outbox rows mid-run, but the rows only became ledger events when
a worker next called ``claim_next``. With the Desktop app's in-process worker
that is necessarily after the run it was executing, so ``artifact.created`` and
``artifact.presentation_decided`` landed after ``run_completed``, the SSE stream
had already closed, and the canvas honestly reported "no artifact was created".

So this journey asserts the two things G2A does not:

1. the artifact's ledger events precede the run's terminal event, and
2. the canvas renders the dataset with **no Sources click and no navigation** —
   the run ends and the table is simply there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession
from g2_csv_lifecycle import (
    CREATE_PROMPT,
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

_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled", "run_rejected"}


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "G2B", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def _assert_artifact_precedes_the_seal(events: list[dict]) -> None:
    """The ledger half: causal artifact facts live inside the sealed prefix."""

    ordered = sorted(events, key=lambda e: e.get("sequence_no", 0))
    names = [(e.get("sequence_no"), e.get("event_type")) for e in ordered]
    seal = next(
        (seq for seq, name in names if name in _TERMINAL_EVENTS),
        None,
    )
    assert seal is not None, f"run never sealed: {names}"
    for event_type in ("artifact.created", "artifact.presentation_decided"):
        seq = next((seq for seq, name in names if name == event_type), None)
        assert seq is not None, f"{event_type} missing from the run ledger: {names}"
        assert seq < seal, (
            f"{event_type} landed at {seq}, after the seal at {seal} — "
            "no live client can receive it"
        )
    # Nothing at all may follow the seal on the green path.
    assert names[-1][1] in _TERMINAL_EVENTS, (
        f"events appended after the seal: {[n for n in names if n[0] > seal]}"
    )


def _assert_canvas_presents_without_navigation(session: DriverSession) -> None:
    """The UI half: the table is on screen with no click of any kind."""

    assert session.wait_for("[data-testid=artifact-frame]", 60), (
        "Studio never presented an artifact frame after the run completed"
    )
    assert session.wait_for("[data-testid=artifact-dataset-renderer]", 60), (
        "Studio presented no dataset table for the published CSV"
    )
    # The regression's exact signature: the canvas' terminal narrative-only
    # empty state must NOT be what the user is looking at.
    panel = session.evaluate(
        'document.querySelector("[data-testid=canvas-lifecycle-panel]")'
        "?.getAttribute(\"data-lifecycle\") ?? 'absent'"
    )
    assert panel in ("absent", "presenting"), (
        f"canvas showed the {panel!r} empty state instead of the dataset"
    )


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"canvas auto-present; provider={provider}")
    with _journey_environment():
        session = DriverSession(name="generative-workflows-g2b-csv-canvas-autopresent")
        completed = False
        try:
            with session:
                status = session.rpc("status")
                assert status.get("target") == "source"
                assert status.get("posture") == "prod"

                session.sign_in_local()
                session.ftue_add_key(provider, key)

                session.send_first_run_message(CREATE_PROMPT)
                conversation_id = _wait_for_conversation_id(session)
                run_id = _wait_for_new_run(session, conversation_id, 0)
                _wait_for_terminal_run(session, run_id)

                _assert_artifact_precedes_the_seal(_events(session, run_id))
                # Deliberately no _open_artifact_from_sources() here.
                _assert_canvas_presents_without_navigation(session)
                session.shot("g2b-canvas-auto-presented-csv")
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
