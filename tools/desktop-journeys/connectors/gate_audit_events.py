#!/usr/bin/env python3
"""CN-09 — the write-approval gate leaves an audit trail, proven on a REAL run.

``gate.opened`` / ``gate.resolved`` for the MCP **write** gate landed in commit
875db5a7 and have never been observed outside unit tests. Before it, a write
parked on the LangGraph interrupt, was decided by a human, and then executed or
refused **leaving no ledger row at all** — the run could not say who let the
write through, or that a gate had ever been in the way. "We record the decision
but not the gate that forced it" is the shape a regulated buyer asks about
first, so a green unit test is not the evidence that matters here. A live run is.

What this journey asserts, in order:

1. a Linear WRITE parks the run (``status == waiting_for_approval``);
2. the run's ledger contains ``gate.opened`` for that park, carrying the parked
   ``approval_id`` as its ``gate_id`` and ``auth_state == "insufficient"`` — the
   member that means *the standing authorization is not sufficient for THIS
   operation*, which is exactly what distinguishes a write gate from the
   OAuth-connect gate (``missing`` / ``expired``);
3. the human **declines**;
4. the ledger then contains ``gate.resolved`` for the SAME ``gate_id`` with
   ``outcome == "cancelled"``;
5. nothing was written to Linear.

SAFETY — this is the design, not a disclaimer
=============================================
This journey drives a write against the user's REAL Linear workspace, so every
part of it is built around one rule: **it must never approve a write, and no
external change may ever result from running it.** Four independent layers:

* **It refuses to ask for a write that could auto-execute.** Before sending
  anything, :func:`autoexecute_risks` reads the app's own authenticated
  surfaces and BLOCKS (exit ``2``) if a write would bypass the gate: a
  per-connector ``write_policy == allow_always`` override, a tool-use policy
  whose ``write`` / ``destructive`` axis reads ``auto``, or a composer pill
  sitting on **Bypass** (``Posture.BYPASS`` lifts every ASK/REQUIRE gate to
  ALLOW — ``capabilities/policy/service.py`` §3.2). In any of those states the
  gate this journey exists to observe would not engage, and the write WOULD
  land. Blocking is the only honest outcome.
* **The write it asks for is inert even if it somehow dispatched.** The target
  is a placeholder issue id that cannot resolve
  (``00000000-0000-4000-8000-000000000000``), and the prompt forbids searching
  for, guessing, or substituting a real issue, and forbids creating one. The
  *intent* is unambiguously a write — which is what makes the PDP gate it — but
  there is no reachable object to mutate.
* **It only ever declines.** There is no approve path in this file. Declining is
  what makes the outcome ``cancelled``, and a declined gate never dispatches:
  ``ToolAccessGate`` returns ``approved=False``, ``CallMcpTool`` returns the
  typed ``permission_denied`` refusal, and the connector call is never made.
* **It sweeps.** Any approval still pending at teardown is declined too, so the
  reused profile is never left holding a card a later human could approve.

The post-run assertion is on the run's own evidence — the declined-write refusal
is present, ``gate.resolved.outcome == "cancelled"``, no ``write.applied`` /
``effect.applied``, and no successful tool result naming the gated op. It does
**not** query Linear's audit log; the guarantee is structural (the gate refuses
before dispatch), and this journey states that rather than implying a read-back
it never performed.

Prerequisites a script cannot satisfy
=====================================
**Linear must already be connected, out of band.** ``driver.mjs`` replaces
``shell.openExternal`` and only RECORDS the URL, so no journey can finish a
vendor OAuth — see ``tools/desktop-journeys/README.md``, "A journey can NEVER
complete an OAuth connect". This journey therefore reuses the SAME profile
FS-F reuses, because ``DriverSession`` derives the userData subdir from its
``name`` (``journey-<name>-reuse`` when ``fresh=False``): sharing the name is
the only way to inherit the one connect a human already made, and it means this
journey adds no new manual setup. Its evidence file and screenshots are named
distinctly so the two journeys do not overwrite each other's artefacts.

Step 1, once, by hand (skip it if you already did it for FS-F)::

    COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" \\
    COPILOT_DESKTOP_USER_DATA_SUBDIR=journey-fs-f-linear-mcp-reuse \\
        npm run dev --workspace @0x-copilot/desktop
    # "Tools" in the left rail -> Linear -> Connect -> finish OAuth in the
    # browser -> the row must read Connected -> QUIT the app.

Step 2, the journey::

    COPILOT_HOME="$PWD/apps/desktop/resources" \\
    COPILOT_JOURNEY_DOTENV=/path/to/services/ai-backend/.env \\
        python3 tools/desktop-journeys/connectors/gate_audit_events.py

``COPILOT_RUNTIME_DIR`` in step 1 and ``COPILOT_HOME`` in step 2 must be the
same directory: the connection lives in the supervised Postgres under that
staged runtime, so a different stage is a different database. Do NOT add
``COPILOT_DEV=1`` / ``COPILOT_AUTH_MODE=dev-mint`` — that signs in a different
persona, whose connectors this journey cannot see.

**And re-stage after any ``services/*`` change.** ``<COPILOT_HOME>/runtime/**``
is a SNAPSHOT. ``gate.opened`` for the write gate is younger than most stages on
this machine, so a stale stage will report the very absence this journey is
meant to detect — and report it with total confidence.

Exit codes (README §"Exit codes"): ``0`` passed · ``1`` failed · ``2`` blocked
(Linear not connected, or a write here could auto-execute, or the model never
attempted the write) · ``3`` skipped (no staged runtime / no BYOK key).

Privacy: no key, token, or ``.env`` content is written to evidence or a
screenshot. Log excerpts and answers pass through :func:`_redact`, and the only
workspace data this journey can surface is the placeholder id it supplied
itself.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Final, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# The FS set owns the shared run/approval/event helpers this journey needs
# (``_fs_journey_lib`` is the home ``_lib`` does not have for POST-with-body).
# Importing it is the same cross-set reuse ``_fs_journey_lib`` itself performs
# on ``generative-workflows`` — it is not a copy of one journey into another.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "filesystem-access"))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    TERMINAL,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    dump,
    event_name,
    events,
    lane,
    payload_of,
    result,
    run_status,
    runs_for,
    settle_run,
    tool_calls,
    transport_json,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY: Final = "CN-09"
SLUG: Final = "linear"

#: The DriverSession name IS the profile selector — ``_lib.DriverSession``
#: builds ``journey-<name>-reuse`` from it when ``fresh=False``. Sharing FS-F's
#: name is deliberate and is the whole reason this journey needs no separate
#: manual connect; see the module docstring.
SESSION_NAME: Final = "fs-f-linear-mcp"
EVIDENCE_FILE: Final = "cn-09-gate-audit-evidence.json"

CONNECTORS_RAIL: Final = '[data-destination="connectors"]'
CHATS_RAIL: Final = '[data-destination="chats"]'
RUN_RAIL: Final = '[data-destination="run"]'
NEW_CHAT: Final = "[data-testid=chats-new-chat]"
SIGN_IN: Final = "[data-testid=sign-in-button]"
ADD_KEY: Final = "[data-testid=first-run-add-key]"
COMPOSER: Final = "[data-testid=composer-textarea]"
FTUE_SKIP: Final = "[data-testid=first-run-skip]"

#: The composer's execution-mode pill. ``data-mode="bypass"`` means the run
#: would seal ``Posture.BYPASS``, which lifts every approval gate — so a write
#: sent in that state EXECUTES. Absence of the pill is manual: the sealed
#: default is ``FilesystemBypassMode.MANUAL``
#: (``execution/filesystem_bypass.py``, ``MANUAL_FILESYSTEM_BYPASS``).
BYPASS_PILL: Final = ".atlas-bypass-pill"

#: The two DOM ways a user declines. A parked write renders as the QUESTION
#: card (the write gate rides an ``approval_requested`` / ``ask_a_question``
#: interrupt), not the consent card, so both shapes are looked for — and
#: ``qc-skip`` only renders for a multi-select question, which is why
#: :func:`decline` still needs its transport fallback.
#:
#: Both selectors are scoped to ONE approval on purpose: a bare
#: ``[data-testid=qc-skip]`` matches every question card on screen, and
#: Playwright refuses an ambiguous selector — turning "two cards parked" into a
#: decline that never happened.
REJECT_BUTTON: Final = '[data-testid="tc-chat-approval-reject-{approval_id}"]'
QUESTION_SKIP: Final = (
    '[data-testid="tc-chat-question-{approval_id}"] [data-testid="qc-skip"]'
)

#: The placeholder the prompt names. Shaped like a v4 UUID so the connector's
#: own argument validation accepts it, and all-zero so it cannot address a real
#: issue. Nothing in the user's workspace can be reached through it.
PROBE_ISSUE_ID: Final = "00000000-0000-4000-8000-000000000000"
PROBE_TITLE: Final = "gate-audit probe (must never be applied)"

#: One deliberate write attempt against an unreachable target. The framing is
#: honest — a model told this is a permission test does not go looking for a
#: "better" (real) issue to satisfy the request with, which is the failure mode
#: that would turn a safe probe into a real mutation.
PROMPT: Final = (
    "This is a deliberate permission test of the approval gate. I expect the "
    "action below to be DECLINED, and being declined is the correct outcome.\n\n"
    "Using the connected Linear connector, make exactly ONE attempt to update "
    f"the title of the Linear issue whose id is exactly {PROBE_ISSUE_ID} so "
    f'that its title becomes "{PROBE_TITLE}".\n\n'
    "Hard constraints:\n"
    "- Use that id verbatim. It is a placeholder and it does not exist. Do not "
    "search for, list, guess, or substitute any other issue, and do not create "
    "one instead.\n"
    "- Do not create, comment on, assign, close, archive or delete anything, "
    "in Linear or anywhere else.\n"
    "- Make exactly one attempt. If it is declined or fails, stop and report "
    "the outcome verbatim. Do not retry, and do not reach for another tool.\n"
    "- Say plainly whether anything was changed. If nothing was changed, say "
    "that nothing was changed."
)

#: The ledger pair under test. Not the ``.v2`` spellings — those belong to the
#: workspace-effect gate; the MCP write gate reuses the run's ONE gate
#: vocabulary (see ``surfaces_v2/gate.py::GateLedger`` for why).
GATE_OPENED: Final = "gate.opened"
GATE_RESOLVED: Final = "gate.resolved"

#: ``insufficient`` is the write gate's discriminator: the connect gate emits
#: ``missing`` / ``expired``, and only the write gate says "the standing
#: authorization is not sufficient for THIS operation".
WRITE_GATE_AUTH_STATE: Final = "insufficient"
CANCELLED_OUTCOME: Final = "cancelled"

APPROVAL_REQUESTED: Final = "approval_requested"
APPROVAL_RESOLVED: Final = "approval_resolved"
MCP_AUTH_REQUIRED: Final = "mcp_auth_required"
RESULT_EVENTS: Final = ("tool_result", "tool_call_completed")

#: A landed write would leave one of these. Absence is not proof on its own —
#: the MCP write path does not stage — but their PRESENCE would be proof of the
#: opposite, so they are checked.
APPLY_EVENTS: Final = ("write.applied", "effect.applied")

#: The typed refusal a declined write returns
#: (``capabilities/mcp/middleware/call_tool.py::_WRITE_DECLINED``). Its presence
#: is positive evidence that the dispatch was refused rather than merely absent.
WRITE_DECLINED_COPY: Final = "The action was declined; no external change was made."
PERMISSION_DENIED_CODE: Final = "permission_denied"

#: The write gate's card copy (``gate.py::_Messages.approve``). Used ONLY to
#: tell "a write parked and the ledger stayed silent" (the regression) from "the
#: model asked its own question" (inconclusive) when ``gate.opened`` is absent —
#: a heuristic by necessity, since the projection strips the gate block from the
#: ``ask_a_question`` payload the client can see.
GATE_QUESTION_RE: Final = re.compile(r"^Allow .+ to run [\w.:-]+\?$")

#: Every write/destructive posture that would let a write dispatch WITHOUT a
#: gate. ``ask`` / ``require`` gate; ``block`` denies outright (also safe).
AUTO_MODE: Final = "auto"
GATED_AXES: Final = ("write", "destructive")

_REDACTIONS: Final = (
    re.compile(r"(?i)\b(bearer|token|authorization)\b\s*[:=]?\s*\S+"),
    re.compile(r"\b(sk|lin_api|lin_oauth|xoxb|ghp|gho)[-_][A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b"),
)


def _redact(text: str) -> str:
    """Strip anything credential-shaped from a string bound for the run dir."""

    for pattern in _REDACTIONS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _safe(session: DriverSession, path: str) -> Any:
    """An authenticated GET through the app that reports its own failure.

    Never raises: a surface this journey cannot read is a fact to record and
    then judge (several of them BLOCK), not an exception that discards the
    evidence already gathered.
    """

    try:
        return transport_json(session, "GET", path)
    except Exception as exc:  # noqa: BLE001
        return {"error": _redact(repr(exc))[:300]}


def _entries(value: Any, *keys: str) -> list[dict[str, Any]]:
    """The list of dicts inside a response, whichever key it arrived under."""

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, list):
                return [item for item in found if isinstance(item, dict)]
    return []


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    """Every dict nested anywhere inside ``value``, itself included."""

    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


# ── is the connector usable at all ───────────────────────────────────────────
def _server_view(entry: dict[str, Any]) -> dict[str, Any]:
    """The fields that decide usability — with header VALUES never copied."""

    return {
        "name": entry.get("name"),
        "display_name": entry.get("display_name"),
        "auth_mode": entry.get("auth_mode"),
        "auth_state": entry.get("auth_state"),
        "health": entry.get("health"),
        "enabled": entry.get("enabled"),
        "access_mode": entry.get("access_mode"),
    }


def usability(server: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Can the agent reach this connector, and if not, what must a human do?

    Same four-part gate FS-F uses, and for the same reason: a catalog entry is
    not a configured server, and an ``access_mode: off`` server is dropped from
    the model's cards entirely, so the agent cannot call it however green the
    row looks.
    """

    if server is None:
        return False, [
            "Linear is not installed for this account. A human must open the "
            'app, click "Tools" in the left rail, find Linear under Available, '
            "and click Connect — which opens Linear's OAuth consent in a "
            "browser. No script can do this: it authorizes a third party in "
            "the user's name."
        ]
    blockers: list[str] = []
    if server.get("auth_state") != "authenticated":
        blockers.append(
            f"Linear is installed but auth_state={server.get('auth_state')!r}; "
            'a human must Reconnect it from "Tools".'
        )
    if server.get("enabled") is False or server.get("health") == "disabled":
        blockers.append('Linear is disabled; a human must re-enable it in "Tools".')
    if server.get("access_mode") == "off":
        blockers.append(
            "Linear is authenticated but access_mode=off, so the runtime drops "
            "it from the model's cards and no write can even be attempted."
        )
    return not blockers, blockers


