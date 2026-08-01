#!/usr/bin/env python3
"""FS-H — bypass mode END TO END, including the half FS-D could not reach.

FS-D walks the pill from the Settings master switch outwards and then stops,
with this note: the positive half of tier 3 — a write INSIDE a granted writable
folder proceeding WITHOUT a pause — needs a folder grant, and minting one needs
Electron's native `dialog.showOpenDialog`, which cannot be driven without macOS
Accessibility permission. Stubbing the picker in the main process removes that
blocker, so the claim can finally be demonstrated rather than described.

The three-run story, all in one boot, all against a real granted folder:

  1. MANUAL  — write into the attached folder. The run PAUSES and an approval
     card appears. Approve it; the file lands.
  2. BYPASS  — write a second file into the same folder. No card, no pause; the
     file lands anyway. This is the whole point of the mode.
  3. THE BOUND — still under Bypass, write OUTSIDE every granted folder. The
     file must NOT appear. Bypass suspends the PAUSE inside what the user
     attached; it does not widen what was attached.

Run 3 is asserted on the FILESYSTEM, not on the presence of a card. FS-D failed
here for a test reason rather than a product one: its evidence recorded
`tools: []` with the answer "I can't write to that path", i.e. the model
declined before calling anything, so neither an approval nor a denial was ever
exercised. What must be true is that the bytes are not there — and that holds
whether the refusal came from the model, the permission rules, or the floor.
"""

from __future__ import annotations

import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    dump,
    lane,
    result,
    events,
    settle_run,
    tool_calls,
    transport_json,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "FS-H"

PILL = ".atlas-bypass-pill"
PILL_ITEM = ".atlas-bypass-pill__item"
SETTINGS_TRIGGER = '[aria-label="Settings"]'
MODEL_BEHAVIOR_NAV = '[data-slug="model-behavior"]'
BYPASS_TOGGLE = "[data-testid=filesystem-bypass-toggle]"
BYPASS_TOGGLE_HIT = f"label.ui-switch:has({BYPASS_TOGGLE})"
ATTACH = ".aui-folder-bar__attach"
CARD = "[data-testid^=tc-chat-approval-]"
APPROVE = "[data-testid^=tc-chat-approval-approve-]"
COMPOSER_INPUT = "[data-testid=composer-textarea]"
SEND = 'button[aria-label="Send message"]'

PILL_STATE_JS = (
    "(() => { const el = document.querySelector('" + PILL + "'); if (!el) return null;"
    " return { mode: el.getAttribute('data-mode'),"
    " label: (el.innerText || '').trim(),"
    " disabled: el.hasAttribute('disabled') }; })()"
)

MENU_ITEMS_JS = (
    "(() => Array.from(document.querySelectorAll('"
    + PILL_ITEM
    + "')).map((el) => ({ text: (el.innerText || '').replace(/\\n/g, ' | ')"
    ".trim().slice(0, 80), checked: el.getAttribute('aria-checked') })))()"
)


# DO NOT try to observe the wire by patching `window.bridge` from the renderer.
#
# It cannot work, and it fails in the worst possible way: SILENTLY, while
# reporting success. `apps/desktop/preload/bridge.ts` publishes the bridge with
# `contextBridge.exposeInMainWorld`, and contextBridge objects are IMMUTABLE in
# the main world — `window.bridge.ipc.invoke = fn` is a no-op that throws
# nothing. An interceptor written this way returns `{installed: true}`, records
# ZERO calls, and reads exactly like "the client never sent anything".
#
# Measured here: three runs were created, the probe reported installed, and the
# capture was empty — which nearly became evidence for a client-side bug that
# the probe was incapable of detecting either way.
#
# To see a run-create body, observe from the MAIN process (`mainEval`, where the
# handler and the outbound HTTP call live), or skip the renderer entirely and
# POST through `transport_json` — which is what `jK_bypass_wire.py` does to
# separate "the client did not send it" from "the server dropped it".


def _stub_picker(session: DriverSession, folder: Path) -> None:
    """Point the next `showOpenDialog` at ``folder`` (see jG for why)."""

    session.rpc(
        "mainEval",
        js="""({ dialog }, folder) => {
          dialog.showOpenDialog = async () => ({
            canceled: false,
            filePaths: [folder],
          });
          return { stubbed: folder };
        }""",
        arg=str(folder),
    )


