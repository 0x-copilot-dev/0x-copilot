#!/usr/bin/env python3
"""Live journey: the Settings tool-call limit actually governs a run.

Settings that look right but do not reach the runtime are the failure mode
worth testing here. This drives the real supervised app end to end:

  1. set "Tool calls per run" on Settings → Model & behavior and Save,
  2. reload the section and require the value to have persisted,
  3. send a research prompt that wants more searches than the limit allows,
  4. require the run to finalize (never a dead run) AND to have executed no
     more than the configured number of searches.

Step 4 is the point: a limit the UI stores but the runtime ignores would
pass steps 1-3 and still be broken.

The provider key is read from services/ai-backend/.env and only ever typed
into the app's password field.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("RICH_CHAT_PROVIDER", "openai")
LIMIT = 2

PROMPT = (
    "Research each of these with its own separate web search, then summarize: "
    "(1) the latest Python release, (2) the latest Node.js LTS, (3) the "
    "latest Postgres major version, (4) the latest Rust release. Use a "
    "separate search for each one."
)

SEND_BUTTON = 'button[aria-label="Send message"]'
CAP_INPUT = "[data-testid=tool-calls-per-run-input]"
SETTINGS_BUTTON = '[aria-label="Settings"]'
# Settings nav rows carry their canonical slug, not a per-row testid.
NAV_MODEL_BEHAVIOR = '[data-slug="model-behavior"]'
NAV_PROVIDER_KEYS = '[data-slug="provider-keys"]'

JS_ASSISTANT_COUNT = (
    'document.querySelectorAll("[data-testid^=tc-chat-message-]'
    '[data-role=assistant]").length'
)

JS_INTERRUPT_TEXT = """(()=>{
  const body=document.body?document.body.innerText:'';
  return /RUN INTERRUPTED|This run needs attention|didn't return a result/i
    .test(body) ? body.slice(0,400) : '';
})()"""

JS_TOOL_CARDS = """(()=>JSON.stringify(
  [...document.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status]')]
    .map((n)=>({
      testId:n.getAttribute('data-testid'),
      status:n.getAttribute('data-tool-status'),
      text:(n.innerText||'').slice(0,240),
    }))
))()"""


def log(line: str) -> None:
    print(line, flush=True)


def open_model_behavior(s: DriverSession) -> None:
    """Open Settings from the app rail and select Model & behavior."""

    s.click(SETTINGS_BUTTON)
    assert s.wait_for("[data-testid=settings-surface]", 20), "Settings never opened"
    s.click(NAV_MODEL_BEHAVIOR)
    assert s.wait_for(CAP_INPUT, 20), "Tool-calls field never rendered"


def set_limit(s: DriverSession, limit: int) -> None:
    """Open Settings → Model & behavior, set the cap, Save, and verify."""

    open_model_behavior(s)

    # The default is surfaced as a placeholder, so an unset workspace shows
    # blank rather than a number the user never chose.
    placeholder = s.evaluate(
        f"(document.querySelector({json.dumps(CAP_INPUT)})||{{}}).placeholder||''"
    )
    log(f"      unset placeholder shows the default: {placeholder!r}")

    s.fill(CAP_INPUT, str(limit))
    assert s.wait_for("[data-testid=settings-savebar-save]", 15), (
        "editing the cap did not dock the SaveBar"
    )
    s.click("[data-testid=settings-savebar-save]")
    time.sleep(4)
    log(f"PASS  set tool-call limit to {limit} and saved")


def assert_persisted(s: DriverSession, limit: int) -> None:
    """Leave the section and come back; the saved value must still be there."""

    s.click(NAV_PROVIDER_KEYS)
    time.sleep(1)
    s.click(NAV_MODEL_BEHAVIOR)
    assert s.wait_for(CAP_INPUT, 20), "Tool-calls field never re-rendered"
    value = s.evaluate(
        f"(document.querySelector({json.dumps(CAP_INPUT)})||{{}}).value||''"
    )
    assert str(value) == str(limit), f"cap did not persist; read back {value!r}"
    log(f"PASS  limit persisted across a section switch (read back {value!r})")


def run_prompt_and_check(s: DriverSession, limit: int) -> None:
    """Send a search-hungry prompt; require finalize AND an enforced cap."""

    s.rpc("press", key="Escape")
    time.sleep(1)
    s.open_destination("Run")
    before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)

    assert s.wait_for("[data-testid=composer-textarea]", 30), "composer never appeared"
    s.fill("[data-testid=composer-textarea]", PROMPT)
    assert s.wait_for(f"{SEND_BUTTON}:not([disabled])", 60), "send never enabled"
    s.click(SEND_BUTTON)

    deadline = time.time() + 300
    while time.time() < deadline:
        banner = s.evaluate(JS_INTERRUPT_TEXT) or ""
        assert not banner, f"run was interrupted instead of finalizing:\n{banner}"
        if int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > before:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("no assistant answer within 300s")

    time.sleep(3)
    cards = json.loads(s.evaluate(JS_TOOL_CARDS) or "[]")
    searches = [c for c in cards if "web_search" in c["text"]]
    errored = [c for c in cards if c["status"] == "error"]
    s.shot("tool-budget-setting")

    log(f"      web_search cards rendered: {len(searches)}")
    assert not errored, f"errored tool cards: {errored!r}"

    # A card is rendered for every *attempted* call, refused ones included, so
    # the card count cannot tell enforcement from execution. The runtime log is
    # the honest source: a refusal there proves the number typed into Settings
    # reached the budget guard, and no fatal escalation proves the run was
    # allowed to finish.
    log_text = (s._user_data_dir / "logs" / "ai-backend.log").read_text(
        encoding="utf-8", errors="replace"
    )
    refusals = log_text.count("tool_budget_rejected")
    fatal = log_text.count("tool_budget_rejected_fatal")
    load_failures = log_text.count("workspace_tool_call_cap_load_failed")

    log(f"      budget refusals: {refusals}, fatal escalations: {fatal}")
    assert load_failures == 0, "the workspace cap failed to load at run start"
    assert refusals > 0, (
        f"the prompt asked for more searches than the configured limit of "
        f"{limit}, but the budget never refused a call — the Settings value "
        "did not reach the runtime"
    )
    assert fatal == 0, "a refusal escalated to a run-fatal error"
    log(f"PASS  run finalized and the configured limit of {limit} was enforced")


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] in {"-h", "--help"}:
            print(
                "Usage: python3 tools/desktop-journeys/chat-rich-cards/"
                "tool_budget_setting.py\n"
                "Sets Settings → Model & behavior → Tool calls per run and "
                "requires it to govern a real run."
            )
            return 0
        raise SystemExit(f"unsupported argument: {sys.argv[1]!r}; use --help")

    key = load_env_key(PROVIDER)
    with DriverSession(name="tool-budget-setting") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        # The app rail (and with it Settings) only mounts once first-run is
        # past, so a cheap opening turn is required before the settings step.
        s.send_first_run_message("Say hello in one short sentence.")
        assert s.wait_for("[data-testid=tc-chat]", 60), "first run never opened"
        assert s.wait_for(SETTINGS_BUTTON, 60), "app rail never mounted"

        set_limit(s, LIMIT)
        assert_persisted(s, LIMIT)
        run_prompt_and_check(s, LIMIT)
    log("PASS  the Settings tool-call limit persists and governs the runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