# ── the safety gate: would a write here auto-execute? ────────────────────────
def _policy_modes(response: Any) -> list[dict[str, str]]:
    """Every ``{kind, mode}`` row in a tool-use policy response."""

    rows: list[dict[str, str]] = []
    for entry in _entries(response, "policies", "entries"):
        kind = entry.get("kind")
        mode = entry.get("mode")
        if isinstance(kind, str) and isinstance(mode, str):
            rows.append({"kind": kind, "mode": mode})
    return rows


def autoexecute_risks(session: DriverSession, evidence: dict[str, Any]) -> list[str]:
    """Every reason a Linear write might run WITHOUT parking on the gate.

    A non-empty list means this journey must not send its prompt at all: the
    gate would not engage, so the write would reach the user's real workspace.
    The checks are deliberately conservative — anything unreadable counts as a
    risk, because "we could not establish that a write would park" and "a write
    would park" are not the same sentence.
    """

    risks: list[str] = []

    # 1. The per-connector override. ``allow_always`` downgrades WRITE+ASK to
    #    AUTO in the PDP (``PdpPolicyService`` §3.4), i.e. no gate.
    connectors = _safe(session, "/v1/connectors")
    evidence["connectors_read_error"] = (
        connectors.get("error") if isinstance(connectors, dict) else None
    )
    rows = _entries(connectors, "connectors", "items")
    connector_row = next(
        (
            row
            for row in rows
            if SLUG in {str(row.get("slug") or ""), str(row.get("name") or "").lower()}
        ),
        None,
    )
    evidence["connector_write_policy"] = (
        connector_row.get("write_policy") if connector_row else None
    )
    if connector_row is None:
        risks.append(
            "GET /v1/connectors did not list Linear, so its write-policy "
            "override could not be read; a stored allow_always would let this "
            "write execute unattended."
        )
    elif connector_row.get("write_policy") == "allow_always":
        risks.append(
            "Linear's write_policy is allow_always — an earlier 'always allow' "
            "choice. A write would execute WITHOUT a gate. Clear it (set "
            'ask_first from the connector\'s row in "Tools") before running '
            "this journey."
        )

    # 2. The tool-use policy axes. A ``write``/``destructive`` axis on ``auto``
    #    is ALLOW at the PDP. Every readable scope must be non-auto: user scope
    #    normally wins, but blocking on a workspace ``auto`` we cannot prove is
    #    overridden costs one manual check and cannot cost a real write.
    for scope, path in (
        ("user", "/v1/me/policies/tool-use"),
        ("workspace", "/v1/workspace/policies/tool-use"),
    ):
        response = _safe(session, path)
        modes = _policy_modes(response)
        evidence[f"tool_use_policy_{scope}"] = modes or (
            response.get("error") if isinstance(response, dict) else None
        )
        if scope == "user" and not modes:
            risks.append(
                "the per-user tool-use policy could not be read, so the write "
                f"posture is unknown ({path} returned no policy rows)"
            )
        for row in modes:
            if row["kind"] in GATED_AXES and row["mode"] == AUTO_MODE:
                risks.append(
                    f"the {scope}-scope tool-use policy sets {row['kind']}="
                    f"{AUTO_MODE}, which makes the PDP ALLOW a write instead of "
                    "gating it"
                )

    # 3. The composer pill. BYPASS lifts every remaining ASK/REQUIRE gate.
    #    Read on the surface the prompt will be sent from — the pill is part of
    #    the composer, so reading it from any other destination answers "absent"
    #    for a control that is merely elsewhere, which is the one answer this
    #    check must never invent.
    pill_mode = session.evaluate(
        "(() => { const el = document.querySelector('"
        + BYPASS_PILL
        + "'); return el ? el.getAttribute('data-mode') : null; })()"
    )
    evidence["bypass_pill_mode"] = pill_mode
    if isinstance(pill_mode, str) and pill_mode.lower() == "bypass":
        risks.append(
            "the composer's execution-mode pill is on Bypass, which seals "
            "Posture.BYPASS on the run and lifts every approval gate; set it "
            "back to Manual before running this journey"
        )
    return risks