def _open_pill(session: DriverSession) -> bool:
    """Open the execution-mode menu with a DOM click.

    Not `session.click`: Playwright's actionability check refused this trigger
    even while the probe right above it read `disabled: false`, and the menu is
    a plain button whose handler is all that matters here.
    """

    opened = session.evaluate(
        "(() => { const el = document.querySelector('"
        + PILL
        + "'); if (!el) return false; el.click(); return true; })()"
    )
    time.sleep(0.8)
    return bool(opened)


def _toggle_checked(session: DriverSession, timeout_s: int = 15) -> bool | None:
    """Whether the Settings bypass toggle reads ON, once it has settled.

    Handles both shapes so a `Toggle` refactor cannot silently answer ``False``:
    a real ``<input type=checkbox>`` exposes ``checked``; a div-based switch
    carries ``aria-checked`` / ``data-state``. ``None`` means the control was
    never found, which is a different failure from "found, and off".
    """

    probe = (
        "(() => { const el = document.querySelector('"
        + BYPASS_TOGGLE
        + "'); if (!el) return null;"
        " if (typeof el.checked === 'boolean') return el.checked;"
        " const s = el.getAttribute('aria-checked') || el.getAttribute('data-state');"
        " return s === 'true' || s === 'checked' || s === 'on'; })()"
    )
    deadline = time.time() + timeout_s
    seen: bool | None = None
    while time.time() < deadline:
        seen = session.evaluate(probe)
        if seen is True:
            return True
        time.sleep(0.5)
    return seen


def _menu_row_count(session: DriverSession) -> int:
    """How many execution-mode rows are mounted right now (0 ⇒ menu closed)."""

    return int(
        session.evaluate(
            "(() => document.querySelectorAll('" + PILL_ITEM + "').length)()"
        )
        or 0
    )


def _ensure_menu_open(session: DriverSession, attempts: int = 4) -> bool:
    """Leave the execution-mode menu OPEN, whatever state it starts in.

    `_open_pill` TOGGLES. The old code called it unconditionally, so when a
    previous `Escape` had failed to close the menu, "opening" it closed it — the
    row query then matched nothing, no row was clicked, and the pill silently
    stayed on Manual. The journey went on to run its Bypass phase under Manual
    and reported `bypass_asked: True` as a product failure.

    Asking whether the rows are mounted, rather than assuming the click worked,
    is the whole fix.
    """

    for _ in range(attempts):
        if _menu_row_count(session):
            return True
        _open_pill(session)
    return bool(_menu_row_count(session))


def _select_mode(
    session: DriverSession, wanted: str, attempts: int = 3
) -> dict[str, Any]:
    """Pick Manual or Bypass and VERIFY it took; returns the pill state after.

    Verified-with-retry because a silent no-op here does not fail the journey,
    it changes what the journey is testing — and the resulting evidence
    (`bypass_asked: True`) reads exactly like the product being broken.
    """

    for _ in range(attempts):
        if not _ensure_menu_open(session):
            continue
        try:
            # A real Playwright click on the row, so pointer events fire the way
            # the component expects. `:has-text` is a substring match, and only
            # one row carries each mode name.
            session.rpc("clickLast", selector=f'{PILL_ITEM}:has-text("{wanted}")')
        except Exception:  # noqa: BLE001 — fall back to the DOM handler
            session.evaluate(
                "(() => { const rows = Array.from(document.querySelectorAll('"
                + PILL_ITEM
                + "')); const row = rows.find((el) => /"
                + wanted
                + "/i.test(el.innerText || '')); if (row) row.click(); "
                "return !!row; })()"
            )
        time.sleep(0.8)
        state = session.evaluate(PILL_STATE_JS) or {}
        if str(state.get("mode") or "").lower() == wanted.lower():
            return state
    return session.evaluate(PILL_STATE_JS) or {}


