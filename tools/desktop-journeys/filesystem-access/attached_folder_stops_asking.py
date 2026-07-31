#!/usr/bin/env python3
"""FS3 — in ENFORCE, an ATTACHED folder must stop asking; an ungranted one must not.

FS1 already proves the second half: a folder nobody attached is never answered
with a confident empty listing. It sets ``WORKSPACE_EFFECT_MODE=enforce``, and
that turned out to be a lane where the FIRST half was silently untrue.

The defect this journey exists for
----------------------------------
``run.py`` branches on ``workspace_effect_mode``. In ENFORCE the ``/workspace/``
object is C3's ``WorkspaceGatewayBackend`` / ``WorkspaceTombstoneBackend``, and
the runtime factory read the run's granted roots OFF THAT OBJECT. Neither can
name a host root — their host-session projection is deliberately path-free, and
that channel is C2's private WRITE bootstrap, not something to widen — so no
``allow`` rule was ever built and EVERY read of a folder the user had explicitly
attached raised a consent card again. Attaching a folder bought the user nothing
in this lane, and nothing on screen or in any packaged log said so.

Why this has to be live
-----------------------
The unit suite could not see it: the grants reach the rules through the
capability broker, which only exists inside the running app, and the mode that
selects the broken object is chosen from the supervised child's environment. A
green suite over a dead lane is precisely the failure this program keeps
repeating, so the claim is settled by granting a real folder through the real
native picker and watching the real agent read it.

What is asserted, and what is deliberately not
----------------------------------------------
* READ of a file inside the ATTACHED folder → the model returns the canary
  contents, and NO workspace-grant approval is raised for it.
* READ of a file inside a NEVER-attached sibling → the canary contents must not
  come back. Parking on a consent card is the pass.

Only ``read_file`` is claimed. ``ls`` / ``glob`` / ``grep`` take deepagents'
BULK interrupt predicate, which fires on any overlap with an interrupt anchor —
and the catch-all read rule is anchored at ``/``. Those ask whether or not a
folder is attached, by design (``capabilities/desktop/host_floor.py``), so
asserting "a listing does not prompt" would be asserting a bug.

Privacy: both directories are journey-owned temporary fixtures with random
canary names. Nothing under the user's real home is read, and no host path is
printed beyond the fixtures this process created.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generative-workflows"))
from g2_csv_lifecycle import (  # noqa: E402
    PreflightSkip,
    _byok_provider,
    _capability_invoke,
    _desktop_process_id,
    _events,
    _folder_picker_command,
    _preflight_staged_runtime,
    _runs_for_conversation,
    _start_native_automation,
    _wait_for_conversation_id,
    _wait_for_new_run,
    _wait_for_terminal_run,
    _wait_native_automation,
)

from ungranted_path_asks import _journey_environment  # noqa: E402

#: The payload key that turns any interrupt into a folder ask. Mirrors
#: ``WORKSPACE_GRANT_PAYLOAD_KEY`` in
#: ``packages/chat-surface/src/approvals/presentation.ts``; Python cannot import
#: it, and a drifted key would make this journey blind to every consent card.
GRANT_BLOCK_KEY: Final = "workspace_grant"


@contextmanager
def _fixtures() -> Iterator[tuple[Path, Path, str, str]]:
    """One folder to attach and one never to, each holding its own canary."""

    nonce = uuid.uuid4().hex[:12]
    with (
        tempfile.TemporaryDirectory(prefix="fs3-attached-") as attached_raw,
        tempfile.TemporaryDirectory(prefix="fs3-ungranted-") as ungranted_raw,
    ):
        attached = Path(attached_raw).resolve()
        ungranted = Path(ungranted_raw).resolve()
        attached_canary = f"fs3-attached-{nonce}"
        ungranted_canary = f"fs3-ungranted-{nonce}"
        (attached / "canary.txt").write_text(attached_canary, encoding="utf-8")
        (ungranted / "canary.txt").write_text(ungranted_canary, encoding="utf-8")
        yield attached, ungranted, attached_canary, ungranted_canary


def _result(outcome: str, **extra: Any) -> None:
    print(
        json.dumps({"journey": "FS3", "outcome": outcome, **extra}, sort_keys=True),
        flush=True,
    )


def _attach(session: DriverSession, root: Path) -> str:
    """Attach ``root`` exactly as a user does: the real native folder picker.

    There is no other way in, and that is the design — ``CapabilityService``
    mints a grant only from the picker's own realpath, so no caller (including
    this journey) can name the folder it wants. The AppleScript only types an
    already-created fixture path into the sheet the app itself opened.
    """

    picker = _start_native_automation(
        _folder_picker_command(root, _desktop_process_id(session))
    )
    try:
        grant = _capability_invoke(
            session,
            "capability.request-folder-grant",
            {"mode": "read_only", "label": "FS3 fixture"},
        )
    finally:
        _wait_native_automation(picker, action="folder picker")

    assert isinstance(grant, dict), "folder picker did not yield a renderer grant"
    assert set(grant) == {"grantId", "mode", "label", "status"}, (
        "folder grant leaked an authority field or changed the renderer-safe shape"
    )
    assert grant.get("status") == "active", "attached folder was not granted"
    grant_id = grant.get("grantId")
    assert isinstance(grant_id, str) and grant_id, "folder grant omitted its id"
    return grant_id


def _final_text(session: DriverSession, run_id: str) -> str:
    """The assistant's visible answer, lowercased for phrase matching."""

    chunks: list[str] = []
    for event in _events(session, run_id):
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for key in ("text", "content", "message", "final_response", "delta"):
            value = payload.get(key)
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(chunks).lower()