# ── driving the run ──────────────────────────────────────────────────────────
def bootstrap(session: DriverSession, provider: str, key: str) -> dict[str, Any]:
    """Clear whichever first-run gates are actually present.

    A reused profile is already signed in and already keyed, so the hard
    asserts in ``sign_in_local`` / ``ftue_add_key`` would fail the journey for
    being in exactly the state it needs to be in.
    """

    state: dict[str, Any] = {"signed_in_now": False, "key_added_now": False}
    if session.wait_for(SIGN_IN, 25):
        session.click(SIGN_IN)
        state["signed_in_now"] = True
    if session.wait_for(ADD_KEY, 25):
        session.ftue_add_key(provider, key)  # the key itself is never logged
        state["key_added_now"] = True
    else:
        assert session.wait_for(COMPOSER, 60), (
            "no first-run key gate and no composer — the app never reached a "
            "usable state, so nothing below would be measuring the gate"
        )
    # Leaving the full-screen first-run gate costs no model call; sending a
    # throwaway message to get the same effect would spend one.
    if session.present(FTUE_SKIP):
        session.click(FTUE_SKIP)
        state["skipped_ftue"] = True
    state["rail_mounted"] = session.wait_for("[data-destination]", 30)
    return state


def _thread_baseline(session: DriverSession) -> tuple[str | None, int]:
    """The conversation on screen and how many runs it already has.

    A reused profile restores the last thread, so "the newest run" can be a run
    from a previous session. Counting first is what makes ``wait_for_new_run``
    mean what its name says.
    """

    match = re.fullmatch(
        r"#/convo/([^/?#]+)(?:[?#].*)?",
        str(session.evaluate("window.location.hash") or ""),
    )
    if match is None:
        return None, 0
    conversation_id = match.group(1)
    try:
        return conversation_id, len(runs_for(session, conversation_id))
    except Exception:  # noqa: BLE001 — an unreadable baseline is just zero
        return conversation_id, 0