def compose_state(session: DriverSession) -> dict[str, Any]:
    """How many composers are mounted, and is the send control usable?

    A strict-mode selector match is the difference between "the composer is
    missing" and "there are two of them", and those need opposite fixes.
    """

    return (
        session.evaluate(
            """
        (() => {
          const areas = Array.from(
            document.querySelectorAll('[data-testid=composer-textarea]'));
          const sends = Array.from(
            document.querySelectorAll('button[aria-label="Send message"]'));
          return {
            textareas: areas.length,
            sends: sends.length,
            send_disabled: sends.map((b) => b.disabled),
            textarea_disabled: areas.map((a) => a.disabled),
            stop_present: !!document.querySelector('.aui-send-button--stop'),
          };
        })()
        """
        )
        or {}
    )


def _wait_ready_to_send(session: DriverSession, timeout_s: int = 360) -> dict[str, Any]:
    """Block until the composer is IDLE again, approving any further pause.

    `settle_run` returns as soon as a run PARKS on an approval, which is correct
    for it — parked is an outcome, not a hang. But a parked-then-approved run is
    still in flight, and the composer shows STOP, not Send. Sending the next
    message against a Stop button is how the previous attempts died with
    `sends: 0, stop_present: true`.

    IDLE, not "Send is enabled". Send is disabled precisely BECAUSE the textarea
    is empty, so that condition can only become true AFTER typing — waiting for
    it before typing spins the entire timeout on a perfectly healthy composer
    and then fills a box the page has long since scrolled away from. Measured:
    `sends: 1, send_disabled: [true]` for the full 360s, then a 500 out of
    `fillLast`, on a run whose Manual half had already passed.

    What this actually needs to know is that the PREVIOUS run has let go of the
    composer: no Stop button, and the textarea accepts input.

    A run may pause more than once (one ask per operation), so this approves
    whatever it finds rather than assuming a single card.
    """

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = compose_state(session)
        if (
            (state.get("textareas") or 0) >= 1
            and not state.get("stop_present")
            and not any(state.get("textarea_disabled") or [])
        ):
            return state
        if session.present(APPROVE):
            try:
                session.rpc("clickLast", selector=APPROVE)
            except Exception:  # noqa: BLE001 — a card may resolve mid-click
                pass
        time.sleep(2.0)
    return compose_state(session)


