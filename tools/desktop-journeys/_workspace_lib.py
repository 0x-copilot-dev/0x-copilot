#!/usr/bin/env python3
"""Shared helpers for the workspace/filesystem-consent journeys.

Formerly `filesystem-access/_fs_journey_lib.py`, which additionally reached
sideways into `generative-workflows/g2_csv_lifecycle` for the macOS
native-dialog automation. Both of those directories are gone; a helper module
that three merged journeys depend on now lives at the top level where they can
all see it.

The lane constants are the important part. `service-env.ts` defaults
OPERATION_GATEWAY_MODE to "off" and lets WORKSPACE_EFFECT_MODE follow it, so
setting NOTHING is what reproduces the shipped posture. A journey that sets
`enforce` is testing an operator opt-in, not the default install — and the two
cannot share a boot, because both are read by the supervisor at launch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Iterator

from _lib import (
    SECRET_ENVIRONMENT_NAMES,
    DriverSession,
    PhaseSkipped,
    wait_for_conversation_id,
    wait_for_new_run,
)

__all__ = [
    "DEFAULT_LANE",
    "ENFORCE_LANE",
    "PhaseSkipped",
    "approval_events",
    "assistant_text",
    "attach_folder",
    "capability_invoke",
    "desktop_process_id",
    "dump",
    "event_name",
    "events",
    "folder_picker_command",
    "lane",
    "payload_of",
    "result",
    "run_status",
    "runs_for",
    "settle_run",
    "start_native_automation",
    "tool_calls",
    "transport_json",
    "wait_for_conversation_id",
    "wait_for_new_run",
    "wait_native_automation",
    "wait_until",
]


_FOLDER_PICKER_APPLESCRIPT: Final = r"""
on waitForSheet(processId, attempts)
  tell application "System Events"
    set targetProcess to first application process whose unix id is processId
    tell targetProcess
      repeat with attempt from 1 to attempts
        if exists sheet 1 of window 1 then return true
        delay 0.1
      end repeat
    end tell
  end tell
  return false
end waitForSheet

on run argv
  set fixtureRoot to item 1 of argv
  set processId to (item 2 of argv) as integer
  if my waitForSheet(processId, 200) is false then error "folder picker did not appear"
  tell application "System Events"
    set targetProcess to first application process whose unix id is processId
    tell targetProcess
      set frontmost to true
      keystroke "g" using {command down, shift down}
      delay 0.2
      keystroke fixtureRoot
      key code 36
      delay 0.4
      key code 36
    end tell
  end tell
end run
"""


def _evaluate_json(session: DriverSession, javascript: str) -> Any:
    raw = session.evaluate(javascript)
    assert isinstance(raw, str), "renderer IPC did not return JSON"
    if raw.startswith("ERR:"):
        raise AssertionError("renderer IPC rejected the journey action")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError("renderer IPC returned malformed JSON") from exc


_LANE_NAMES: Final = (
    "SURFACES_V2",
    "ARTIFACT_EFFECTS_V2",
    "ARTIFACT_DRAFTS_V2",
    "OPERATION_GATEWAY_MODE",
    "WORKSPACE_EFFECT_MODE",
    "RUNTIME_ENABLE_DESKTOP_FILESYSTEM",
)


DEFAULT_LANE: Final[dict[str, str]] = {}


ENFORCE_LANE: Final[dict[str, str]] = {
    "OPERATION_GATEWAY_MODE": "enforce",
    "WORKSPACE_EFFECT_MODE": "enforce",
}


@contextmanager
def lane(overrides: dict[str, str] | None = None) -> Iterator[None]:
    """Run a journey in a named lane, with no plaintext provider key inherited.

    Unset means unset: the desktop supervisor's own default is the thing under
    test, so this clears every flag it does not explicitly set rather than
    leaving whatever the caller's shell happened to export.
    """

    previous = {name: os.environ.get(name) for name in _LANE_NAMES}
    previous_secrets = {name: os.environ.get(name) for name in SECRET_ENVIRONMENT_NAMES}
    for name in _LANE_NAMES:
        os.environ.pop(name, None)
    os.environ.update(overrides or {})
    for name in SECRET_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in {**previous, **previous_secrets}.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def transport_json(
    session: DriverSession,
    method: str,
    path: str,
    body: Any | None = None,
) -> Any:
    """An authenticated facade call WITH a body, made through the running app."""

    request: dict[str, Any] = {"method": method, "path": path}
    if body is not None:
        request["body"] = body
    javascript = (
        '(async()=>{try{const r=await window.bridge.ipc.invoke("transport.request",'
        f"{json.dumps(request)});"
        'if(r&&r.kind==="transport-result"){'
        'if(!r.ok)return "ERR:HTTP "+String(r.error?.status??"unknown")+" "+'
        'String(r.error?.message??"request failed");'
        "return JSON.stringify(r.value ?? null);}"
        "return JSON.stringify(r);}"
        'catch(e){return "ERR:"+e.message}})()'
    )
    raw = session.evaluate(javascript)
    if isinstance(raw, str) and raw.startswith("ERR:"):
        raise RuntimeError(raw)
    return json.loads(raw)


def events(session: DriverSession, run_id: str) -> list[dict[str, Any]]:
    """Full event replay — payloads intact, not summarised."""

    replay = transport_json(session, "GET", f"/v1/agent/runs/{run_id}/events")
    found = replay.get("events", []) if isinstance(replay, dict) else []
    return [event for event in found if isinstance(event, dict)]


def event_name(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or "")


def payload_of(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def runs_for(session: DriverSession, conversation_id: str) -> list[dict[str, Any]]:
    listing = transport_json(
        session, "GET", f"/v1/agent/conversations/{conversation_id}/runs"
    )
    found = listing.get("runs", []) if isinstance(listing, dict) else []
    return [run for run in found if isinstance(run, dict)]


TERMINAL: Final = {"completed", "failed", "cancelled", "expired", "timed_out"}


def run_status(session: DriverSession, run_id: str) -> dict[str, Any]:
    result = transport_json(session, "GET", f"/v1/agent/runs/{run_id}")
    return result if isinstance(result, dict) else {}


def wait_until(predicate, timeout_s: int = 180, interval_s: float = 1.0) -> bool:  # noqa: ANN001
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def settle_run(
    session: DriverSession, run_id: str, timeout_s: int = 200
) -> dict[str, Any]:
    """Wait until the run is terminal OR parked on an approval; report which.

    Parked is a legitimate OUTCOME, never a harness failure — a run that
    correctly stops to ask never becomes terminal.
    """

    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = run_status(session, run_id)
        status = last.get("status")
        if status in TERMINAL:
            return last
        if status == "waiting_for_approval":
            # Give the run a beat to move on if something resolves it, but
            # report parked rather than burning the whole budget.
            time.sleep(5)
            again = run_status(session, run_id)
            if again.get("status") == "waiting_for_approval":
                return again
            last = again
            continue
        time.sleep(1)
    return last


def assistant_text(session: DriverSession, run_id: str) -> str:
    """What the user actually reads — the final response, not every delta."""

    chunks: list[str] = []
    for event in events(session, run_id):
        name = event_name(event)
        if name not in {"final_response", "message_completed"}:
            continue
        payload = payload_of(event)
        for key in ("text", "content", "message", "final_response"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                chunks.append(value)
    return "\n".join(chunks)


def approval_events(session_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every approval event with its FULL payload — decision authority included."""

    out: list[dict[str, Any]] = []
    for event in session_events:
        name = event_name(event)
        if "approval" not in name:
            continue
        out.append({"event": name, "payload": payload_of(event)})
    return out


