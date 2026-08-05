#!/usr/bin/env python3
"""A parked write is reviewed and decided WITHOUT leaving the transcript.

The row used to be a one-line ask whose payload lived only on the Studio canvas,
so deciding a write meant changing modes. It expands in place now, and the claim
that needs live evidence is the one unit tests cannot make: that the audit
anchor printed in the expanded body is the REAL `ledgerId` off the run's ledger
gate — not a locally derived string that happens to look like one.

That distinction is the whole reason `ledgerId` is joined host-side rather than
computed from the approval: the ledger id anchors on the `gate.opened` event,
which carries a different `sequence_no` from the `approval_requested` the
transcript folds. A derived id would render, look plausible, and point at the
wrong ledger row. Only a live run has both numbers to compare.

SAFETY
======
The write is INERT by construction: the target id is a placeholder that cannot
resolve, and the prompt forbids searching for or substituting a real one. The
*intent* is unambiguously a write — which is what makes the PDP gate it — but
there is no reachable object to mutate. The journey also never approves; it
declines, which is the safe terminal state and the one that leaves a resolved
gate behind to assert against. Borrowed wholesale from CN-09.

PRECONDITION
============
A connector must already be connected in the profile this journey reuses. That
is not laziness: `driver.mjs` replaces `shell.openExternal` and only RECORDS the
url, so no journey can ever finish an OAuth consent. See
`tools/desktop-journeys/README.md`.

The obvious way around that — the checked-in local stdio fixture, which needs no
OAuth — does not work either: the facade cannot execute it yet, which
`g5_local_email_triage` declares and blocks on. The fixture server itself is
fine (it answers `tools/list` over stdio); the gap is facade-side.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import SOURCE_TARGET, DriverSession  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generative-workflows"))
from g2_csv_lifecycle import (  # noqa: E402
    PreflightSkip,
    _assert_no_plaintext_secret,
    _byok_provider,
    _journey_environment,
    _preflight_staged_runtime,
    _wait_for_conversation_id,
    _wait_for_new_run,
)

# The fixture registration is inlined rather than imported from
# `_g3_g10_support`: that module needs Python 3.11+ (`enum.StrEnum`) and this
# journey should not inherit an interpreter floor it has no other use for.
#: The DriverSession name IS the profile selector — `_lib` builds
#: `journey-<name>-reuse` when `fresh=False`. Shared with FS-F / CN-09 so a
#: connector connected by hand ONCE serves all three. A journey can never
#: complete an OAuth consent itself: the driver replaces `shell.openExternal`
#: and only records the URL, so the connect is a PRECONDITION, never a step.
SESSION_NAME = "fs-f-linear-mcp"
CONNECTOR_SLUG = os.environ.get("WRITE_GATE_CONNECTOR", "linear")


def _transport_body(session: DriverSession, method: str, path: str, body: dict):
    """An authenticated facade call WITH a body, made through the app.

    `_lib.DriverSession.transport` deliberately takes no body; the per-chat
    connector scope PATCH needs one, so this mirrors it rather than widening the
    shared helper for a single caller.
    """

    js = (
        "(async()=>{try{const r=await window.bridge.ipc.invoke('transport.request',"
        + json.dumps({"method": method, "path": path})[:-1]
        + ',"body":'
        + json.dumps(body)
        + "});"
        "if(r&&r.kind==='transport-result'){"
        "if(!r.ok)return 'ERR:HTTP '+String(r.error?.status??'unknown')+' '"
        "+String(r.error?.message??'');"
        "return JSON.stringify(r.value);}"
        "return JSON.stringify(r);}"
        "catch(e){return 'ERR:'+e.message}})()"
    )
    raw = session.evaluate(js)
    if isinstance(raw, str) and raw.startswith("ERR:"):
        raise RuntimeError(raw)
    return json.loads(raw)


def connected_server(session: DriverSession) -> dict | None:
    """The installed connector this journey can drive, or None."""

    servers = session.transport("GET", "/v1/mcp/servers")
    for server in (servers or {}).get("servers", []) or []:
        name = str(server.get("name") or "")
        if CONNECTOR_SLUG in name.lower():
            return server
    return None


# Deliberately INERT. The id is a placeholder that cannot resolve, and the
# prompt forbids substituting a real one — the INTENT is unambiguously a write,
# which is what makes the PDP gate it, but there is no reachable object to
# mutate. Borrowed from CN-09, which drives the same gate for its audit trail.
PROBE_ID = "00000000-0000-4000-8000-000000000000"
PROMPT = (
    "This is a deliberate permission test of the approval gate. I expect the "
    "action below to be DECLINED, and being declined is the correct outcome.\n\n"
    f"Using the connected {CONNECTOR_SLUG} connector, make exactly ONE attempt "
    f"to update the item whose id is exactly {PROBE_ID} so that its title "
    'becomes "gate probe".\n\n'
    "Hard constraints:\n"
    "- Use that id verbatim. It is a placeholder and it does not exist. Do not "
    "search for, list, guess, or substitute any other item, and do not create "
    "one instead.\n"
    "- Do not create, comment on, assign, close, archive or delete anything.\n"
    "- Make exactly one attempt. If it is declined or fails, stop and report "
    "the outcome verbatim. Do not retry, and do not reach for another tool."
)

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "write-gate-inline", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


#: The three DECISION controls are named after the approval they decide —
#: `tc-chat-approval-approve-<id>` / `-reject-<id>` / `-body-approve-<id>` — so
#: two parked asks can never make a click ambiguous. Everything structural on
#: the card (`tc-write-gate`, `-row`, `-title`, `-connector`, `-review`,
#: `-body*`) is still a global name, which is why only these three are matched
#: by PREFIX here and the rest stay exact.
#:
#: `-body-approve-` deliberately does not start with `-approve-`: an
#: irreversible write's only approve lives in the body, and a prefix that
#: swallowed it would let any "press Approve" journey press the one control the
#: card withholds until its payload has been read.
APPROVE_SELECTOR = "[data-testid^=tc-chat-approval-approve-]"
BODY_APPROVE_SELECTOR = "[data-testid^=tc-chat-approval-body-approve-]"
DECLINE_SELECTOR = "[data-testid^=tc-chat-approval-reject-]"


def _read_gate(session: DriverSession) -> dict:
    """The write-gate row as the DOM actually has it."""

    js = """(() => {
      const root = document.querySelector('[data-testid=tc-write-gate]');
      const row = document.querySelector('[data-testid=tc-write-gate-row]');
      const text = (sel) => {
        const n = document.querySelector(sel);
        return n ? (n.textContent || '').trim() : null;
      };
      return {
        present: !!root,
        open: root ? root.getAttribute('data-open') : null,
        risk: row ? row.getAttribute('data-risk') : null,
        title: text('[data-testid=tc-write-gate-title]'),
        connector: text('[data-testid=tc-write-gate-connector]'),
        // The ACCESSIBLE NAME, not textContent. The two lanes label this
        // control differently: an irreversible write keeps a worded button
        // ("Review →" / "Hide", because the disclosure is its only way
        // forward), while a reversible one is an icon-only chevron whose name
        // lives in aria-label. Reading textContent reported the empty string
        // for the lane production actually emits, so this field looked
        // captured and proved nothing.
        reviewLabel: (function () {
          var el = document.querySelector('[data-testid=tc-write-gate-review]');
          if (!el) return null;
          return el.getAttribute('aria-label') || (el.textContent || '').trim();
        })(),
        bodyPresent: !!document.querySelector('[data-testid=tc-write-gate-body]'),
        bodyParams: text('[data-testid=tc-write-gate-body-params]'),
        bodyReversibility: text('[data-testid=tc-write-gate-body-reversibility]'),
        bodyLedgerId: text('[data-testid=tc-write-gate-body-ledger-id]'),
        rowApprove: !!document.querySelector('__APPROVE__'),
        bodyApprove: !!document.querySelector('__BODY_APPROVE__'),
        decline: !!document.querySelector('__DECLINE__'),
        mode: (document.querySelector('[data-testid=thread-canvas]') || {}).getAttribute
          ? document.querySelector('[data-testid=thread-canvas]').getAttribute('data-mode')
          : null,
      };
    })()"""
    js = (
        js.replace("__APPROVE__", APPROVE_SELECTOR)
        .replace("__BODY_APPROVE__", BODY_APPROVE_SELECTOR)
        .replace("__DECLINE__", DECLINE_SELECTOR)
    )
    return session.evaluate(js) or {}


def _ledger_gate(session: DriverSession, run_id: str) -> dict:
    """The gate as the RUN'S EVENT STREAM has it — the id the UI must agree with."""

    replay = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = replay.get("events", []) if isinstance(replay, dict) else []
    for event in events:
        if str(event.get("event_type", "")).endswith("gate.opened"):
            payload = event.get("payload") or {}
            return {
                "gateId": payload.get("gate_id"),
                "authState": payload.get("auth_state"),
                "sequenceNo": event.get("sequence_no"),
                "runId": event.get("run_id"),
            }
    return {}


