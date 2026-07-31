#!/usr/bin/env python3
"""FS-D — the filesystem-bypass pill, from the Settings master switch outwards.

Three tiers, and the journey walks them in the order a user meets them:

  1. MASTER OFF (a fresh install). The pill must render a DISABLED "Manual" and
     must not offer Bypass ANYWHERE — not greyed, not in the accessibility tree,
     not in the DOM. An option that is offered and then ignored is worse than an
     absent one, because "the user said bypass" is exactly the sort of claim
     something downstream later reads as authorization.
  2. MASTER ON, set through the real Settings surface (not a PUT this script
     invents), then re-read from the server so the assertion is about what was
     PERSISTED. The pill becomes a real menu: Manual · Bypass · a scope choice.
  3. THE BOUND. With Bypass selected, a write to a path nobody granted must
     STILL ask, and the file must still not exist on disk afterwards. This is
     the half that matters: bypass suspends the pause inside a folder the user
     attached with write permission, and changes nothing anywhere else.

What this journey CANNOT do here, stated rather than approximated: the positive
half of tier 3 — a write INSIDE a granted writable folder proceeding without a
pause — needs a folder grant, and the only way to mint one is Electron's native
`dialog.showOpenDialog`. Driving that dialog needs macOS Accessibility
permission for the controlling process; without it `System Events` refuses
(-25211) and no keystroke reaches the sheet. That is a system privacy setting,
so the journey reports the gap instead of stubbing the picker.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    TERMINAL,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    approval_events,
    assistant_text,
    dump,
    events,
    lane,
    result,
    run_status,
    runs_for,
    settle_run,
    tool_calls,
    transport_json,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "FS-D"

PILL = ".atlas-bypass-pill"
PILL_ITEM = ".atlas-bypass-pill__item"
SETTINGS_TRIGGER = '[aria-label="Settings"]'
MODEL_BEHAVIOR_NAV = '[data-slug="model-behavior"]'
BYPASS_TOGGLE = "[data-testid=filesystem-bypass-toggle]"
#: The design-system switch hides its <input> and paints the track on the
#: wrapping <label>, so the label is what a user's pointer actually hits.
BYPASS_TOGGLE_HIT = f"label.ui-switch:has({BYPASS_TOGGLE})"
RUN_RAIL = '[aria-label="Run"][data-destination]'
CARD_SELECTOR = "[data-testid^=tc-chat-approval-]"

PILL_STATE_JS = (
    "(() => { const el = document.querySelector('" + PILL + "'); if (!el) return null;"
    " return { mode: el.getAttribute('data-mode'),"
    " label: (el.innerText || '').trim(),"
    " disabled: el.hasAttribute('disabled'),"
    " haspopup: el.getAttribute('aria-haspopup'),"
    " tooltip: el.getAttribute('data-tooltip') }; })()"
)

#: "Bypass" must not exist anywhere a person or a screen reader could find it.
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


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    nonce = uuid.uuid4().hex[:12]
    evidence: dict[str, Any] = {}
    with lane(DEFAULT_LANE), tempfile.TemporaryDirectory(prefix="fsd-") as raw:
        ungranted = Path(raw).resolve()
        target = ungranted / f"bypass-{nonce}.txt"
        evidence["ungranted_write_target"] = str(target)

        session = DriverSession(name="fs-d-bypass")
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                session.sign_in_local()
                session.ftue_add_key(provider, key)

                # The pill is mounted by `RunComposer` only — neither the
                # first-run composer nor the cockpit's empty-state composer
                # carries it, so a session that never sends a message never
                # sees an execution-mode control at all. Recorded, then walked
                # past: the tiers below are about the composer that HAS one.
                evidence["pill_on_first_run_composer"] = session.evaluate(PILL_STATE_JS)
                session.shot("d-00-first-run-composer")
                session.send_first_run_message("Say READY and nothing else.")
                warmup_conversation = wait_for_conversation_id(session)
                warmup_run = wait_for_new_run(session, warmup_conversation, 0)
                settle_run(session, warmup_run, timeout_s=180)
                assert session.wait_for(PILL, timeout_s=90), (
                    "the execution-mode pill never rendered on the run composer"
                )
                session.shot("d-01-master-off")

                # --- tier 1: master OFF ------------------------------------
                evidence["master_off_pill"] = session.evaluate(PILL_STATE_JS)
                # A locked pill must not open anything. Playwright refuses to
                # click a disabled control, and that refusal IS the evidence —
                # so record it rather than letting it end the journey.
                try:
                    session.click(PILL)
                    evidence["master_off_click"] = "accepted"
                except Exception as exc:  # noqa: BLE001
                    evidence["master_off_click"] = f"refused: {type(exc).__name__}"
                time.sleep(0.4)
                evidence["master_off_menu_items"] = session.evaluate(MENU_ITEMS_JS)
                evidence["master_off_bypass_anywhere"] = session.evaluate(
                    BYPASS_ANYWHERE_JS
                )
                evidence["master_off_defaults"] = transport_json(
                    session, "GET", "/v1/agent/workspace/defaults"
                ).get("behavior_overrides", {})
                session.shot("d-02-master-off-clicked")

                # --- tier 1 → 2: turn it on in the real Settings surface ----
                session.click(SETTINGS_TRIGGER)
                assert session.wait_for("[data-testid=settings-surface]", 30), (
                    "Settings never opened"
                )
                session.click(MODEL_BEHAVIOR_NAV)
                assert session.wait_for(BYPASS_TOGGLE, 30), (
                    "Model & behavior has no filesystem-bypass toggle"
                )
                session.shot("d-03-settings-model-behavior")
                evidence["toggle_before"] = session.evaluate(
                    "(() => { const el = document.querySelector('"
                    + BYPASS_TOGGLE
                    + "'); return el ? el.checked : null; })()"
                )
                session.click(BYPASS_TOGGLE_HIT)
                time.sleep(3.5)
                evidence["toggle_after"] = session.evaluate(
                    "(() => { const el = document.querySelector('"
                    + BYPASS_TOGGLE
                    + "'); return el ? el.checked : null; })()"
                )
                session.shot("d-04-toggle-on")
                # What was PERSISTED, read back from the server.
                evidence["master_on_defaults"] = transport_json(
                    session, "GET", "/v1/agent/workspace/defaults"
                ).get("behavior_overrides", {})

                # --- tier 2: the pill now offers Bypass --------------------
                # Three probes, escalating, because WHERE the switch stops
                # travelling is the diagnosis. `useDesktopComposerBypass` reads
                # the master once per mount, so: does returning to the chat
                # suffice, does a fresh chat, or does it take a reload?
                session.click(RUN_RAIL)
                time.sleep(3)
                if not session.wait_for(PILL, timeout_s=15):
                    # The rail lands on the cockpit, which may show its EMPTY
                    # composer rather than rebinding the warm-up chat — and that
                    # composer has no pill at all. Go back to the conversation
                    # by its own route so the probe is about the master switch,
                    # not about which composer happened to mount.
                    session.evaluate(
                        f"window.location.hash = '#/convo/{warmup_conversation}'"
                    )
                    time.sleep(4)
                    session.wait_for(PILL, timeout_s=30)
                evidence["master_on_pill"] = session.evaluate(PILL_STATE_JS)

                # An ABSENT pill is not an enabled one. `{}` has no `disabled`
                # key, so ask the question the other way round: a pill counts as
                # live only when it is present AND not disabled.
                def _live(probe: Any) -> bool:
                    return isinstance(probe, dict) and probe.get("disabled") is False

                live = evidence["master_on_pill"]
                evidence["master_reached_pill_via"] = (
                    "returning to the chat" if _live(live) else None
                )
                if not _live(live):
                    # A reload remounts every composer with the conversation
                    # still bound — the cheapest thing short of relaunching the
                    # app, and the honest test of "is this mount-scoped?".
                    session.evaluate("window.location.reload()")
                    time.sleep(12)
                    if not session.wait_for(PILL, timeout_s=60):
                        session.evaluate(
                            f"window.location.hash = '#/convo/{warmup_conversation}'"
                        )
                        time.sleep(4)
                        session.wait_for(PILL, timeout_s=60)
                    live = session.evaluate(PILL_STATE_JS)
                    evidence["pill_after_reload"] = live
                    session.shot("d-05b-after-reload")
                    if _live(live):
                        evidence["master_reached_pill_via"] = "a renderer reload"

                if not _live(live):
                    # Nothing reached it. Tier 3 still runs — the ungranted
                    # bound is worth measuring either way — but the verdict must
                    # name the tier that actually broke.
                    evidence["tier2_blocked"] = (
                        "the master switch never reached the composer pill "
                        "(neither on return to the chat nor after a renderer "
                        "reload)"
                    )
                else:
                    session.click(PILL)
                    if not session.wait_for(PILL_ITEM, timeout_s=10):
                        # One retry: the pill toggles, so a click that raced the
                        # post-reload settle can leave it shut.
                        session.click(PILL)
                        session.wait_for(PILL_ITEM, timeout_s=10)
                    evidence["master_on_menu_items"] = session.evaluate(MENU_ITEMS_JS)
                    session.shot("d-05-pill-menu-open")
                    # Choose Bypass by its visible label, as a user would.
                    session.click(f'{PILL_ITEM}:has-text("Bypass")')
                    time.sleep(0.8)
                    evidence["pill_after_select"] = session.evaluate(PILL_STATE_JS)
                    # Re-open to see the scope rows the selection reveals.
                    session.click(PILL)
                    session.wait_for(PILL_ITEM, timeout_s=10)
                    evidence["scope_rows"] = session.evaluate(MENU_ITEMS_JS)
                    session.shot("d-06-bypass-selected")
                session.press("body", "Escape")
                time.sleep(0.3)

                # --- tier 3: the bound — ungranted still asks ---------------
                # ⌘N / a reload may have moved the app off the warm-up chat, so
                # read the conversation the composer is actually bound to now.
                import re as _re

                match = _re.fullmatch(
                    r"#/convo/([^/?#]+)(?:[?#].*)?",
                    str(session.evaluate("window.location.hash") or ""),
                )
                conversation_id = match.group(1) if match else None
                before = (
                    len(runs_for(session, conversation_id))
                    if conversation_id is not None
                    else 0
                )
                session.send_first_run_message(
                    f"Create the file {target} containing exactly the text "
                    f"bypass-{nonce}. Write that exact path."
                )
                if conversation_id is None:
                    conversation_id = wait_for_conversation_id(session)
                run_id = wait_for_new_run(session, conversation_id, before)
                evidence["conversation_id"] = conversation_id
                evidence["bypass_run_id"] = run_id

                observed: list[str] = []
                card_seen = False
                deadline = time.time() + 180
                while time.time() < deadline:
                    state = str(run_status(session, run_id).get("status"))
                    if not observed or observed[-1] != state:
                        observed.append(state)
                    if session.present(CARD_SELECTOR):
                        card_seen = True
                    if state in TERMINAL:
                        break
                    time.sleep(1)
                session.shot("d-07-ungranted-write-under-bypass")
                stream = events(session, run_id)
                evidence["ungranted_write"] = {
                    "status_trace": observed,
                    "final_status": observed[-1] if observed else None,
                    "card_rendered": card_seen,
                    "tools": tool_calls(stream),
                    "approvals": approval_events(stream),
                    "answer": assistant_text(session, run_id)[-800:],
                }
                evidence["file_created_on_disk"] = target.exists()
                evidence["fixture_dir_contents"] = sorted(
                    p.name for p in ungranted.iterdir()
                )
                # Did the run-create body actually carry the selection?
                try:
                    evidence["run_record"] = transport_json(
                        session, "GET", f"/v1/agent/runs/{run_id}"
                    )
                except Exception as exc:  # noqa: BLE001
                    evidence["run_record_error"] = repr(exc)[:200]
        finally:
            out = dump(session.run_dir, "fs-d-evidence.json", evidence)
            print(f"[fs-d] evidence -> {out}", flush=True)

    failures: list[str] = []
    off_pill = evidence.get("master_off_pill") or {}
    if not off_pill:
        failures.append("no execution-mode pill rendered at all")
    else:
        if off_pill.get("mode") != "manual":
            failures.append(f"master-off pill reports mode {off_pill.get('mode')!r}")
        if not off_pill.get("disabled"):
            failures.append("master-off pill is not disabled")
    if evidence.get("master_off_bypass_anywhere"):
        failures.append(
            "Bypass is reachable with the master switch OFF: "
            + json.dumps(evidence["master_off_bypass_anywhere"])[:200]
        )
    if (
        evidence.get("master_on_defaults", {}).get("filesystem_bypass_enabled")
        is not True
    ):
        failures.append("the Settings toggle did not persist filesystem_bypass_enabled")
    if evidence.get("tier2_blocked"):
        failures.append(str(evidence["tier2_blocked"]))
    else:
        on_items = evidence.get("master_on_menu_items") or []
        if not any("Bypass" in str(item.get("text", "")) for item in on_items):
            failures.append("with the master ON the pill menu still offers no Bypass")
        if (evidence.get("pill_after_select") or {}).get("mode") != "bypass":
            failures.append("selecting Bypass did not change the pill's mode")
    write = evidence.get("ungranted_write") or {}
    if not write.get("approvals"):
        failures.append("an UNGRANTED write under Bypass raised no approval")
    if evidence.get("file_created_on_disk"):
        failures.append("an UNGRANTED write under Bypass actually created the file")

    if failures:
        result(JOURNEY, "FAILED", reasons=failures)
        return 1
    result(
        JOURNEY,
        "passed",
        master_off_offers_bypass=False,
        persisted=True,
        selected_mode=(evidence.get("pill_after_select") or {}).get("mode"),
        ungranted_still_asks=True,
        granted_writable_case="BLOCKED — needs a folder grant; see the header",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