def open_new_thread(session: DriverSession) -> bool:
    """Land on a fresh thread, with the composer actually on screen.

    Separate from sending because the safety checks run BETWEEN the two: the
    execution-mode pill lives in the composer, so it has to be mounted before
    it can be read, and it has to be read before a write prompt is sent.
    """

    session.click(CHATS_RAIL)
    time.sleep(2)
    if session.present(NEW_CHAT):
        session.click(NEW_CHAT)
        time.sleep(2)
    else:
        session.click(RUN_RAIL)
        time.sleep(2)
    return session.wait_visible(COMPOSER, 30)


def send_write_probe(session: DriverSession, evidence: dict[str, Any]) -> str:
    """Send the one write attempt into the open thread. Returns the run id."""

    previous_id, before = _thread_baseline(session)
    session.send_first_run_message(PROMPT)
    conversation_id = wait_for_conversation_id(session)
    run_id = wait_for_new_run(
        session, conversation_id, before if conversation_id == previous_id else 0
    )
    evidence["conversation_id"] = conversation_id
    evidence["run_id"] = run_id
    evidence["reused_thread"] = conversation_id == previous_id
    return run_id


def approval_index(stream: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every approval this run raised, keyed by id, with its resolution state."""

    index: dict[str, dict[str, Any]] = {}
    for event in stream:
        name = event_name(event)
        payload = payload_of(event)
        approval_id = payload.get("approval_id")
        if not isinstance(approval_id, str) or not approval_id:
            continue
        if name in {APPROVAL_REQUESTED, MCP_AUTH_REQUIRED}:
            index.setdefault(
                approval_id,
                {
                    "approval_id": approval_id,
                    "event": name,
                    "approval_kind": payload.get("approval_kind"),
                    "question": payload.get("question") or payload.get("message"),
                    "resolved": False,
                    "decision": None,
                },
            )
        elif name == APPROVAL_RESOLVED and approval_id in index:
            index[approval_id]["resolved"] = True
            index[approval_id]["decision"] = payload.get("decision") or payload.get(
                "status"
            )
    return index


def decline(session: DriverSession, approval_id: str) -> dict[str, Any]:
    """Decline ONE parked approval. There is deliberately no approve path.

    The DOM control is preferred because it is what a user actually presses,
    but the write gate renders as the question card and that card only grows a
    Skip button for a multi-select question — so the fallback POSTs the exact
    body the card's own Skip posts (``RunDestination.handleAnswer`` maps an
    empty answer to ``decision: rejected``). Both routes are unambiguous
    declines; which one ran is recorded rather than assumed.

    A DOM click that Playwright refuses falls through to the endpoint rather
    than raising. Raising here would abandon a PARKED approval in a profile the
    next run reuses, which is the one outcome the safety contract forbids.
    """

    for selector, label in (
        (REJECT_BUTTON.format(approval_id=approval_id), "reject-button"),
        (QUESTION_SKIP.format(approval_id=approval_id), "question-skip"),
    ):
        if not session.present(selector):
            continue
        try:
            session.click(selector)
        except Exception:  # noqa: BLE001 — fall through to the endpoint
            break
        return {"approval_id": approval_id, "declined_via": label}
    try:
        transport_json(
            session,
            "POST",
            f"/v1/agent/approvals/{approval_id}/decision",
            {
                "decision": "rejected",
                "reason": "declined by the CN-09 gate-audit journey",
            },
        )
    except Exception as exc:  # noqa: BLE001 — a failed decline is a finding
        return {
            "approval_id": approval_id,
            "declined_via": "decision-endpoint",
            "error": _redact(repr(exc))[:300],
        }
    return {"approval_id": approval_id, "declined_via": "decision-endpoint"}


def park_and_decline(
    session: DriverSession, run_id: str, evidence: dict[str, Any]
) -> None:
    """Wait for the write to park, then decline every approval it raised.

    Loops rather than declining once: a model that issued two tool calls parks
    twice, and an un-declined second park would leave the run waiting and the
    profile holding a live card.
    """

    declines: list[dict[str, Any]] = []
    parked_seen = False
    deadline = time.time() + 240
    while time.time() < deadline:
        status = run_status(session, run_id).get("status")
        if status in TERMINAL:
            break
        stream = events(session, run_id)
        pending = [
            entry
            for entry in approval_index(stream).values()
            if not entry["resolved"]
            and entry["approval_id"] not in {d["approval_id"] for d in declines}
        ]
        if pending:
            if not parked_seen:
                parked_seen = True
                evidence["parked_status"] = status
                session.shot("cn09-02-parked")
            for entry in pending:
                declines.append({**decline(session, entry["approval_id"]), **entry})
                time.sleep(1.5)
            time.sleep(4)
            continue
        time.sleep(2)

    evidence["parked"] = parked_seen
    evidence["declines"] = declines
    evidence["approved_anything"] = False  # this file has no approve path
    session.shot("cn09-03-after-decline")


def sweep_pending(session: DriverSession, run_id: str) -> list[dict[str, Any]]:
    """Decline anything still pending, whatever went wrong above.

    The profile is REUSED, so a card left pending outlives this process. Best
    effort by design: teardown must never raise past the evidence dump.
    """

    swept: list[dict[str, Any]] = []
    try:
        for entry in approval_index(events(session, run_id)).values():
            if not entry["resolved"]:
                swept.append(decline(session, entry["approval_id"]))
    except Exception as exc:  # noqa: BLE001 — teardown reports, never raises
        swept.append({"error": _redact(repr(exc))[:200]})
    return swept


# ── reading the ledger ───────────────────────────────────────────────────────
def gate_rows(stream: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    """The projected payloads of one gate event type, in ledger order."""

    return [
        {**payload_of(event), "sequence_no": event.get("sequence_no")}
        for event in stream
        if event_name(event) == event_type
    ]


def write_evidence(stream: list[dict[str, Any]], ops: set[str]) -> dict[str, Any]:
    """Did the declined write stay declined?

    Three readings, kept separate because they fail differently: the typed
    refusal (positive proof the dispatch was refused), any apply-shaped ledger
    event (proof of the opposite), and any tool result that both names a gated
    op and does not look like a failure (the shape a silently-executed write
    would leave).
    """

    refusals: list[dict[str, Any]] = []
    applied: list[str] = []
    suspicious: list[dict[str, Any]] = []
    for event in stream:
        name = event_name(event)
        payload = payload_of(event)
        if name in APPLY_EVENTS:
            applied.append(name)
        for node in _walk(payload):
            code = node.get("code") or node.get("error_code")
            message = node.get("safe_message")
            if code == PERMISSION_DENIED_CODE or message == WRITE_DECLINED_COPY:
                refusals.append({"event": name, "code": code, "safe_message": message})
        if name not in RESULT_EVENTS:
            continue
        status = str(payload.get("status") or "")
        blob = json.dumps(payload, default=str)
        names_a_gated_op = any(op and op in blob for op in ops)
        looks_refused = (
            status in {"failed", "error", "blocked"}
            or WRITE_DECLINED_COPY in blob
            or PERMISSION_DENIED_CODE in blob
        )
        if names_a_gated_op and not looks_refused:
            suspicious.append(
                {
                    "event": name,
                    "tool_name": payload.get("tool_name"),
                    "status": status or None,
                    "excerpt": _redact(blob)[:400],
                }
            )
    return {
        "declined_refusals": refusals[:10],
        "apply_events": applied,
        "suspicious_results": suspicious[:10],
    }


def gated_ops(opened: list[dict[str, Any]]) -> set[str]:
    """The tool names the gate policed, read off ``purpose``.

    The ledger purpose is built as ``approve <op_class> <op> on <connector>``
    from identifier-sanitised tokens only — never from the call's arguments —
    so the third token is the op name and nothing user-authored rides along.
    """

    ops: set[str] = set()
    for row in opened:
        parts = str(row.get("purpose") or "").split()
        if len(parts) >= 3 and parts[0] == "approve":
            ops.add(parts[2])
    return ops


# ── verdict ──────────────────────────────────────────────────────────────────
def verdict(evidence: dict[str, Any]) -> tuple[str, dict[str, Any], int]:
    """Turn the gathered evidence into an outcome, a report, and an exit code.

    Pure on purpose: every claim is decided here from data alone, so the wording
    of a failure can be exercised without booting the app, and no verdict can
    quietly depend on something only a live session knows.
    """

    if evidence.get("blockers"):
        return (
            "blocked",
            {
                "reason": "Linear is not usable for this account",
                "human_action_required": evidence["blockers"],
                "linear_server": evidence.get("linear_server"),
            },
            2,
        )
    if evidence.get("autoexecute_risks"):
        return (
            "blocked",
            {
                "reason": (
                    "a Linear write would NOT park on the approval gate in this "
                    "configuration, so asking for one could change the user's "
                    "real workspace; no prompt was sent"
                ),
                "risks": evidence["autoexecute_risks"],
            },
            2,
        )

    opened = evidence.get("gate_opened") or []
    resolved = evidence.get("gate_resolved") or []
    approvals = evidence.get("approvals") or []
    writes = evidence.get("write_evidence") or {}
    declines = evidence.get("declines") or []
    failures: list[str] = []

    # The two halves of the pair are judged under different preconditions.
    # ``gate.opened`` is owed the moment the write PARKS, so it is asserted
    # unconditionally. ``gate.resolved`` is owed only once a decision was
    # actually delivered — a decline this journey failed to POST must be
    # reported as an undelivered decline, never as a missing ledger row, or the
    # harness's own failure would be filed as a product audit gap.
    failed_declines = [row for row in declines if row.get("error")]
    decision_delivered = bool(declines) and not failed_declines

    gate_shaped = [
        approval
        for approval in approvals
        if isinstance(approval.get("question"), str)
        and GATE_QUESTION_RE.match(approval["question"].strip())
    ]

    if not opened:
        if not evidence.get("parked") and not gate_shaped:
            # Nothing parked at all. The gate was never reached, so this run
            # says nothing about the ledger either way — and, importantly, no
            # write was attempted, so nothing was at risk.
            return (
                "blocked",
                {
                    "reason": (
                        "the model never issued a Linear write, so the write "
                        "gate never engaged and the assertion was not exercised"
                    ),
                    "run_id": evidence.get("run_id"),
                    "tools_called": evidence.get("tools"),
                    "run_status": evidence.get("run_status"),
                    "answer": evidence.get("answer"),
                },
                2,
            )
        failures.append(
            "AUDIT GAP: a write parked on the approval gate and the run ledger "
            f"contains NO {GATE_OPENED} event. This is exactly the state "
            "875db5a7 fixed — the write gate parks, resumes and executes "
            "leaving no trail, so the run cannot say a gate was ever in the "
            "way. (If the staged runtime predates that commit, re-stage: a "
            "stale stage reports this absence with total confidence.)"
        )
    else:
        write_gates = [
            row for row in opened if row.get("auth_state") == WRITE_GATE_AUTH_STATE
        ]
        if not write_gates:
            failures.append(
                f"{GATE_OPENED} was emitted but no row carries "
                f"auth_state={WRITE_GATE_AUTH_STATE!r}, so what parked was a "
                "connect gate, not the write gate under test: "
                + json.dumps([row.get("auth_state") for row in opened])
            )
        parked_ids = {approval["approval_id"] for approval in approvals}
        unpaired = [
            row.get("gate_id")
            for row in opened
            if row.get("gate_id") not in parked_ids and parked_ids
        ]
        if unpaired:
            failures.append(
                f"{GATE_OPENED}.gate_id does not match any parked approval_id "
                f"({unpaired}); the gate row cannot be tied back to the "
                "decision, which is the whole point of carrying the approval id"
            )
        opened_ids = {row.get("gate_id") for row in opened}
        resolved_ids = {row.get("gate_id") for row in resolved}
        missing = sorted(str(gate_id) for gate_id in opened_ids - resolved_ids)
        if decision_delivered and missing:
            failures.append(
                f"AUDIT GAP: {GATE_OPENED} has no matching {GATE_RESOLVED} for "
                f"gate_id {missing}. The write was declined, so the ledger "
                "records a gate that is still open and PendingWorkProjector "
                "will report it pending forever."
            )
        wrong = [
            row.get("outcome")
            for row in resolved
            if row.get("gate_id") in opened_ids
            and row.get("outcome") != CANCELLED_OUTCOME
        ]
        if wrong:
            failures.append(
                f"{GATE_RESOLVED}.outcome is {wrong} for a gate this journey "
                f"DECLINED; it must be {CANCELLED_OUTCOME!r}. An outcome of "
                "'connected' on a declined write would mean the ledger records "
                "a grant that was never given."
            )

    # A real defect outranks an undelivered decline: ``gate.opened`` is owed at
    # park time, so its absence is still the finding even if the decline then
    # failed to land.
    if not failures and opened and not decision_delivered:
        return (
            "blocked",
            {
                "reason": (
                    "the gate opened, but this journey never delivered a "
                    "decline, so the resolution half of the pair was not "
                    "exercised (nothing was approved, and nothing was written)"
                ),
                "declines": declines,
                "gate_opened": opened,
                "run_id": evidence.get("run_id"),
            },
            2,
        )

    if writes.get("apply_events"):
        failures.append(
            "a write-apply ledger event was emitted on a run whose only write "
            f"was declined: {writes['apply_events']}"
        )
    if writes.get("suspicious_results"):
        failures.append(
            "a tool result names the gated operation and does not read as a "
            "refusal — a declined write may have dispatched anyway: "
            + json.dumps(writes["suspicious_results"][:2])[:600]
        )
    if opened and decision_delivered and not writes.get("declined_refusals"):
        failures.append(
            "the gate opened and was declined, but no typed "
            f"{PERMISSION_DENIED_CODE!r} refusal reached the model — a decline "
            "the run never sees is indistinguishable from a silent success"
        )
    if decision_delivered and evidence.get("run_status") not in {"completed", None}:
        # A declined write is a refusal the model reads and answers, so the run
        # resumes and finishes. Still parked here means the decision never
        # reached the interrupt.
        failures.append(
            f"run did not complete after the decline: "
            f"status={evidence.get('run_status')!r} "
            f"safe_error={evidence.get('safe_error')!r}"
        )

    if failures:
        return (
            "FAILED",
            {
                "reasons": failures,
                "run_id": evidence.get("run_id"),
                "gate_opened": opened,
                "gate_resolved": resolved,
                "approvals": approvals,
                "write_evidence": writes,
            },
            1,
        )
    return (
        "passed",
        {
            "run_id": evidence.get("run_id"),
            "gate_opened": opened,
            "gate_resolved": resolved,
            "declines": evidence.get("declines"),
            "no_write_landed": True,
            "run_status": evidence.get("run_status"),
        },
        0,
    )


def drive(
    session: DriverSession, provider: str, key: str, evidence: dict[str, Any]
) -> None:
    """The whole live pass. Returns early — never raises — on a blocked state.

    Every ``return`` below is a refusal to continue, and each one leaves
    ``evidence`` in the shape :func:`verdict` reads. Nothing sends a prompt
    until the two gates above it have passed.
    """

    evidence["target"] = session.rpc("status").get("target")
    evidence["user_data_subdir"] = session.user_data_subdir
    evidence["byok_provider"] = provider  # never the key itself
    evidence["bootstrap"] = bootstrap(session, provider, key)

    servers = _safe(session, "/v1/mcp/servers")
    server_entry = next(
        (
            entry
            for entry in _entries(servers, "servers", "items")
            if SLUG
            in {
                str(entry.get("name") or "").lower(),
                str(entry.get("display_name") or "").lower(),
            }
        ),
        None,
    )
    evidence["linear_server"] = _server_view(server_entry) if server_entry else None
    usable, blockers = usability(evidence["linear_server"])
    evidence["blockers"] = blockers
    if session.wait_for(CONNECTORS_RAIL, 20):
        session.click(CONNECTORS_RAIL)
        time.sleep(2)
        session.shot("cn09-01-connectors")
    if not usable:
        return

    evidence["composer_ready"] = open_new_thread(session)

    # The safety gate. The next line is the one that sends a real write prompt
    # against the user's real workspace, so it does not run until a write is
    # PROVEN to park rather than dispatch.
    evidence["autoexecute_risks"] = autoexecute_risks(session, evidence)
    if not evidence["composer_ready"]:
        evidence["autoexecute_risks"].append(
            "the composer never became visible, so the execution-mode pill "
            "could not be read and the run's approval posture is unknown"
        )
    if evidence["autoexecute_risks"]:
        return

    run_id = send_write_probe(session, evidence)
    park_and_decline(session, run_id, evidence)
    evidence["swept_pending"] = sweep_pending(session, run_id)

    final = settle_run(session, run_id)
    evidence["run_status"] = final.get("status")
    evidence["safe_error"] = final.get("safe_error")
    session.shot("cn09-04-outcome")

    stream = events(session, run_id)
    evidence["event_count"] = len(stream)
    evidence["tools"] = tool_calls(stream)
    evidence["approvals"] = list(approval_index(stream).values())
    evidence["gate_opened"] = gate_rows(stream, GATE_OPENED)
    evidence["gate_resolved"] = gate_rows(stream, GATE_RESOLVED)
    evidence["write_evidence"] = write_evidence(
        stream, gated_ops(evidence["gate_opened"])
    )
    evidence["answer"] = _redact(
        "\n".join(
            str(payload_of(event).get("text") or "")
            for event in stream
            if event_name(event) == "final_response"
        )
    )[-2000:]


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    evidence: dict[str, Any] = {}
    with lane(DEFAULT_LANE):
        session = DriverSession(name=SESSION_NAME, fresh=False)
        try:
            with session:
                drive(session, provider, key, evidence)
        finally:
            # The evidence is written even when an assertion blew up mid-run,
            # because the gate rows gathered before the failure are the whole
            # point of the journey.
            out = dump(session.run_dir, EVIDENCE_FILE, evidence)
            print(f"[cn-09] evidence -> {out}", flush=True)

    outcome, report, exit_code = verdict(evidence)
    result(JOURNEY, outcome, **report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