def tool_calls(session_events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in session_events:
        if "tool" not in event_name(event):
            continue
        payload = payload_of(event)
        tool = payload.get("tool_name") or payload.get("name")
        if isinstance(tool, str) and tool not in names:
            names.append(tool)
    return names


def attach_folder(session: DriverSession, root: Path, *, mode: str, label: str) -> str:
    """Attach ``root`` through the app's REAL native folder picker.

    There is no other way in: ``CapabilityService`` mints a grant only from the
    picker's own realpath, so this cannot name a folder the user did not choose.
    The AppleScript types an already-created fixture path into the sheet the app
    itself opened.
    """

    picker = start_native_automation(
        folder_picker_command(root, desktop_process_id(session))
    )
    try:
        grant = capability_invoke(
            session,
            "capability.request-folder-grant",
            {"mode": mode, "label": label},
        )
    finally:
        wait_native_automation(picker, action="folder picker")
    assert isinstance(grant, dict), "the folder picker did not yield a grant"
    assert grant.get("status") == "active", f"folder grant was not active: {grant}"
    grant_id = grant.get("grantId")
    assert isinstance(grant_id, str) and grant_id
    return grant_id


def dump(run_dir: Path, name: str, value: Any) -> Path:
    out = run_dir / name
    out.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    return out


def result(journey: str, outcome: str, **extra: Any) -> None:
    print(
        json.dumps({"journey": journey, "outcome": outcome, **extra}, sort_keys=True),
        flush=True,
    )


def folder_picker_command(root: Path, process_id: int) -> list[str]:
    return [
        "/usr/bin/osascript",
        "-e",
        _FOLDER_PICKER_APPLESCRIPT,
        "--",
        str(root),
        str(process_id),
    ]


def start_native_automation(command: list[str]) -> subprocess.Popen[bytes]:
    if sys.platform != "darwin":
        raise AssertionError("G2 native workspace approval requires macOS")
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_native_automation(
    process: subprocess.Popen[bytes], *, action: str, timeout_s: int = 30
) -> None:
    try:
        exit_code = process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError(f"native {action} automation timed out") from exc
    if exit_code != 0:
        raise AssertionError(
            f"native {action} automation failed; "
            "no folder grant or approval was assumed"
        )


def capability_invoke(
    session: DriverSession, channel: str, payload: Mapping[str, Any]
) -> Any:
    return _evaluate_json(
        session,
        "(async()=>{try{const value=await window.bridge.ipc.invoke("
        f"{json.dumps(channel)},{json.dumps(dict(payload))});"
        "return JSON.stringify(value);}catch(error){return 'ERR:'+error.message;}})()",
    )


def desktop_process_id(session: DriverSession) -> int:
    status = session.rpc("status")
    process_id = status.get("pid")
    assert isinstance(process_id, int) and process_id > 0, (
        "desktop driver did not expose its launched Electron process id"
    )
    return process_id
