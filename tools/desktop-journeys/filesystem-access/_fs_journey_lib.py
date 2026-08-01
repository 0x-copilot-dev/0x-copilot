#!/usr/bin/env python3
"""Shared pieces for the FS live journeys that drive the REAL packaged app.

Everything here is a thin wrapper over ``_lib.DriverSession`` — no service is
ever called directly, so a green journey proves the shipped wiring.

The one thing worth reading before reusing: :func:`transport_json` posts a BODY
through the app's own authenticated transport, which ``_lib.transport`` cannot.
That is how a journey reads a run's full event payloads (not the summarised
shapes the older scripts keep) and how it can assert on WHO decided an approval
rather than merely that one was resolved.
"""

from __future__ import annotations

import json
import os
import sys
import time
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
    _folder_picker_command,
    _preflight_staged_runtime,
    _start_native_automation,
    _wait_native_automation,
)

SECRET_ENVIRONMENT_NAMES: Final = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
)

#: Every flag the FS journeys ever touch. Listed once so the context manager can
#: restore the process env exactly, whichever lane a journey selected.
_LANE_NAMES: Final = (
    "SURFACES_V2",
    "ARTIFACT_EFFECTS_V2",
    "ARTIFACT_DRAFTS_V2",
    "OPERATION_GATEWAY_MODE",
    "WORKSPACE_EFFECT_MODE",
    "RUNTIME_ENABLE_DESKTOP_FILESYSTEM",
)

#: The lane a real desktop user gets. `service-env.ts` defaults
#: OPERATION_GATEWAY_MODE to "off", and WORKSPACE_EFFECT_MODE follows it, so
#: setting NOTHING is what reproduces the shipped posture. A journey that sets
#: `enforce` is testing an operator opt-in, not the default install.
DEFAULT_LANE: Final[dict[str, str]] = {}

#: The operator opt-in the older FS journeys pinned.
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


def wait_for_conversation_id(session: DriverSession, timeout_s: int = 90) -> str:
    import re

    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = str(session.evaluate("window.location.hash") or "")
        match = re.fullmatch(r"#/convo/([^/?#]+)(?:[?#].*)?", last)
        if match is not None:
            return match.group(1)
        time.sleep(0.25)
    raise AssertionError(f"no conversation route bound; last hash was {last!r}")


def wait_for_new_run(
    session: DriverSession, conversation_id: str, before: int, timeout_s: int = 150
) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        runs = runs_for(session, conversation_id)
        if len(runs) > before:
            run_id = runs[0].get("run_id")
            assert isinstance(run_id, str) and run_id
            return run_id
        time.sleep(0.5)
    raise AssertionError("no run was created for the prompt")


#: Every status a run can END on. ``timed_out`` belongs here: leaving it out
#: makes a timed-out run look like one still working, and every waiter then
#: burns its whole budget before reporting the wrong thing.
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

    picker = _start_native_automation(
        _folder_picker_command(root, _desktop_process_id(session))
    )
    try:
        grant = _capability_invoke(
            session,
            "capability.request-folder-grant",
            {"mode": mode, "label": label},
        )
    finally:
        _wait_native_automation(picker, action="folder picker")
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


__all__ = (
    "DEFAULT_LANE",
    "ENFORCE_LANE",
    "TERMINAL",
    "DriverSession",
    "PreflightSkip",
    "_byok_provider",
    "_preflight_staged_runtime",
    "approval_events",
    "assistant_text",
    "attach_folder",
    "dump",
    "event_name",
    "events",
    "lane",
    "payload_of",
    "result",
    "run_status",
    "runs_for",
    "settle_run",
    "tool_calls",
    "transport_json",
    "wait_for_conversation_id",
    "wait_for_new_run",
    "wait_until",
)