def main() -> int:
    try:
        _preflight_staged_runtime(target=SOURCE_TARGET)
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"inline write gate; provider={provider}")
    with _journey_environment():
        session = DriverSession(name=SESSION_NAME, fresh=False)
        completed = False
        try:
            with session:
                # A REUSED profile is already signed in and already keyed — the
                # whole point of reusing it is that a human connected a
                # connector there once. Both steps are therefore conditional;
                # asserting them unconditionally is what made the first run of
                # this journey die on "sign-in gate never appeared".
                if session.wait_for("[data-testid=sign-in-button]", 8):
                    session.sign_in_local()
                if session.wait_for("[data-testid=first-run-add-key]", 8):
                    session.ftue_add_key(provider, key)
                assert session.wait_visible("[data-testid=composer-textarea]", 30), (
                    "no composer after sign-in; the profile is not usable"
                )
                server = connected_server(session)
                if server is None:
                    _result(
                        "blocked",
                        f"no {CONNECTOR_SLUG!r} connector installed in profile "
                        f"journey-{SESSION_NAME}-reuse. A human must connect it "
                        "once by hand — the driver suppresses the browser "
                        "handoff, so no journey can finish an OAuth consent. "
                        "See tools/desktop-journeys/README.md.",
                    )
                    return 2
                fixture_name = str(server.get("name"))

                # A connector is scoped PER CHAT, and the scope has to exist
                # before the run that needs it — so seed the conversation with a
                # throwaway turn, bind the scope, then ask for the write.
                session.send_first_run_message("Say hi in five words.")
                conversation_id = _wait_for_conversation_id(session)
                _wait_for_new_run(session, conversation_id, 0)
                time.sleep(6)

                scoped = _transport_body(
                    session,
                    "PATCH",
                    f"/v1/agent/conversations/{conversation_id}/connectors",
                    {"scopes": {fixture_name: ["read", "write"]}},
                )
                assert (scoped.get("scopes") or {}).get(fixture_name) == [
                    "read",
                    "write",
                ], f"facade did not bind the fixture scope: {scoped}"

                session.fill("[data-testid=composer-textarea]", PROMPT)
                time.sleep(0.3)
                session.click('button[aria-label="Send message"]')
                run_id = _wait_for_new_run(session, conversation_id, 1)

                # The row is the signal the write parked. A run that never gates
                # proves nothing about this change, so say so rather than fail.
                if not session.wait_for("[data-testid=tc-write-gate]", 180):
                    run = session.transport("GET", f"/v1/agent/runs/{run_id}")
                    # Say WHICH way it failed to park: the model never called a
                    # write tool, or it called one and the PDP let it through.
                    # Those need opposite fixes and look identical from here.
                    replay = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
                    events = (
                        replay.get("events", []) if isinstance(replay, dict) else []
                    )
                    tools = [
                        (e.get("payload") or {}).get("tool_name")
                        for e in events
                        if str(e.get("event_type", "")).endswith("tool_call_started")
                    ]
                    servers = session.transport("GET", "/v1/mcp/servers")
                    _result(
                        "blocked",
                        "the write never parked at a gate "
                        f"(run status={run.get('status')!r}); "
                        f"tools_called={[t for t in tools if t]}; "
                        f"servers={json.dumps(servers)[:400]}",
                    )
                    return 2

                time.sleep(1.5)
                collapsed = _read_gate(session)
                session.shot("01-parked-write-collapsed")

                # Collapsed: the ask, and no way to approve blind.
                assert collapsed.get("open") == "false", (
                    f"the gate auto-expanded: {collapsed}"
                )
                assert not collapsed.get("bodyPresent"), (
                    "the payload rendered before anyone asked for it"
                )
                assert collapsed.get("decline"), "decline must always be one click"

                # Expand IN PLACE.
                session.click("[data-testid=tc-write-gate-review]")
                assert session.wait_for("[data-testid=tc-write-gate-body]", 30), (
                    "Review did not expand the row"
                )
                time.sleep(1.0)
                expanded = _read_gate(session)
                session.shot("02-parked-write-expanded-in-place")

                assert expanded.get("mode") == collapsed.get("mode"), (
                    f"reviewing changed the mode "
                    f"({collapsed.get('mode')!r} -> {expanded.get('mode')!r}); "
                    "the point of expanding in place is that it does not"
                )
                assert expanded.get("bodyParams"), (
                    f"the expanded body showed no payload: {expanded}"
                )

                # THE CLAIM: the anchor on screen is the ledger's, not a guess.
                gate = _ledger_gate(session, run_id)
                shown = expanded.get("bodyLedgerId")
                print(
                    json.dumps(
                        {"collapsed": collapsed, "expanded": expanded, "gate": gate},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                assert gate.get("gateId"), (
                    "no gate.opened on the run stream — cannot verify the anchor"
                )
                assert shown, f"the expanded body printed no ledger id: {expanded}"
                # `formatLedgerId` renders r<short>·<seq> off the GATE's seq. If
                # the UI had derived it from the approval instead, the sequence
                # half would disagree with the gate.opened event below.
                assert str(gate["sequenceNo"]) in str(shown), (
                    f"the displayed anchor {shown!r} does not carry the "
                    f"gate.opened sequence {gate['sequenceNo']!r} — it was "
                    "derived from the wrong event"
                )

                # Decline. Never approve: the safe terminal state, and it leaves
                # a resolved gate behind.
                session.click(DECLINE_SELECTOR)
                time.sleep(3)
                session.shot("03-declined")
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