def _grant_asks(events: list[dict]) -> list[dict[str, Any]]:
    """Every consent card that asked for a host folder in this run."""

    asks: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if "approval" in name and GRANT_BLOCK_KEY in payload:
            asks.append({"event": name, "kind": payload.get("approval_kind")})
    return asks


def _tool_names(events: list[dict]) -> list[str]:
    """Which tools the turn actually called — what makes a failure diagnosable.

    ``ls``/``glob``/``grep`` ask by design, so a turn that chose to list before
    reading explains a consent card that is not the defect.
    """

    names: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event_type") or event.get("type") or "")
        if "tool" not in name:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        tool = payload.get("tool_name") or payload.get("name")
        if isinstance(tool, str) and tool not in names:
            names.append(tool)
    return names


def _read_prompt(path: Path) -> str:
    """One turn that reads ONE exact path.

    "Do not list" is load-bearing rather than tidy: a listing takes deepagents'
    BULK interrupt predicate and asks whether or not the folder is attached, so
    a turn that lists first would raise a consent card this journey has no
    business calling a regression.
    """

    return (
        f"Read the file {path} and reply with its exact contents and nothing "
        "else. Read that exact path directly — do not list the directory, do "
        "not search, and do not guess."
    )


def _turn(
    session: DriverSession, prompt: str, *, conversation_id: str | None
) -> tuple[str, str, list[dict]]:
    """Send one turn; return ``(conversation_id, run_id, events)`` once it settles.

    A run that correctly stops to ASK never becomes terminal, so a wait timeout
    is an OUTCOME here, not a failure — which turn was supposed to park is the
    caller's claim to make.
    """

    before = 0
    if conversation_id is not None:
        before = len(_runs_for_conversation(session, conversation_id))
    session.send_first_run_message(prompt)
    if conversation_id is None:
        conversation_id = _wait_for_conversation_id(session)
    run_id = _wait_for_new_run(session, conversation_id, before)
    try:
        _wait_for_terminal_run(session, run_id)
    except AssertionError:
        pass
    time.sleep(1.5)
    return conversation_id, run_id, _events(session, run_id)


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", reason=str(exc))
        return 0
    if sys.platform != "darwin":
        _result("skipped", reason="the native folder picker driver is macOS-only")
        return 0

    _result("running", provider=provider)

    with (
        _journey_environment(),
        _fixtures() as (
            attached,
            ungranted,
            attached_canary,
            ungranted_canary,
        ),
    ):
        session = DriverSession(name="filesystem-access-fs3-attached-folder")
        evidence: dict[str, Any] = {
            "attached_root": str(attached),
            "ungranted_root": str(ungranted),
            "workspace_effect_mode": os.environ.get("WORKSPACE_EFFECT_MODE"),
        }
        try:
            with session:
                status = session.rpc("status")
                evidence["target"] = status.get("target")
                evidence["posture"] = status.get("posture")

                session.sign_in_local()
                session.ftue_add_key(provider, key)
                session.shot("fs3-01-key-added")

                evidence["grant_id"] = _attach(session, attached)
                session.shot("fs3-02-folder-attached")

                # 1. The attached folder. A file read inside it must not ask.
                conversation_id, run_id, events = _turn(
                    session,
                    _read_prompt(attached / "canary.txt"),
                    conversation_id=None,
                )
                evidence["attached_run_id"] = run_id
                evidence["attached_asks"] = _grant_asks(events)
                evidence["attached_tools"] = _tool_names(events)
                answer = _final_text(session, run_id)
                evidence["attached_canary_returned"] = attached_canary in answer
                session.shot("fs3-03-attached-read")

                # 2. The folder nobody attached. Same shape, opposite outcome.
                _conversation_id, run_id, events = _turn(
                    session,
                    _read_prompt(ungranted / "canary.txt"),
                    conversation_id=conversation_id,
                )
                evidence["ungranted_run_id"] = run_id
                evidence["ungranted_asks"] = _grant_asks(events)
                evidence["ungranted_tools"] = _tool_names(events)
                answer = _final_text(session, run_id)
                evidence["ungranted_canary_returned"] = ungranted_canary in answer
                session.shot("fs3-04-ungranted-read")
        finally:
            out = session.run_dir / "fs3-evidence.json"
            out.write_text(
                json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"[fs3] evidence -> {out}", flush=True)
            print(f"[fs3] shots    -> {session.run_dir}", flush=True)

    failures: list[str] = []
    # THE regression: attaching a folder must actually buy something.
    if evidence.get("attached_asks"):
        failures.append("an ATTACHED folder still raised a grant request")
    if not evidence.get("attached_canary_returned"):
        failures.append("an ATTACHED file did not come back readable")
    # ...and it must buy exactly one folder, not the disk.
    if evidence.get("ungranted_canary_returned"):
        failures.append("an UNGRANTED file was read without consent")

    if failures:
        _result(
            "FAILED",
            reasons=failures,
            attached_run_id=evidence.get("attached_run_id"),
            ungranted_run_id=evidence.get("ungranted_run_id"),
        )
        return 1
    _result(
        "passed",
        attached_asks=len(evidence.get("attached_asks") or []),
        ungranted_asks=len(evidence.get("ungranted_asks") or []),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
