#!/usr/bin/env python3
"""G2D — the two artifact-editing defects reported from live use, reproduced.

Both were fixed on unit-test evidence alone. This drives the packaged app the
way the reporter did and asserts the FACADE TRUTH, not the DOM's opinion.

BUG 1 — "Save patched revision" returned 409 and the surface claimed "A newer
revision exists" when the artifact had exactly one revision. Root cause: a user
edit had to borrow a run to be causal, and the run on screen is already sealed
by the time a table is visible. The regression only appears once the run is
TERMINAL, so this journey deliberately waits for that before editing.

BUG 2 — "add one more row" minted a SECOND artifact and a second canvas tab,
because publish was the only verb the model had. The fix added revise, so the
proof is that the conversation still holds exactly one dataset artifact whose
revision advanced.

Artifact-only: no workspace grant, no folder picker, no filesystem effect.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession
from g2_csv_lifecycle import (
    CREATE_PROMPT,
    PreflightSkip,
    _artifact_detail,
    _assert_dataset_surface,
    _assert_no_plaintext_secret,
    _byok_provider,
    _dataset_artifact_from_run,
    _events,
    _journey_environment,
    _open_artifact_from_sources,
    _preflight_staged_runtime,
    _wait_for_conversation_id,
    _wait_for_new_run,
    _wait_for_terminal_run,
)

ADD_ROW_PROMPT = "Add one more row to that CSV. Keep the same columns."

# The exact counterfactual string the surface used to show. Its presence after
# a save is the reported bug, so it is asserted against by name.
STALE_CLAIM = "A newer revision exists"

CELL = ".ui-dataset-table--editable tbody input.ui-dataset-cell-input"
SAVE = '[aria-label="Dataset revision actions"] button.ui-button--primary'


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "G2D", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def _current_revision(session: DriverSession, artifact_id: str) -> int:
    detail = _artifact_detail(session, artifact_id)
    artifact = detail.get("artifact") if isinstance(detail, dict) else None
    assert isinstance(artifact, dict), f"no artifact record for {artifact_id}"
    revision = artifact.get("current_revision")
    assert isinstance(revision, int), f"no current_revision on {artifact_id}"
    return revision


def _dataset_artifact_ids(session: DriverSession, conversation_id: str) -> set[str]:
    """Every dataset artifact the conversation canvas holds.

    Read from the conversation canvas rather than one run's events, because the
    duplicate in BUG 2 was produced by a LATER run than the original.
    """
    canvas = session.transport(
        "GET", f"/v1/agent/conversations/{conversation_id}/canvas"
    )
    subjects = canvas.get("subjects", []) if isinstance(canvas, dict) else []
    return {
        subject["subject_id"]
        for subject in subjects
        if isinstance(subject, dict)
        and subject.get("kind") == "artifact"
        and str(subject.get("renderer_hint", "")).endswith("dataset")
    }


def _wait_for_revision(
    session: DriverSession, artifact_id: str, at_least: int, timeout_s: int = 30
) -> int:
    """Poll the facade until the artifact reaches ``at_least``, or give up.

    Polling rather than sleeping keeps a slow machine from being reported as a
    regression, and returns the revision actually observed so the caller can
    assert on a real number rather than a timeout.
    """
    seen = 0
    for _ in range(timeout_s * 2):
        seen = _current_revision(session, artifact_id)
        if seen >= at_least:
            return seen
        time.sleep(0.5)
    return seen


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"artifact-edit regressions; provider={provider}")
    with _journey_environment():
        session = DriverSession(name="generative-workflows-g2d-artifact-edit")
        completed = False
        try:
            with session:
                assert session.rpc("status").get("posture") == "prod"
                session.sign_in_local()
                session.ftue_add_key(provider, key)

                # --- create a dataset artifact, then let the run SEAL ---
                session.send_first_run_message(CREATE_PROMPT)
                conversation_id = _wait_for_conversation_id(session)
                run_id = _wait_for_new_run(session, conversation_id, 0)
                _wait_for_terminal_run(session, run_id)
                artifact = _dataset_artifact_from_run(_events(session, run_id))
                assert _current_revision(session, artifact.artifact_id) == 1

                _open_artifact_from_sources(session)
                _assert_dataset_surface(session)
                assert session.wait_for(CELL), "no editable dataset cell"

                # --- BUG 1: save a cell edit while the run is terminal ---
                session.fill(CELL, "edited-by-journey")
                session.shot("g2d-cell-edited")
                session.click(SAVE)

                revision = _wait_for_revision(session, artifact.artifact_id, 2)
                session.shot("g2d-after-save")
                page_text = session.evaluate("document.body.innerText") or ""
                assert STALE_CLAIM not in page_text, (
                    "BUG 1 REGRESSED: the surface claimed "
                    f"{STALE_CLAIM!r} after a save on a terminal run"
                )
                assert revision >= 2, (
                    "BUG 1 REGRESSED: saving a cell edit on a completed run did "
                    f"not append a revision (still at r{revision})"
                )

                # --- BUG 2: ask for another row; revise, do not re-publish ---
                before = _dataset_artifact_ids(session, conversation_id)
                assert artifact.artifact_id in before
                # Back to Chat first: `_open_artifact_from_sources` left the rail
                # on Sources, where the composer is not mounted at all, so the
                # fill below fails on a missing selector rather than on anything
                # about the product.
                session.click('[role=tab]:has-text("Chat")')
                assert session.wait_for("[data-testid=composer-textarea]"), (
                    "returning to the Chat tab did not mount the composer"
                )
                session.fill("[data-testid=composer-textarea]", ADD_ROW_PROMPT)
                session.press("[data-testid=composer-textarea]", "Enter")
                added_run = _wait_for_new_run(session, conversation_id, 1)
                _wait_for_terminal_run(session, added_run)

                after = _dataset_artifact_ids(session, conversation_id)
                extra = after - before
                assert not extra, (
                    "BUG 2 REGRESSED: adding a row minted a second dataset "
                    f"artifact ({sorted(extra)}) instead of revising"
                )
                revised = _wait_for_revision(
                    session, artifact.artifact_id, revision + 1
                )
                assert revised > revision, (
                    "BUG 2 REGRESSED: the artifact did not gain a revision "
                    f"(still r{revised}); the model did not revise it"
                )
                session.shot("g2d-after-add-row")
                completed = True
        finally:
            _assert_no_plaintext_secret(key, (session.run_dir, session._user_data_dir))

    if completed:
        _result("passed")
        return 0
    _result("failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
