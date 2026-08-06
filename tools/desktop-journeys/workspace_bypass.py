#!/usr/bin/env python3
"""workspace-bypass — the execution-mode pill, and what a grant actually buys.

TWO boots, because the lane is fixed when the supervisor launches and cannot
change mid-session:

* **default lane** (WB-1…WB-9) — what a real desktop user gets.
  `service-env.ts` defaults OPERATION_GATEWAY_MODE to "off" and lets
  WORKSPACE_EFFECT_MODE follow it, so setting NOTHING is the shipped posture.
* **enforce lane** (WB-10…WB-11) — the operator opt-in. The same "an attached
  folder stops asking" claim, in the lane that turns the gateway on.

Ordering inside the default boot is not cosmetic. The folder bar exists only
BEFORE the first message, so WB-1 attaches every fixture grant while it is
still on screen. WB-2 must run under Manual (the default) before WB-4 selects
Bypass, or the manual half proves nothing.

    python3 tools/desktop-journeys/workspace_bypass.py

Folds in: filesystem-access/{jD_bypass, jH_bypass_demo, jK_bypass_wire,
jI_workspace_writeback, attached_folder_stops_asking, jB_attached_folder_is_silent}.

## The master-switch contradiction, deliberately not resolved here

`jD` and `jH` shipped encoding OPPOSITE expectations of a fresh install, and
[RUN-RESULTS.md](./RUN-RESULTS.md) records it as NEEDS A PRODUCT DECISION:

* `agent_runtime/execution/filesystem_bypass.py:177` sets
  `DEFAULT_FILESYSTEM_BYPASS_OFFERED = True`, which `jH` encodes ("the master
  switch is ON out of the box, and it must be").
* The same module's docstring (line ~24) states the master switch is
  "default **off**", which `jD` encodes (a fresh install shows a DISABLED
  Manual pill offering no Bypass anywhere).

They cannot both pass, so asserting either side here would just pick a winner
by fiat. WB-3 instead asserts the invariant that holds under EITHER default —
**the options the pill offers must agree with the master switch it reads** —
and RECORDS the observed default in its evidence. Resolve the contradiction in
the product, then tighten this phase to the side you chose.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Final

from _lib import (
    DriverSession,
    JourneyPlan,
    byok_provider,
    preflight_staged_runtime,
    require,
)
from _workspace_lib import (
    DEFAULT_LANE,
    ENFORCE_LANE,
    TERMINAL,
    approval_events,
    assistant_text,
    attach_folder,
    dump,
    events,
    run_status,
    runs_for,
    settle_run,
    tool_calls,
    transport_json,
    wait_for_conversation_id,
    wait_for_new_run,
)

STATE: dict[str, Any] = {}


def log(line: str) -> None:
    print(f"  {line}", flush=True)


def _fail(failures: list[str]) -> None:
    if failures:
        raise AssertionError("; ".join(failures))


PILL = ".atlas-bypass-pill"


PILL_ITEM = ".atlas-bypass-pill__item"


SETTINGS_TRIGGER = '[aria-label="Settings"]'


MODEL_BEHAVIOR_NAV = '[data-slug="model-behavior"]'


BYPASS_TOGGLE = "[data-testid=filesystem-bypass-toggle]"


BYPASS_TOGGLE_HIT = f"label.ui-switch:has({BYPASS_TOGGLE})"


# Both forms: `AppRail` labels the item "Run (1)" while a run is live, so an
# exact match stops resolving exactly when there is a run to go back to.
RUN_RAIL = (
    '[data-destination][aria-label="Run"], [data-destination][aria-label^="Run ("]'
)


CARD_SELECTOR = "[data-testid^=tc-chat-approval-]"


PILL_STATE_JS = (
    "(() => { const el = document.querySelector('" + PILL + "'); if (!el) return null;"
    " return { mode: el.getAttribute('data-mode'),"
    " label: (el.innerText || '').trim(),"
    " disabled: el.hasAttribute('disabled'),"
    " haspopup: el.getAttribute('aria-haspopup'),"
    " tooltip: el.getAttribute('data-tooltip') }; })()"
)


BYPASS_ANYWHERE_JS = (
    "(() => Array.from(document.querySelectorAll('button,[role=menuitemradio],"
    "[role=menuitem],[aria-label]')).filter((el) =>"
    " /bypass/i.test(el.textContent || '') ||"
    " /bypass/i.test(el.getAttribute('aria-label') || ''))"
    ".map((el) => ((el.textContent || el.getAttribute('aria-label') || '')"
    ".trim().slice(0, 60))))()"
)


MENU_ITEMS_JS = (
    "(() => Array.from(document.querySelectorAll('"
    + PILL_ITEM
    + "')).map((el) => ({ text: (el.innerText || '').replace(/\\n/g, ' | ')"
    ".trim().slice(0, 80), checked: el.getAttribute('aria-checked') })))()"
)


ATTACH = ".aui-folder-bar__attach"


CARD = "[data-testid^=tc-chat-approval-]"


APPROVE = "[data-testid^=tc-chat-approval-approve-]"


COMPOSER_INPUT = "[data-testid=composer-textarea]"


SEND = 'button[aria-label="Send message"]'


def stub_picker(session: DriverSession, folder: Path) -> None:
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


def open_pill(session: DriverSession) -> bool:
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


def toggle_checked(session: DriverSession, timeout_s: int = 15) -> bool | None:
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


def menu_row_count(session: DriverSession) -> int:
    """How many execution-mode rows are mounted right now (0 ⇒ menu closed)."""

    return int(
        session.evaluate(
            "(() => document.querySelectorAll('" + PILL_ITEM + "').length)()"
        )
        or 0
    )


def ensure_menu_open(session: DriverSession, attempts: int = 4) -> bool:
    """Leave the execution-mode menu OPEN, whatever state it starts in.

    `open_pill` TOGGLES. The old code called it unconditionally, so when a
    previous `Escape` had failed to close the menu, "opening" it closed it — the
    row query then matched nothing, no row was clicked, and the pill silently
    stayed on Manual. The journey went on to run its Bypass phase under Manual
    and reported `bypass_asked: True` as a product failure.

    Asking whether the rows are mounted, rather than assuming the click worked,
    is the whole fix.
    """

    for _ in range(attempts):
        if menu_row_count(session):
            return True
        open_pill(session)
    return bool(menu_row_count(session))


def select_mode(
    session: DriverSession, wanted: str, attempts: int = 3
) -> dict[str, Any]:
    """Pick Manual or Bypass and VERIFY it took; returns the pill state after.

    Verified-with-retry because a silent no-op here does not fail the journey,
    it changes what the journey is testing — and the resulting evidence
    (`bypass_asked: True`) reads exactly like the product being broken.
    """

    for _ in range(attempts):
        if not ensure_menu_open(session):
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


def wait_ready_to_send(session: DriverSession, timeout_s: int = 360) -> dict[str, Any]:
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


def ask(session: DriverSession, conversation_id: str, text: str, after: int) -> str:
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


def run_status_of(session: DriverSession, run_id: str) -> str:
    try:
        return str(
            transport_json(session, "GET", f"/v1/agent/runs/{run_id}").get("status")
        )
    except Exception:  # noqa: BLE001 — a polling read, never the verdict
        return "unknown"


GOAL = "Say READY and nothing else."


def create_run_with_bypass(
    session: DriverSession,
    conversation_id: str,
    selection: dict[str, str] | None,
) -> dict[str, Any]:
    """POST one run, with or without a bypass selection, and read it back."""

    # `user_input`, not `goal`. The composer's RunStartRequest calls it `goal`
    # and `buildRunCreateBody` renames it on the way out; posting the client-side
    # name straight at the API is a 422.
    body: dict[str, Any] = {"conversation_id": conversation_id, "user_input": GOAL}
    if selection is not None:
        body["filesystem_bypass"] = selection
    created = transport_json(session, "POST", "/v1/agent/runs", body=body)
    run_id = (created or {}).get("run_id")
    if not isinstance(run_id, str) or run_id == "":
        return {"sent": selection, "error": "no run_id", "created": created}

    # The sealed decision is on the run record. Read it back through the same
    # transport rather than off disk, so this journey asserts what the PRODUCT
    # reports about itself.
    sealed: Any = None
    for _ in range(20):
        run = transport_json(session, "GET", f"/v1/agent/runs/{run_id}")
        context = (run or {}).get("runtime_context") or {}
        sealed = context.get("filesystem_bypass")
        if sealed is not None:
            break
        time.sleep(1.0)
    return {"sent": selection, "run_id": run_id, "sealed": sealed}


SEED_CSV = "region,q3\nnorth,120\nsouth,90\n"


APPLIED_EVENTS = ("write.applied", "effect.applied", "workspace.")


def stub_dialogs(session: DriverSession, folder: Path) -> None:
    """Point the picker at ``folder`` and auto-accept the native approval.

    The picker stub is jG's. The `showMessageBox` stub is NEW and is the reason
    this can run unattended at all: the workspace commit authority raises a
    NATIVE approval, which Playwright cannot see and System Events will not
    drive without Accessibility (-25211).

    Stubbing a CONSENT dialog is a bigger claim than stubbing a file picker, so
    state it plainly: this spike proves the MECHANISM (do bytes land), not the
    consent UX. That half is already covered by FS-A, which clicks a real
    approval and reads the granted folder afterwards.
    """

    session.rpc(
        "mainEval",
        js="""({ dialog }, folder) => {
          dialog.showOpenDialog = async () => ({
            canceled: false,
            filePaths: [folder],
          });
          // Index 0 is the affirmative button on every showMessageBox this app
          // raises; `response` is what Electron returns for the click.
          dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false });
          return { stubbed: folder };
        }""",
        arg=str(folder),
    )


def digest_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approve_everything(session: DriverSession, seconds: int = 180) -> int:
    """Approve every card that appears; return how many were clicked.

    A single write can pause more than once (the ask, then the commit), so this
    does not assume one card.
    """

    clicked = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        if session.present(CARD) and session.present(APPROVE):
            try:
                session.rpc("clickLast", selector=APPROVE)
                clicked += 1
            except Exception:  # noqa: BLE001 — a card can resolve mid-click
                pass
        time.sleep(2.0)
    return clicked


GRANT_BLOCK_KEY: Final = "workspace_grant"


def read_prompt(path: Path) -> str:
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


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in_and_key(s: DriverSession) -> None:
    preflight_staged_runtime()
    provider, key = byok_provider()
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    STATE["target"] = s.rpc("status").get("target")
    s.sign_in_local()
    s.ftue_add_key(provider, key)


# ── default lane ─────────────────────────────────────────────────────────────
def wb1_grants_attached_while_the_bar_exists(s: DriverSession) -> None:
    """Attach every fixture grant NOW — the folder bar dies with the first message.

    Also records the pill on the FIRST-RUN composer. It did not carry one when
    these journeys were written, so a session that never sent a message saw no
    execution-mode control at all; the probe is what would catch that
    regressing.
    """

    nonce = uuid.uuid4().hex[:8]
    STATE["nonce"] = nonce
    STATE["pill_on_first_run_composer"] = s.evaluate(PILL_STATE_JS)
    s.shot("first-run-composer")

    granted = Path.home() / ".0xcopilot-journey-fixtures" / f"wb-{nonce}"
    granted.mkdir(parents=True, exist_ok=True)
    (granted / "README.md").write_text("workspace-bypass fixture\n", encoding="utf-8")
    writeback = Path.home() / ".0xcopilot-journey-fixtures" / f"wb-write-{nonce}"
    writeback.mkdir(parents=True, exist_ok=True)
    seed = writeback / "seed.csv"
    seed.write_text(SEED_CSV, encoding="utf-8")
    STATE.update(
        {
            "granted": granted,
            "writeback": writeback,
            "seed": seed,
            "digest_before": digest_of(seed),
        }
    )

    require(s.wait_for(ATTACH, timeout_s=60), "no folder bar on the first-run composer")
    for folder in (granted, writeback):
        stub_picker(s, folder)
        s.click(ATTACH)
        time.sleep(2.5)
    attached = s.evaluate(
        "(() => { const n = document.querySelector('.aui-folder-bar__name');"
        " return n ? n.textContent : null; })()"
    )
    STATE["attached"] = attached
    s.shot("folders-attached")
    assert attached, "no folder chip after attaching — the picker stub did not take"
    log(f"attached {granted.name} and {writeback.name}; chip reads {attached!r}")


def wb2_manual_write_into_a_granted_folder_asks(s: DriverSession) -> None:
    """Run 1 is MANUAL, and Manual must ASK even inside a folder you attached.

    Runs before anything selects Bypass. Manual is the default and needs no
    master switch, so this is also the order a real user meets these screens in
    — Settings is not even reachable from the FTUE gate.
    """

    granted = STATE.get("granted")
    require(granted, "needs the grant WB-1 attaches")
    manual_file = f"manual-{STATE['nonce']}.txt"
    STATE["manual_file"] = manual_file

    s.send_first_run_message(
        f"Create a file named {manual_file} inside the folder I attached "
        f"({granted.name}) containing the single word MANUAL. Use your "
        "filesystem tools."
    )
    conversation_id = wait_for_conversation_id(s)
    run1 = wait_for_new_run(s, conversation_id, 0)
    STATE["conversation_id"] = conversation_id

    asked = False
    for _ in range(80):
        if s.present(CARD):
            asked = True
            break
        time.sleep(1.0)
    if asked:
        s.shot("manual-approval-card")
        if s.present(APPROVE):
            s.click(APPROVE)
    settle_run(s, run1, timeout_s=240)
    wait_ready_to_send(s)
    s.shot("manual-done")

    failures: list[str] = []
    if not asked:
        failures.append("MANUAL: the write into the attached folder did not ask")
    if not (granted / manual_file).exists():
        failures.append("MANUAL: the approved write produced no file")
    log(f"manual tools = {tool_calls(events(s, run1))}")
    _fail(failures)


def wb3_the_pill_agrees_with_the_master_switch(s: DriverSession) -> None:
    """The options the pill offers must agree with the master switch it reads.

    See this module's docstring: `jD` and `jH` encode opposite expectations of
    the fresh-install default, so the DEFAULT itself is recorded rather than
    asserted. What is asserted holds either way — a disabled pill must not
    offer Bypass ANYWHERE (not greyed, not in the accessibility tree), and an
    enabled one must.
    """

    require(
        s.wait_for(PILL, timeout_s=90), "no execution-mode pill on the run composer"
    )
    evidence: dict[str, Any] = {}
    pill = s.evaluate(PILL_STATE_JS) or {}
    evidence["pill_as_shipped"] = pill
    evidence["defaults_as_shipped"] = (
        transport_json(s, "GET", "/v1/agent/workspace/defaults") or {}
    ).get("behavior_overrides", {})
    s.shot("pill-as-shipped")

    # A locked pill must not open anything. Playwright refuses to click a
    # disabled control, and that refusal IS the evidence — record it rather
    # than letting it end the phase.
    try:
        s.click(PILL)
        evidence["click"] = "accepted"
    except Exception as exc:  # noqa: BLE001
        evidence["click"] = f"refused: {type(exc).__name__}"
    time.sleep(0.4)
    evidence["menu_items"] = s.evaluate(MENU_ITEMS_JS)
    evidence["bypass_anywhere"] = s.evaluate(BYPASS_ANYWHERE_JS)
    s.press("body", "Escape")

    failures: list[str] = []
    if not pill:
        failures.append("no execution-mode pill rendered at all")
    else:
        disabled = bool(pill.get("disabled"))
        offers_bypass = bool(evidence["bypass_anywhere"])
        # THE invariant, true under either default.
        if disabled and offers_bypass:
            failures.append(
                "Bypass is reachable while the pill is DISABLED: "
                + json.dumps(evidence["bypass_anywhere"])[:200]
            )
        if disabled and pill.get("mode") != "manual":
            failures.append(f"a disabled pill reports mode {pill.get('mode')!r}")
        if not disabled and not offers_bypass:
            failures.append("the pill is enabled but offers no Bypass anywhere")
    dump(s.run_dir, "wb3-master-switch.json", evidence)
    log(
        f"OBSERVED master default: pill.disabled={pill.get('disabled')!r} "
        f"filesystem_bypass_enabled="
        f"{evidence['defaults_as_shipped'].get('filesystem_bypass_enabled')!r} "
        "— recorded, not asserted (see the module docstring)"
    )
    STATE["pill_disabled_as_shipped"] = bool(pill.get("disabled"))
    _fail(failures)


def wb4_the_settings_toggle_persists_and_reaches_the_pill(s: DriverSession) -> None:
    """Turning the master switch ON in the real Settings surface reaches the pill.

    Three probes, escalating, because WHERE the switch stops travelling is the
    diagnosis: `useDesktopComposerBypass` reads the master once per mount, so
    does returning to the chat suffice, or does it take a renderer reload?
    """

    evidence: dict[str, Any] = {}
    s.click(SETTINGS_TRIGGER)
    require(s.wait_for("[data-testid=settings-surface]", 30), "Settings never opened")
    s.click(MODEL_BEHAVIOR_NAV)
    require(
        s.wait_for(BYPASS_TOGGLE, 30),
        "Model & behavior has no filesystem-bypass toggle",
    )
    s.shot("settings-model-behavior")
    # POLLED, not read once: `Toggle` is controlled by the workspace-defaults
    # GET, so a single read the instant the pane mounts catches it at its
    # initial `false` — measured, on two runs whose server value was `true`.
    evidence["checked_on_arrival"] = toggle_checked(s)
    if evidence["checked_on_arrival"] is not True:
        s.click(BYPASS_TOGGLE_HIT)
        time.sleep(3.5)
        evidence["checked_after_click"] = toggle_checked(s)
    s.shot("toggle-on")
    evidence["persisted"] = (
        transport_json(s, "GET", "/v1/agent/workspace/defaults") or {}
    ).get("behavior_overrides", {})

    # LEAVE Settings, and prove it — on VISIBILITY, not presence. `present()`
    # is a querySelector: it finds the pill while Settings still covers it, so
    # a presence-based check drove a screen nobody was looking at. The pill
    # really did switch on a hidden element, evidence and all, and the next
    # fill died 15s later with "element is not visible".
    s.press("body", "Escape")
    time.sleep(1.5)
    for _ in range(3):
        if not s.present("[data-testid=settings-surface]"):
            break
        s.press("body", "Escape")
        time.sleep(1.0)
    s.open_destination("Run")

    def _live(probe: Any) -> bool:
        # An ABSENT pill is not an enabled one: `{}` has no `disabled` key, so
        # ask it the other way round.
        return isinstance(probe, dict) and probe.get("disabled") is False

    conversation_id = STATE.get("conversation_id")
    if not s.wait_visible(PILL, timeout_s=30) and conversation_id:
        s.evaluate(f"window.location.hash = '#/convo/{conversation_id}'")
        time.sleep(4)
        s.wait_visible(PILL, timeout_s=30)
    live = s.evaluate(PILL_STATE_JS)
    evidence["reached_via"] = "returning to the chat" if _live(live) else None

    if not _live(live):
        # A reload remounts every composer with the conversation still bound —
        # the cheapest thing short of relaunching, and the honest test of
        # "is this mount-scoped?".
        s.evaluate("window.location.reload()")
        time.sleep(12)
        if not s.wait_visible(PILL, timeout_s=60) and conversation_id:
            s.evaluate(f"window.location.hash = '#/convo/{conversation_id}'")
            time.sleep(4)
            s.wait_visible(PILL, timeout_s=60)
        live = s.evaluate(PILL_STATE_JS)
        evidence["pill_after_reload"] = live
        s.shot("after-reload")
        if _live(live):
            evidence["reached_via"] = "a renderer reload"

    dump(s.run_dir, "wb4-master-toggle.json", evidence)
    failures: list[str] = []
    if evidence["persisted"].get("filesystem_bypass_enabled") is not True:
        failures.append("the Settings toggle did not persist filesystem_bypass_enabled")
    if not _live(live):
        failures.append(
            "the master switch never reached the composer pill (neither on "
            "return to the chat nor after a renderer reload)"
        )
    else:
        log(f"the master switch reached the pill by {evidence['reached_via']}")
    _fail(failures)


def wb5_selecting_bypass_changes_the_pill(s: DriverSession) -> None:
    """DEPENDS ON WB-4. The menu offers Bypass, and choosing it takes."""

    require(
        s.evaluate(PILL_STATE_JS) or {},
        "needs a live pill from WB-4",
    )
    open_pill(s)
    items = s.evaluate(MENU_ITEMS_JS) or []
    s.shot("pill-menu")
    s.press("body", "Escape")
    assert any("Bypass" in str(item.get("text", "")) for item in items), (
        f"with the master ON the pill menu still offers no Bypass: {items!r}"
    )
    pill = select_mode(s, "Bypass")
    STATE["bypass_pill"] = pill
    s.shot("bypass-selected")
    assert str((pill or {}).get("mode") or "") == "bypass", (
        f"selecting Bypass did not change the pill's mode: {pill!r}"
    )


def wb6_bypass_writes_inside_a_grant_without_pausing(s: DriverSession) -> None:
    """DEPENDS ON WB-5. The same write as WB-2, now with no pause.

    If the pill never switched, this phase would run under Manual and produce
    `asked=True` — evidence indistinguishable from "the product ignored
    Bypass". WB-5 owns that distinction, and this skips rather than
    misattribute.
    """

    require(
        str((STATE.get("bypass_pill") or {}).get("mode") or "") == "bypass",
        "the pill never switched to Bypass, so this would run under Manual",
    )
    granted = STATE["granted"]
    bypass_file = f"bypass-{STATE['nonce']}.txt"

    wait_ready_to_send(s)
    run2 = ask(
        s,
        STATE["conversation_id"],
        f"Now create {bypass_file} in the same attached folder containing the "
        "single word BYPASS. Use your filesystem tools.",
        after=0,
    )
    asked = False
    for _ in range(80):
        if s.present(CARD):
            asked = True
            break
        if run_status_of(s, run2) in {"completed", "failed"}:
            break
        time.sleep(1.0)
    settle_run(s, run2, timeout_s=240)
    s.shot("bypass-done")

    failures: list[str] = []
    if asked:
        failures.append("BYPASS: the write still asked")
    if not (granted / bypass_file).exists():
        failures.append("BYPASS: no file was written")
    log(f"bypass tools = {tool_calls(events(s, run2))}")
    _fail(failures)


def wb7_bypass_is_bounded_by_the_grant(s: DriverSession) -> None:
    """DEPENDS ON WB-5. Bypass must not write OUTSIDE every granted folder.

    The bound, from both sides: no file appears on disk, and the ungranted
    write still raises an approval rather than being answered silently.
    """

    require(
        str((STATE.get("bypass_pill") or {}).get("mode") or "") == "bypass",
        "the pill never switched to Bypass",
    )
    nonce = STATE["nonce"]
    evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="wb-outside-") as raw:
        outside = Path(raw).resolve()
        outside_file = outside / f"outside-{nonce}.txt"
        evidence["ungranted_target"] = str(outside_file)

        run3 = ask(
            s,
            STATE["conversation_id"],
            f"Now write the word LEAK to the absolute path {outside_file}. "
            "Use your filesystem tools.",
            after=0,
        )
        observed: list[str] = []
        card_seen = False
        deadline = time.time() + 180
        while time.time() < deadline:
            state = str(run_status(s, run3).get("status"))
            if not observed or observed[-1] != state:
                observed.append(state)
            if s.present(CARD_SELECTOR):
                card_seen = True
            if state in TERMINAL:
                break
            time.sleep(1)
        s.shot("bound-outside-grant")
        stream = events(s, run3)
        evidence.update(
            {
                "status_trace": observed,
                "card_rendered": card_seen,
                "tools": tool_calls(stream),
                "approvals": approval_events(stream),
                "file_created_on_disk": outside_file.exists(),
                "dir_contents": sorted(p.name for p in outside.iterdir()),
                "answer": (assistant_text(s, run3) or "")[-800:],
            }
        )
    dump(s.run_dir, "wb7-bound.json", evidence)

    failures: list[str] = []
    if evidence["file_created_on_disk"]:
        failures.append("BOUND: bypass wrote OUTSIDE every granted folder")
    if not evidence["approvals"]:
        failures.append("BOUND: an ungranted write under Bypass raised no approval")
    _fail(failures)


def wb8_staged_workspace_writes_land_bytes_on_disk(s: DriverSession) -> None:
    """THE SPIKE: does a staged workspace write actually change the file?

    One question, and the answer decides how much code the gen-UI-over-files
    work needs. Deliberately the HOST path: the earlier phrasing pointed at the
    VIRTUAL `/workspace/<mount>/` route, which lands on the read-only workspace
    backend, so the model read the file and reached for `publish_artifact`. The
    grant-honouring write rule governs HOST paths.

    Stubs the native approval as well as the picker, and that is a bigger claim
    than stubbing a file chooser, so state it plainly: this phase proves the
    MECHANISM (do bytes land), not the consent UX. That half is WC-4 in
    `workspace_consent.py`, which clicks a real approval and reads the granted
    folder afterwards.
    """

    seed = STATE.get("seed")
    require(seed, "needs the writeback fixture WB-1 seeds")
    evidence: dict[str, Any] = {
        "target_file": str(seed),
        "digest_before": STATE["digest_before"],
    }
    # The workspace commit authority raises a NATIVE approval that Playwright
    # cannot see and System Events will not drive without Accessibility
    # (-25211). Without this the run parks on an invisible dialog forever.
    stub_dialogs(s, STATE["writeback"])

    run_id = ask(
        s,
        STATE["conversation_id"],
        f"Read the file at {seed} and then write it back to that exact same "
        "path with one extra column named `note` whose value is `checked` on "
        "every row. Use your filesystem tools.",
        after=0,
    )
    evidence["approvals_clicked"] = approve_everything(s)
    settle_run(s, run_id, timeout_s=300)
    s.shot("after-writeback")

    stream = events(s, run_id)
    evidence["tools"] = tool_calls(stream)
    evidence["applied_events"] = sorted(
        {
            str(e.get("event_type") or e.get("type") or "")
            for e in stream
            if any(
                marker in str(e.get("event_type") or e.get("type") or "")
                for marker in APPLIED_EVENTS
            )
        }
    )
    evidence["digest_after"] = digest_of(seed)
    evidence["bytes_after"] = (
        seed.read_text(encoding="utf-8") if seed.is_file() else None
    )
    dump(s.run_dir, "wb8-writeback.json", evidence)

    assert evidence["digest_before"] != evidence["digest_after"], (
        "the staged workspace write never changed the file on disk; tools="
        f"{evidence['tools']} applied_events={evidence['applied_events']} "
        f"approvals_clicked={evidence['approvals_clicked']}"
    )


def wb9_the_bypass_selection_survives_the_wire(s: DriverSession) -> None:
    """The BACKEND half, alone: does a run-create carrying `filesystem_bypass`
    come back with a sealed decision of BYPASS?

    Splits a bug three UI runs could not. A green result here says the backend
    is fine and the renderer is not sending the selection.
    """

    evidence: dict[str, Any] = {}
    evidence["workspace_defaults"] = (
        transport_json(s, "GET", "/v1/agent/workspace/defaults") or {}
    ).get("behavior_overrides")
    conversation = transport_json(s, "POST", "/v1/agent/conversations", body={})
    conversation_id = (conversation or {}).get("conversation_id")
    assert isinstance(conversation_id, str) and conversation_id, (
        f"no conversation_id: {conversation!r}"
    )
    evidence["baseline"] = create_run_with_bypass(s, conversation_id, None)
    evidence["message_scope"] = create_run_with_bypass(
        s, conversation_id, {"message": "bypass"}
    )
    evidence["run_scope"] = create_run_with_bypass(
        s, conversation_id, {"run": "bypass"}
    )
    dump(s.run_dir, "wb9-wire.json", evidence)

    def mode(key: str) -> str:
        return str(((evidence.get(key) or {}).get("sealed") or {}).get("mode") or "")

    def source(key: str) -> str:
        return str(((evidence.get(key) or {}).get("sealed") or {}).get("source") or "")

    failures: list[str] = []
    if mode("baseline") != "manual":
        failures.append(
            f"BASELINE: a run with no selection sealed {mode('baseline')!r}, so "
            "the other two probes prove nothing"
        )
    if mode("message_scope") != "bypass":
        failures.append(
            "MESSAGE SCOPE: a run-create carrying "
            "`filesystem_bypass={'message':'bypass'}` sealed "
            f"{mode('message_scope')!r}/{source('message_scope')!r} — the server "
            "dropped or refused the selection"
        )
    if mode("run_scope") != "bypass":
        failures.append(
            "RUN SCOPE: a run-create carrying `filesystem_bypass={'run':'bypass'}` "
            f"sealed {mode('run_scope')!r}/{source('run_scope')!r}"
        )
    log(
        "verdict: "
        + (
            "backend OK — a failing UI means the renderer is not sending it"
            if not failures
            else "the backend drops or refuses the selection"
        )
    )
    _fail(failures)


# ── the attached-folder claim, in each lane ──────────────────────────────────
def _attached_folder_is_silent(s: DriverSession, lane_name: str) -> None:
    """An ATTACHED folder stops asking; an ungranted one still does not.

    Turn 2 deliberately clicks NOTHING and sits watching: a run that resolves
    its own approval with no user present is the thing worth catching, and only
    stillness can catch it.
    """

    nonce = uuid.uuid4().hex[:12]
    evidence: dict[str, Any] = {"lane": lane_name}
    with (
        tempfile.TemporaryDirectory(prefix="wb-attached-") as attached_raw,
        tempfile.TemporaryDirectory(prefix="wb-ungranted-") as ungranted_raw,
    ):
        attached = Path(attached_raw).resolve()
        ungranted = Path(ungranted_raw).resolve()
        attached_canary = f"attached-{nonce}"
        ungranted_canary = f"ungranted-{nonce}"
        (attached / "canary.txt").write_text(attached_canary, encoding="utf-8")
        (ungranted / "canary.txt").write_text(ungranted_canary, encoding="utf-8")

        # Attaching is the ONLY way in, and it is a NATIVE dialog. When the host
        # denies the controlling process Accessibility no keystroke can reach
        # that sheet — so record the block and still run turn 2, which needs no
        # grant and answers the other half.
        try:
            evidence["grant_id"] = attach_folder(
                s, attached, mode="read_only", label=f"{lane_name} fixture"
            )
            s.shot(f"{lane_name}-folder-attached")
        except Exception as exc:  # noqa: BLE001
            evidence["attach_blocked"] = repr(exc)[:300]
            s.shot(f"{lane_name}-attach-blocked")

        conversation_id: str | None = None
        if evidence.get("grant_id"):
            s.send(read_prompt(attached / "canary.txt"))
            conversation_id = wait_for_conversation_id(s)
            run_id = wait_for_new_run(s, conversation_id, 0)
            final = settle_run(s, run_id)
            time.sleep(1.5)
            s.shot(f"{lane_name}-attached-read")
            stream = events(s, run_id)
            answer = assistant_text(s, run_id) or ""
            evidence["attached"] = {
                "status": final.get("status"),
                "tools": tool_calls(stream),
                "approvals": approval_events(stream),
                "grant_asks": [
                    a
                    for a in approval_events(stream)
                    if GRANT_BLOCK_KEY in a.get("payload", {})
                ],
                "canary_returned": attached_canary in answer,
            }

        before = len(runs_for(s, conversation_id)) if conversation_id else 0
        s.send(read_prompt(ungranted / "canary.txt"))
        if conversation_id is None:
            conversation_id = wait_for_conversation_id(s)
        run_id = wait_for_new_run(s, conversation_id, before)
        observed: list[str] = []
        card_seen = False
        deadline = time.time() + 150
        while time.time() < deadline:
            state = str(run_status(s, run_id).get("status"))
            if not observed or observed[-1] != state:
                observed.append(state)
            if s.present(CARD_SELECTOR):
                card_seen = True
            if state in TERMINAL:
                break
            time.sleep(1)
        s.shot(f"{lane_name}-ungranted-read")
        stream = events(s, run_id)
        answer = assistant_text(s, run_id) or ""
        evidence["ungranted"] = {
            "status_trace": observed,
            "card_rendered": card_seen,
            "tools": tool_calls(stream),
            "approvals": approval_events(stream),
            "canary_returned": ungranted_canary in answer,
        }
    dump(s.run_dir, f"wb-{lane_name}-attached.json", evidence)

    # Not a pass and not a product failure: the half that needed a grant never
    # ran, so no caller can read silence as success.
    require(
        not evidence.get("attach_blocked"),
        "the native folder picker could not be driven: "
        + str(evidence.get("attach_blocked")),
    )
    attached_ev = evidence.get("attached", {})
    ungranted_ev = evidence.get("ungranted", {})
    failures: list[str] = []
    if attached_ev.get("approvals"):
        failures.append(
            "reading INSIDE the attached folder still raised an approval — "
            "attaching bought the user nothing in this lane"
        )
    if not attached_ev.get("canary_returned"):
        failures.append("the attached file did not come back readable")
    if ungranted_ev.get("canary_returned"):
        failures.append("an UNGRANTED file was read without any consent")
    if not ungranted_ev.get("approvals"):
        failures.append("the ungranted read raised no approval at all")
    _fail(failures)


def wb10_attached_folder_is_silent_default_lane(s: DriverSession) -> None:
    """The lane the desktop actually runs. macOS-only (native picker)."""

    require(sys.platform == "darwin", "the native folder picker driver is macOS-only")
    _attached_folder_is_silent(s, "default")


def wb11_attached_folder_is_silent_enforce_lane(s: DriverSession) -> None:
    """The operator opt-in. THE regression: attaching must buy something."""

    require(sys.platform == "darwin", "the native folder picker driver is macOS-only")
    _attached_folder_is_silent(s, "enforce")


def main() -> int:
    plan = JourneyPlan("workspace-bypass")
    plan.boot(
        "source · fresh · DEFAULT lane",
        lambda: DriverSession(name="workspace-bypass-default"),
        setup=sign_in_and_key,
        env=DEFAULT_LANE,
        phases=[
            (
                "WB-1",
                "grants attached while the folder bar still exists",
                wb1_grants_attached_while_the_bar_exists,
            ),
            (
                "WB-2",
                "Manual asks, even inside a folder you attached",
                wb2_manual_write_into_a_granted_folder_asks,
            ),
            (
                "WB-3",
                "the pill's options agree with the master switch",
                wb3_the_pill_agrees_with_the_master_switch,
            ),
            (
                "WB-4",
                "the Settings toggle persists and reaches the pill",
                wb4_the_settings_toggle_persists_and_reaches_the_pill,
            ),
            (
                "WB-5",
                "the menu offers Bypass and selecting it takes [needs WB-4]",
                wb5_selecting_bypass_changes_the_pill,
            ),
            (
                "WB-6",
                "Bypass writes inside a grant without pausing [needs WB-5]",
                wb6_bypass_writes_inside_a_grant_without_pausing,
            ),
            (
                "WB-7",
                "Bypass is bounded by the grant [needs WB-5]",
                wb7_bypass_is_bounded_by_the_grant,
            ),
            (
                "WB-8",
                "a staged workspace write lands bytes on disk",
                wb8_staged_workspace_writes_land_bytes_on_disk,
            ),
            (
                "WB-9",
                "a bypass selection survives the wire (backend alone)",
                wb9_the_bypass_selection_survives_the_wire,
            ),
            (
                "WB-10",
                "an attached folder is silent — default lane",
                wb10_attached_folder_is_silent_default_lane,
            ),
        ],
    )
    plan.boot(
        "source · fresh · ENFORCE lane",
        lambda: DriverSession(name="workspace-bypass-enforce"),
        setup=sign_in_and_key,
        env=ENFORCE_LANE,
        phases=[
            (
                "WB-11",
                "an attached folder is silent — enforce lane",
                wb11_attached_folder_is_silent_enforce_lane,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
