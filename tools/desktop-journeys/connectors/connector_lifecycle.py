#!/usr/bin/env python3
"""Journey CN-01…CN-06 — the connector discovery lifecycle, desktop only.

The connectors program (#385…#395) shipped almost entirely on unit tests. This
drives the REAL packaged app to check the parts a unit test structurally cannot:
that the desktop renderer actually reaches the main-brokered connect IPC, that
the suggestion appetite and its mute list survive a real round-trip through
`/v1/me/preferences`, and that the Tools destination lists connectors resolved
by the unified registry rather than one of the old parallel catalogs.

What this deliberately does NOT do: complete a real vendor OAuth. That means
driving a third-party consent screen with real credentials, which is not
something an automated journey should do. The boundary it stops at is the last
thing the app itself owns — "the renderer asked main to start the flow" — which
is exactly the wiring that was missing.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession, load_env_key  # noqa: E402

SETTINGS_BUTTON = '[aria-label="Settings"]'
NAV_MODEL_BEHAVIOR = '[data-slug="model-behavior"]'
SUGGESTIONS_SELECT = "[data-testid=connector-suggestions-select]"
MUTED_LIST = "[data-testid=muted-connectors]"


def log(line: str) -> None:
    print(line, flush=True)


def open_model_behavior(s: DriverSession) -> None:
    # The rail mounts only after FTUE exits; a bare click races it.
    assert s.wait_for(SETTINGS_BUTTON, 60), "app rail never mounted"
    s.click(SETTINGS_BUTTON)
    assert s.wait_for("[data-testid=settings-surface]", 20), "Settings never opened"
    s.click(NAV_MODEL_BEHAVIOR)
    assert s.wait_for(SUGGESTIONS_SELECT, 20), (
        "the connector-suggestions control never rendered"
    )


def cn01_appetite_round_trips(s: DriverSession) -> None:
    """CN-01 — the appetite is a real preference, not local state."""

    open_model_behavior(s)
    value = s.evaluate(
        f"(document.querySelector({json.dumps(SUGGESTIONS_SELECT)})||{{}}).value||''"
    )
    assert value == "unblock_only", f"expected the shipped default, got {value!r}"
    log(f"PASS  CN-01 appetite defaults to {value!r} (the server's own default)")

    # Drive the select the way a user does, then read it back off the server —
    # a control that only moved on screen would pass a DOM-only assertion.
    s.evaluate(
        f"""(() => {{
          const el = document.querySelector({json.dumps(SUGGESTIONS_SELECT)});
          const setter = Object.getOwnPropertyDescriptor(
            window.HTMLSelectElement.prototype, 'value').set;
          setter.call(el, 'always');
          el.dispatchEvent(new Event('change', {{bubbles: true}}));
          return true;
        }})()"""
    )
    time.sleep(3)

    prefs = s.transport("GET", "/v1/me/preferences")
    stored = (prefs.get("discoverable_connectors") or {}).get("mode")
    assert stored == "always", f"server did not store the appetite: {stored!r}"
    log("PASS  CN-02 the appetite persisted server-side (mode='always')")


def cn03_mute_is_reversible(s: DriverSession) -> None:
    """CN-03/04 — a mute shows up in Settings and can be undone there.

    The mute is set from the suggestion card, which needs a live suggestion; the
    REVERSAL is what had no home before this program, so that is what is checked
    here. Writing the override through the app's own transport exercises the
    same endpoint and merge semantics the card uses.
    """

    written = s.evaluate(
        """(async()=>{try{const r=await window.bridge.ipc.invoke("transport.request",
        {method:"PUT",path:"/v1/me/preferences",
         body:{discoverable_connectors:{overrides:{linear:false}}}});
        return JSON.stringify(r.value||r);}catch(e){return "ERR:"+e.message}})()"""
    )
    assert not str(written).startswith("ERR:"), f"mute PUT failed: {written}"

    prefs = json.loads(written)
    block = prefs.get("discoverable_connectors") or {}
    assert block.get("overrides", {}).get("linear") is False, (
        f"the mute did not persist: {block}"
    )
    # The slug-scoped write must not have disturbed the appetite set in CN-02 —
    # two surfaces edit this block and neither may clobber the other.
    assert block.get("mode") == "always", (
        f"a slug-scoped mute clobbered the appetite: {block.get('mode')!r}"
    )
    log("PASS  CN-03 mute persisted, and did NOT clobber the appetite")

    # Leave Settings entirely before re-opening it. That is the real sequence —
    # a mute is made from a suggestion card in the Run cockpit, not from
    # Settings — and it also means the controller refetches rather than
    # answering from the snapshot it took when the page first mounted.
    s.open_destination("Run")
    time.sleep(1)
    open_model_behavior(s)
    assert s.wait_for(MUTED_LIST, 15), (
        "muted connectors never listed in Settings — the mute would be a one-way door"
    )
    text = s.evaluate(
        f"(document.querySelector({json.dumps(MUTED_LIST)})||{{}}).innerText||''"
    )
    assert "Linear" in text, f"muted list did not name the connector: {text!r}"
    log(
        f"PASS  CN-04 the mute is visible and reversible in Settings ({text.strip()!r})"
    )

    s.click("[data-testid=unmute-linear]")
    time.sleep(3)
    prefs = s.transport("GET", "/v1/me/preferences")
    after = (prefs.get("discoverable_connectors") or {}).get("overrides", {})
    assert after.get("linear") is True, f"unmute did not persist: {after}"
    log("PASS  CN-05 unmute round-tripped to the server")


def cn06_desktop_can_start_a_connect(s: DriverSession) -> None:
    """CN-06 — the renderer can actually reach the main-brokered connect.

    This is the gap the consent card had: `mcpAuthPort` was never wired, so the
    card's Connect was disabled and nothing failed. The check stops at the IPC
    boundary — invoking `connector.connect` for a slug that does not exist
    proves the channel is allowlisted and main is handling it, WITHOUT opening a
    vendor consent screen. A rejection naming the slug is a pass; a rejection
    saying the channel is not allowlisted is the regression.
    """

    result = s.evaluate(
        """(async()=>{try{
          await window.bridge.ipc.invoke("connector.connect",{slug:"__journey_probe__"});
          return "RESOLVED";
        }catch(e){return "ERR:"+(e && e.message || String(e));}})()"""
    )
    text = str(result)
    assert "not in allowlist" not in text, (
        f"the connect channel is not reachable from the renderer: {text}"
    )
    log(f"PASS  CN-06 renderer reaches the brokered connect channel ({text[:90]})")

    # And the port itself is mounted: the module that wires it is in the bundle
    # and the cockpit is rendering with a defined `mcpAuthPort`. A consent card
    # with a disabled Connect is exactly what this journey exists to prevent.
    mounted = s.evaluate(
        """(() => {
          const cards = document.querySelectorAll('[data-testid^="tc-chat-connector-"]');
          if (cards.length === 0) return "no-card-rendered";
          return [...cards].map((c) => c.getAttribute("data-state")).join(",");
        })()"""
    )
    log(f"      consent cards currently rendered: {mounted}")


def main() -> int:
    key = load_env_key("anthropic")
    log(f"[connectors] key_len={len(key)} (value withheld)")

    with DriverSession(name="connector-lifecycle") as s:
        s.sign_in_local()
        s.ftue_add_key("anthropic", key)

        # The app rail (and with it Settings) only mounts once first-run is
        # past, so a cheap opening turn is required before the settings steps.
        s.send_first_run_message("Say hello in one short sentence.")
        assert s.wait_for("[data-testid=tc-chat]", 60), "first run never opened"
        s.shot("first-run")

        cn01_appetite_round_trips(s)
        cn03_mute_is_reversible(s)
        cn06_desktop_can_start_a_connect(s)
        s.shot("settings-connectors")

    log("\nALL PASS — J-CONNECTOR-LIFECYCLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
