#!/usr/bin/env python3
"""One-off: prove a Linear WRITE executes end to end under bypass posture.

Everything else in this program deliberately never writes. This file is the
exception, and it exists because the write half of the per-tool MCP path had
never once been seen to complete: the gate journey proves a write PARKS and is
refused, and nothing proved that an allowed write actually reaches Linear and
comes back with the created record.

Run only with the workspace owner's explicit say-so for that run. It creates a
real issue in a real workspace and does not delete it — the created identifier
is the evidence, so removing it would remove the proof.

Bypass is the mechanism under test, not a convenience: ``Posture.BYPASS`` lifts
every approval gate, so the write dispatches without a card. That is exactly the
path a user takes when they have turned approvals off, and it is the one no test
has ever exercised against a live connector.

The verdict is read from the RUN and its events — terminal status, the write
tool's own result payload — never from page text. Page text contains the prompt,
so a probe that greps the DOM for its own title reports success the instant the
message is sent, which is what the first draft of this file did.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "filesystem-access"))

from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    assistant_text,
    dump,
    event_name,
    events,
    lane,
    payload_of,
    result,
    transport_json,
    settle_run,
    tool_calls,
    wait_for_conversation_id,
    wait_for_new_run,
)
from jF_linear_mcp import bootstrap, _thread_baseline  # noqa: E402

JOURNEY = "bypass-write-probe"
SESSION_NAME = "fs-f-linear-mcp"
COMPOSER = "[data-testid=composer-textarea]"
BYPASS_PILL = ".atlas-bypass-pill"
CHATS_RAIL = '[data-destination="chats"]'
RUN_RAIL = '[data-destination="run"]'
NEW_CHAT = "[data-testid=chats-new-chat]"
WORKSPACE_DEFAULTS = "/v1/agent/workspace/defaults"

#: A title nobody would mistake for real work, and unique per run so the created
#: record can be found again without guessing.
TICKET_TITLE = f"copilot e2e write probe {int(time.time())}"

PROMPT = (
    "Using the connected Linear connector, create exactly ONE new issue in the "
    f'team "Parth-test" with the title "{TICKET_TITLE}". '
    "Do not create more than one. Do not modify, comment on, assign, close, "
    "archive or delete anything else. "
    "When it is created, report the issue's identifier and its URL exactly as "
    "Linear returned them. If the connector returns an error, quote the error "
    "verbatim and do not guess."
)

#: A created Linear issue comes back with these. Asserting on them, rather than
#: on ``status == completed``, is what stops an empty 200 reading as a write.
CREATED_MARKERS = ("identifier", "url", "linear.app")


def enable_master_switch(session: DriverSession) -> bool:
    """Turn on the workspace's bypass MASTER switch (PRD-FS-10 §4.3 tier 1).

    Without this the pill is *locked to Manual and its menu never renders* — so
    a probe that clicks the pill and reads ``manual`` back is not observing a
    refused bypass, it is observing a control that was never offered. Done as
    the host's own read-merge-PUT, because the defaults document is replaced
    whole and a bare patch would drop every other knob.
    """

    current = transport_json(session, "GET", WORKSPACE_DEFAULTS)
    overrides = dict(current.get("behavior_overrides") or {})
    overrides["filesystem_bypass_enabled"] = True
    updated = transport_json(
        session,
        "PUT",
        WORKSPACE_DEFAULTS,
        {
            "default_model": current.get("default_model"),
            "default_connectors": current.get("default_connectors"),
            "retention_days": current.get("retention_days"),
            "behavior_overrides": overrides,
            "enabled_models": current.get("enabled_models"),
        },
    )
    return bool(
        (updated.get("behavior_overrides") or {}).get("filesystem_bypass_enabled")
    )


def set_bypass(session: DriverSession) -> dict[str, Any]:
    """Choose Bypass in the pill's menu, BEFORE the message is sent.

    The pill is a menu button, not a toggle: clicking it only opens the popover,
    which is why the first draft read ``manual`` back and sent a MANUAL run.

    The desktop host mounts the pill without ``onScopeChange``, so the menu
    offers Manual/Bypass and no scope rows — the selection takes the safer
    ``message`` default, which is all this probe needs since it sends once.

    Posture is sealed at run start, so none of this would lift the gate if it
    ran after the send. ``pill_mode`` is therefore the gate on continuing: a
    ``manual`` reading here means the run about to be sent is not the run this
    probe claims to be measuring.
    """

    session.click(BYPASS_PILL)
    time.sleep(0.5)
    # Plain concatenation, not an f-string: an f-string doubles the JS braces
    # and Playwright receives a syntax error, not a script.
    rows = session.evaluate(
        "Array.from(document.querySelectorAll('[role=menuitemradio]'))"
        ".map(r => (r.innerText || '').trim().split('\\n')[0])"
    )
    clicked = bool(
        session.evaluate(
            "(() => { const rows = Array.from(document.querySelectorAll("
            "'[role=menuitemradio]'));"
            " const hit = rows.find(r => (r.innerText || '').trim()"
            ".startsWith('Bypass'));"
            " if (!hit) return false; hit.click(); return true; })()"
        )
    )
    time.sleep(0.5)
    mode = session.evaluate(
        "(() => { const p = document.querySelector('" + BYPASS_PILL + "');"
        " return p ? p.getAttribute('data-mode') : 'absent'; })()"
    )
    return {"menu_rows": rows, "bypass_row_clicked": clicked, "pill_mode": mode}


def write_evidence(stream: list[dict[str, Any]]) -> dict[str, Any]:
    """What the connector actually returned for the write call.

    ``tool_calls`` yields NAMES, so the payloads are pulled off the tool events
    directly: the point of this probe is the returned record, and a name alone
    cannot tell an executed write from an attempted one.

    The write events are found by the CONTENT they carry — this run's unique
    title, or a Linear issue URL — not by a guessed tool name. Linear's create
    tool is ``save_issue``, so the first cut of this looked for ``create`` in
    the name, found nothing, and reported a failure over a run that had just
    filed a real issue.
    """

    names = tool_calls(stream)
    writes: list[dict[str, Any]] = []
    for event in stream:
        if "tool" not in event_name(event):
            continue
        payload = payload_of(event)
        rendered = json.dumps(payload)
        if TICKET_TITLE in rendered or "linear.app/" in rendered:
            writes.append(payload)
    blob = json.dumps(writes)
    return {
        "tool_names": names,
        "write_events": len(writes),
        "write_payloads": [json.dumps(payload)[:1200] for payload in writes[:4]],
        "markers_present": [m for m in CREATED_MARKERS if m in blob],
    }


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    evidence: dict[str, Any] = {"title": TICKET_TITLE}
    with lane(DEFAULT_LANE):
        session = DriverSession(name=SESSION_NAME, fresh=False)
        try:
            with session:
                evidence["bootstrap"] = bootstrap(session, provider, key)
                session.wait_for(COMPOSER, 120)

                # Prefer a brand-new thread; fall back to the Run cockpit's
                # composer, which is where a reused profile lands.
                session.click(CHATS_RAIL)
                time.sleep(2)
                if session.present(NEW_CHAT):
                    session.click(NEW_CHAT)
                else:
                    session.click(RUN_RAIL)
                time.sleep(2)
                previous_id, before = _thread_baseline(session)
                assert session.wait_for(COMPOSER, 60), (
                    "no composer to send the write from"
                )

                evidence["master_switch_on"] = enable_master_switch(session)
                evidence["bypass"] = set_bypass(session)
                time.sleep(0.5)
                session.shot("w-01-bypass-set")

                session.send_first_run_message(PROMPT)
                conversation_id = wait_for_conversation_id(session)
                run_id = wait_for_new_run(
                    session,
                    conversation_id,
                    before if conversation_id == previous_id else 0,
                )
                evidence["conversation_id"] = conversation_id
                evidence["run_id"] = run_id

                final = settle_run(session, run_id, timeout_s=300)
                evidence["run_status"] = final.get("status")
                evidence["safe_error"] = final.get("safe_error")

                stream = events(session, run_id)
                evidence["write"] = write_evidence(stream)
                evidence["answer"] = assistant_text(session, run_id)[-2000:]
                time.sleep(2)
                session.shot("w-02-write-outcome")
        finally:
            out = dump(session.run_dir, "bypass-write-evidence.json", evidence)
            print(f"[{JOURNEY}] evidence -> {out}", flush=True)

    created = (
        bool(evidence.get("write", {}).get("markers_present"))
        and evidence.get("run_status") == "completed"
    )
    result(
        JOURNEY,
        "passed" if created else "failed",
        run_status=evidence.get("run_status"),
        create_events=evidence.get("write", {}).get("create_events"),
        markers=evidence.get("write", {}).get("markers_present"),
    )
    return 0 if created else 1


if __name__ == "__main__":
    raise SystemExit(main())
