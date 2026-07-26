#!/usr/bin/env python3
"""Live reproduction: a research turn that overruns the per-run tool budget.

The reported failure: any run making more than the per-run tool allowance
died with "RUN INTERRUPTED / This run needs attention / The tool reported an
error and didn't return a result", losing every result it had already
gathered. The cause was a hard-cap rejection raised as a run-fatal exception
that escaped through ``astream_runtime``, even though its own message told
the model to "finalize".

This drives the real supervised app with the two prompts from the report
(plus one that deliberately demands many distinct searches), and requires:

  * the run reaches a real assistant answer,
  * no errored tool card and no run-interrupted banner,
  * if the budget did fire, it fired as a refusal the model recovered from.

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

# The two prompts from the bug report, plus a deliberately search-hungry one.
PROMPTS = [
    (
        "report-1",
        "Catch me up on this week's AI agent releases, then draft a short "
        "update I can send the team.",
    ),
    (
        "report-2",
        "Check what shipped in Ethereum's latest upgrade, then draft a short "
        "community update I can post.",
    ),
    (
        "search-hungry",
        "Research each of these separately with its own web search, then give "
        "me one combined summary: (1) LangGraph's latest release, (2) OpenAI's "
        "most recent model announcement, (3) Anthropic's most recent model "
        "announcement, (4) the newest Python 3.14 feature, (5) the latest "
        "Postgres major version, (6) the latest Node.js LTS version, (7) the "
        "newest Rust release, (8) the latest Kubernetes release. Use a "
        "separate search for each one.",
    ),
]

JS_ASSISTANT_COUNT = (
    'document.querySelectorAll("[data-testid^=tc-chat-message-]'
    '[data-role=assistant]").length'
)

# The failure banner a user saw instead of an answer.
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

JS_LAST_ASSISTANT = """(()=>{
  const nodes=[...document.querySelectorAll(
    '[data-testid^=tc-chat-message-][data-role=assistant]')];
  const last=nodes[nodes.length-1];
  return last?(last.innerText||'').slice(0,600):'';
})()"""


def log(line: str) -> None:
    print(line, flush=True)


def tool_cards(s: DriverSession) -> list[dict]:
    raw = s.evaluate(JS_TOOL_CARDS)
    return json.loads(raw) if raw else []


def wait_for_answer(s: DriverSession, before: int, timeout_s: int = 300) -> None:
    """Wait for a new assistant turn, failing fast on the interrupt banner."""

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        banner = s.evaluate(JS_INTERRUPT_TEXT) or ""
        if banner:
            raise AssertionError(
                f"run was interrupted instead of finalizing:\n{banner}"
            )
        if int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > before:
            return
        time.sleep(0.5)
    raise AssertionError(f"no assistant answer within {timeout_s}s")


SEND_BUTTON = 'button[aria-label="Send message"]'


def send(s: DriverSession, text: str, first: bool) -> None:
    if first:
        s.send_first_run_message(text)
        assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
        return
    assert s.wait_for("[data-testid=composer-textarea]", 30), "composer never appeared"
    s.fill("[data-testid=composer-textarea]", text)
    # The composer disables Send while the prior run is still settling; clicking
    # a disabled control is a harness race, not a product failure.
    assert s.wait_for(f"{SEND_BUTTON}:not([disabled])", 60), (
        "send button never became enabled"
    )
    s.click(SEND_BUTTON)


def run_prompt(s: DriverSession, label: str, prompt: str, first: bool) -> None:
    log(f"── {label} ─────────────────────────────────────────")
    before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    seen_before = {card["testId"] for card in tool_cards(s)}
    send(s, prompt, first)
    wait_for_answer(s, before)

    # Let any trailing card settle before reading terminal state.
    time.sleep(3)
    cards = tool_cards(s)
    new_cards = [c for c in cards if c["testId"] not in seen_before]
    errored = [c for c in new_cards if c["status"] == "error"]
    searches = [c for c in new_cards if "web_search" in c["text"]]

    answer = s.evaluate(JS_LAST_ASSISTANT) or ""
    s.shot(f"budget-{label}")

    log(f"      tool cards: {len(new_cards)} (web_search: {len(searches)})")
    assert not errored, f"errored tool cards after budget overrun: {errored!r}"
    assert answer.strip(), "assistant turn appeared but carried no answer text"
    banner = s.evaluate(JS_INTERRUPT_TEXT) or ""
    assert not banner, f"interrupt banner present after the answer:\n{banner}"
    log(f"PASS  {label}: finalized with an answer; no errored cards, no interrupt")


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] in {"-h", "--help"}:
            print(
                "Usage: python3 tools/desktop-journeys/chat-rich-cards/"
                "budget_overrun.py\n"
                "Drives multi-search research turns and requires each to "
                "finalize rather than die on the per-run tool budget."
            )
            return 0
        raise SystemExit(f"unsupported argument: {sys.argv[1]!r}; use --help")

    key = load_env_key(PROVIDER)
    with DriverSession(name="budget-overrun") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")
        for index, (label, prompt) in enumerate(PROMPTS):
            run_prompt(s, label, prompt, first=index == 0)
    log("PASS  every research turn finalized instead of dying on the tool budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