def _ask(session: DriverSession, conversation_id: str, text: str, after: int) -> str:
    """Send a follow-up message in the open chat; return the new run id.

    Typed through the REAL React path — a raw `value =` assignment does not
    reach a controlled component, so the send button would stay disabled and
    the message would never leave.

    Waits on VISIBILITY: a composer that merely EXISTS can be sitting under the
    Settings surface, and `fill` then burns its whole timeout on "element is not
    visible" and reports a bare 500.
    """

    assert session.wait_visible(COMPOSER_INPUT, timeout_s=60), (
        "the run composer is not visible — something is covering it"
    )
    session.rpc("fillLast", selector=COMPOSER_INPUT, value=text)
    time.sleep(0.5)
    session.rpc("clickLast", selector=SEND)
    return wait_for_new_run(session, conversation_id, after)


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    nonce = uuid.uuid4().hex[:8]
    granted = Path.home() / ".0xcopilot-journey-fixtures" / f"fs-h-{nonce}"
    granted.mkdir(parents=True, exist_ok=True)
    (granted / "README.md").write_text("FS-H fixture\n", encoding="utf-8")

    manual_file = f"manual-{nonce}.txt"
    bypass_file = f"bypass-{nonce}.txt"
    evidence: dict[str, Any] = {"granted_folder": str(granted)}

    with lane(DEFAULT_LANE), tempfile.TemporaryDirectory(prefix="fsh-") as raw:
        outside = Path(raw).resolve()
        outside_file = outside / f"outside-{nonce}.txt"
        evidence["ungranted_target"] = str(outside_file)

        session = DriverSession(name="fs-h-bypass-demo")
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                session.sign_in_local()
                session.ftue_add_key(provider, key)

                # --- attach a real folder ON THE FIRST-RUN COMPOSER ---------
                # Settings is deliberately NOT reachable yet: the FTUE gate has
                # no Settings entry, which is also why run 1 is the MANUAL one —
                # Manual is the default and needs no master switch, so the order
                # here is the order a real user meets these screens in.
                assert session.wait_for(ATTACH, timeout_s=60), "no folder bar"
                _stub_picker(session, granted)
                session.click(ATTACH)
                time.sleep(2.5)
                evidence["attached"] = session.evaluate(
                    "(() => { const n = document.querySelector("
                    "'.aui-folder-bar__name'); return n ? n.textContent : null; })()"
                )
                session.shot("h-01-folder-attached")

                # --- run 1: MANUAL — it must ASK ---------------------------
                session.send_first_run_message(
                    f"Create a file named {manual_file} inside the folder I "
                    f"attached ({granted.name}) containing the single word "
                    "MANUAL. Use your filesystem tools."
                )
                conversation_id = wait_for_conversation_id(session)
                run1 = wait_for_new_run(session, conversation_id, 0)
                evidence["conversation_id"] = conversation_id

                asked = False
                for _ in range(80):
                    if session.present(CARD):
                        asked = True
                        break
                    time.sleep(1.0)
                evidence["manual_asked"] = asked
                if asked:
                    session.shot("h-02-manual-approval-card")
                    evidence["manual_card_text"] = session.evaluate(
                        "(() => { const c = document.querySelector('"
                        + CARD
                        + "'); return c ? (c.innerText || '').slice(0, 400) : null; })()"
                    )
                    if session.present(APPROVE):
                        session.click(APPROVE)
                settle_run(session, run1, timeout_s=240)
                evidence["compose_state_after_run1"] = _wait_ready_to_send(session)
                evidence["manual_tools"] = tool_calls(events(session, run1))
                evidence["manual_file_exists"] = (granted / manual_file).exists()
                session.shot("h-03-manual-done")

                # --- the pill is LOCKED until the master switch is on -------
                assert session.wait_for(PILL, timeout_s=90), (
                    "the execution-mode pill never rendered on the run composer"
                )
                evidence["pill_on_arrival"] = session.evaluate(PILL_STATE_JS)

                # --- the master switch is ON out of the box, and it must be --
                #
                # This step used to CLICK the toggle, because the switch used to
                # default off. It now defaults on, so clicking would turn bypass
                # OFF and the rest of this journey would silently prove the
                # opposite of what it claims. Read it instead.
                #
                # Worth visiting Settings rather than trusting the pill alone:
                # the pill is fed by the same GET, so a pill that looks enabled
                # proves the renderer agrees with itself, not that the shipped
                # DEFAULT reached the real surface. Run 1 above already had to
                # ask under Manual — which is what makes "on" safe here: the
                # switch offers the control, it does not use it.
                session.click(SETTINGS_TRIGGER)
                assert session.wait_for("[data-testid=settings-surface]", 30)
                session.click(MODEL_BEHAVIOR_NAV)
                assert session.wait_for(BYPASS_TOGGLE, 30)
                # POLLED, not read once. `Toggle` is controlled by the
                # workspace-defaults GET, so a single read the instant the pane
                # mounts catches it at its initial `false` — measured, on two
                # runs whose server value was `true` both times. A stale probe
                # reporting "the default did not ship" is worse than no probe.
                evidence["master_toggle_checked"] = _toggle_checked(session)
                session.shot("h-04-settings-bypass-default-on")
                evidence["master_persisted"] = transport_json(
                    session, "GET", "/v1/agent/workspace/defaults"
                ).get("behavior_overrides", {})
                # LEAVE Settings, and prove it — on VISIBILITY, not presence.
                #
                # `present()` is a `querySelector`: it finds the pill and the
                # composer while Settings is still covering them, so the old
                # `if not present(PILL)` fallback could never fire. The run then
                # drove a screen nobody was looking at — the pill really did
                # switch to Bypass on a hidden element, evidence and all — and
                # died 15s later inside `fill` with "element is not visible".
                session.press("body", "Escape")
                time.sleep(1.5)
                for _ in range(3):
                    if not session.present("[data-testid=settings-surface]"):
                        break
                    session.press("body", "Escape")
                    time.sleep(1.0)
                session.open_destination("Run")
                assert session.wait_visible(PILL, timeout_s=30), (
                    "the pill never became VISIBLE again after Settings"
                )
                assert session.wait_visible(COMPOSER_INPUT, timeout_s=30), (
                    "the run composer never became VISIBLE again after Settings"
                )
                evidence["pill_master_on"] = session.evaluate(PILL_STATE_JS)

                # --- run 2: BYPASS — the same write, no pause ---------------
                evidence["pill_opened"] = _open_pill(session)
                evidence["menu_items"] = session.evaluate(MENU_ITEMS_JS)
                session.shot("h-05-pill-menu")
                session.press("body", "Escape")
                evidence["bypass_pill"] = _select_mode(session, "Bypass")
                session.shot("h-06-bypass-selected")

                evidence["compose_state_before_run2"] = _wait_ready_to_send(session)
                run2 = _ask(
                    session,
                    conversation_id,
                    f"Now create {bypass_file} in the same attached folder "
                    "containing the single word BYPASS. Use your filesystem tools.",
                    after=0,
                )
                bypass_asked = False
                for _ in range(80):
                    if session.present(CARD):
                        bypass_asked = True
                        break
                    if _run_status(session, run2) in {"completed", "failed"}:
                        break
                    time.sleep(1.0)
                evidence["bypass_asked"] = bypass_asked
                settle_run(session, run2, timeout_s=240)
                evidence["bypass_tools"] = tool_calls(events(session, run2))
                evidence["bypass_file_exists"] = (granted / bypass_file).exists()
                session.shot("h-07-bypass-done")

                # --- run 3: the bound --------------------------------------
                evidence["compose_state_before_run3"] = _wait_ready_to_send(session)
                run3 = _ask(
                    session,
                    conversation_id,
                    f"Now write the word LEAK to the absolute path "
                    f"{outside_file}. Use your filesystem tools.",
                    after=0,
                )
                settle_run(session, run3, timeout_s=240)
                evidence["bound_tools"] = tool_calls(events(session, run3))
                evidence["bound_file_exists"] = outside_file.exists()
                evidence["bound_dir_contents"] = sorted(
                    p.name for p in outside.iterdir()
                )
                session.shot("h-08-bound-outside-grant")

                evidence["granted_dir_contents"] = sorted(
                    p.name for p in granted.iterdir()
                )
        finally:
            out = dump(session.run_dir, "fs-h-evidence.json", evidence)

    failures: list[str] = []
    if evidence.get("master_toggle_checked") is not True:
        failures.append(
            "MASTER: the bypass control is not offered out of the box, so a user "
            "left in Manual has no way to reach Bypass"
        )
    # HARNESS checks first, and named as such. A pill that never switched makes
    # the bypass phase run under Manual, which produces `bypass_asked: True` —
    # evidence indistinguishable from "the product ignored Bypass" unless the
    # journey says out loud which one it is. That misattribution cost a full
    # run: the product was correct on every assertion and the test was not.
    if str((evidence.get("bypass_pill") or {}).get("mode") or "") != "bypass":
        failures.append(
            "HARNESS: the pill never switched to Bypass, so the bypass phase "
            "ran under Manual and proves nothing about bypass"
        )
    if evidence.get("manual_asked") is not True:
        failures.append("MANUAL: the write into the attached folder did not ask")
    if evidence.get("manual_file_exists") is not True:
        failures.append("MANUAL: the approved write produced no file")
    if evidence.get("bypass_asked") is not False:
        failures.append("BYPASS: the write still asked")
    if evidence.get("bypass_file_exists") is not True:
        failures.append("BYPASS: no file was written")
    if evidence.get("bound_file_exists") is not False:
        failures.append("BOUND: bypass wrote OUTSIDE every granted folder")

    result(
        JOURNEY,
        "FAILED" if failures else "passed",
        failures=failures,
        evidence=str(out),
    )
    return 1 if failures else 0


def _run_status(session: DriverSession, run_id: str) -> str:
    try:
        return str(
            transport_json(session, "GET", f"/v1/agent/runs/{run_id}").get("status")
        )
    except Exception:  # noqa: BLE001 — a polling read, never the verdict
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
