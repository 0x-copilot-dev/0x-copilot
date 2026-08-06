#!/usr/bin/env python3
"""transcript-rendering — everything the run cockpit draws while a turn happens.

Thirteen original journeys asked the same machine for the same thing: sign in,
add a key, send a prompt, look at what rendered. They now share one boot.

**The phases are ordered and several are dependent.** TR-3 asserts that TR-1's
tool card and TR-2's fleet card SURVIVE a new run binding — that claim does not
exist without its predecessors, so each dependent phase declares what it
consumes and skips (never passes) when the state is absent. Everything after
TR-5 is independent: each sends its own prompt and reads its own result.

Model-driven shape is reported as BLOCKED, not FAILED. If the model declines to
call web search, or answers without citing, or skips reasoning on an easy input,
the shape under test never occurred — that is a statement about the run, not
about the renderer, and calling it a failure trains people to ignore the suite.

    python3 tools/desktop-journeys/transcript_rendering.py

Folds in: chat-rich-cards/rich_chat, focus-mode/focus_activity,
focus-inline-artifacts/focus_inline, turn-interleaving/{interleaved_turn,
multi_batch_turn, thinking_visible, shimmer_visible, thinking_expandable},
transcript-density/long_run_grouping, run-timeline-persistence/{timeline_persists,
citation_chips}.

Two originals are deliberately NOT folded in, because neither asserts anything:
`run-timeline-persistence/catch_gap` documents its own expected result on a
fixed build as "no gap observed, nothing to photograph" (it exists to photograph
a PRE-fix defect, and TR-17's 50ms sampler is the real proof), and
`sources_probe` says of itself "prints findings; does not assert. Delete once
covered by a real journey." Its three probes survive as diagnostic output in
TR-18.

The provider key is read from services/ai-backend/.env and only ever reaches the
password field — never printed, logged, or written to an artifact.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

from _lib import (
    DriverSession,
    JourneyPlan,
    blocked_unless,
    byok_provider,
    require,
    wait_for_conversation_id,
    wait_for_new_run,
    wait_for_terminal_run,
)

PROVIDER = os.environ.get("JOURNEY_PROVIDER", "anthropic")

#: Cross-phase handoff. The rich-card matrix is a sequence of claims about the
#: SAME conversation — "the card TR-1 made is still there after TR-3" cannot be
#: expressed any other way. A phase that needs a predecessor's product reads it
#: from here and skips if it is absent.
STATE: dict[str, object] = {}


def log(line: str) -> None:
    print(f"  {line}", flush=True)


def send_in_run(s: DriverSession, text: str) -> None:
    """Send a follow-up through the run cockpit composer.

    Same testIds as the FTUE composer (`composer-textarea` + the Send button).
    """

    assert s.wait_for("[data-testid=composer-textarea]"), "run composer never appeared"
    s.fill("[data-testid=composer-textarea]", text)
    time.sleep(0.3)
    s.click('button[aria-label="Send message"]')


P_WEB = (
    "Use the web_search tool exactly once to find the official Python "
    "documentation page for math.isqrt. Do not delegate this work. Return the "
    "official URL and a one-sentence summary."
)


P_SINGLE = (
    "Use exactly ONE subagent to check whether 97 is prime. Do not do the "
    "calculation yourself. State the subagent's conclusion in the final answer."
)


P_MULTI = (
    "Use exactly TWO subagents in parallel. One must test whether 97 is prime "
    "by trial division. The other must use the web_search tool exactly once "
    "to find the official Python documentation page for math.isqrt, then state "
    "the URL. Do not create more or fewer subagents, and do not use tools "
    "yourself. Give both findings."
)


P_MIXED = (
    "In this one response, use the web_search tool yourself exactly once to "
    "find the official Python documentation page for math.isqrt, and dispatch "
    "exactly TWO subagents in parallel: one checks whether 97 is prime by "
    "trial division and one states the definition of a prime number. Do not "
    "delegate the web search. Return the official URL and both findings."
)


JS_ASSISTANT_COUNT = 'document.querySelectorAll("[data-testid^=tc-chat-message-][data-role=assistant]").length'


def css_test_id(test_id: str) -> str:
    return f"[data-testid={json.dumps(test_id)}]"


def wait_new_assistant(s: DriverSession, before: int, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > before:
            return
        time.sleep(0.25)
    raise AssertionError("no assistant turn appeared after sending the prompt")


def card_snapshots(s: DriverSession, kind: str) -> list[dict]:
    """Read the activity card hosts a user sees, preserving their stable ids."""
    if kind == "tool":
        selector = '[data-testid^="tc-chat-tool-"][data-tool-status]'
    elif kind == "fleet":
        selector = '[data-testid^="tc-chat-fleet-"]'
    else:
        raise ValueError(f"unsupported card kind {kind!r}")
    js = f"""(()=>{{
      const selector={json.dumps(selector)};
      return JSON.stringify([...document.querySelectorAll(selector)].map((node)=>{{
        const fleet=node.querySelector('[data-fleet-id]');
        return {{
          testId:node.getAttribute('data-testid'),
          text:node.innerText,
          toolStatus:node.getAttribute('data-tool-status'),
          fleetStatus:fleet&&fleet.getAttribute('data-status'),
          childRows:node.querySelectorAll('.subagent-fleet-row').length,
          childStates:[...node.querySelectorAll('.subagent-fleet-row')].map((row)=>({{
            taskId:row.getAttribute('data-task-id'),
            status:row.getAttribute('data-status'),
            text:row.innerText,
          }})),
          hasDetails:!!node.querySelector('details > summary'),
        }};
      }}));
    }})()"""
    raw = s.evaluate(js)
    return json.loads(raw) if raw else []


def cards_not_seen(s: DriverSession, kind: str, previous_ids: set[str]) -> list[dict]:
    return [
        card for card in card_snapshots(s, kind) if card["testId"] not in previous_ids
    ]


def assert_completed_children(card: dict, expected_count: int) -> None:
    children = card["childStates"]
    assert len(children) == expected_count, card
    assert all(child["status"] == "completed" for child in children), (
        f"expected every delegated task to complete successfully; got {children!r}"
    )


def wait_new_card(
    s: DriverSession,
    kind: str,
    previous_ids: set[str],
    predicate: Callable[[dict], bool],
    timeout_s: int = 75,
) -> dict:
    deadline = time.time() + timeout_s
    last: list[dict] = []
    while time.time() < deadline:
        cards = card_snapshots(s, kind)
        # Each send starts a new run session, so its card list may replace the
        # prior run's list rather than append to it. Stable card ids—not list
        # offsets—are the only reliable way to identify a newly rendered card.
        last = [card for card in cards if card["testId"] not in previous_ids]
        for card in last:
            if predicate(card):
                return card
        time.sleep(0.25)
    raise AssertionError(
        f"no required new {kind} card appeared within {timeout_s}s; new cards={last!r}"
    )


def wait_terminal_card(
    s: DriverSession, kind: str, test_id: str, timeout_s: int = 90
) -> dict:
    deadline = time.time() + timeout_s
    last: dict | None = None
    while time.time() < deadline:
        last = next(
            (card for card in card_snapshots(s, kind) if card["testId"] == test_id),
            None,
        )
        if last is None:
            raise AssertionError(
                f"{kind} card {test_id!r} disappeared from the transcript"
            )
        status = last["toolStatus"] if kind == "tool" else last["fleetStatus"]
        if status == "error":
            raise AssertionError(
                f"{kind} card {test_id!r} reached error: {last['text']!r}"
            )
        if status in {"complete", "done"}:
            return last
        time.sleep(0.25)
    raise AssertionError(f"{kind} card {test_id!r} did not complete; last={last!r}")


def wait_cards_quiet(s: DriverSession, timeout_s: int = 45) -> None:
    """Wait until all activity cards are terminal and the card list is stable."""
    deadline = time.time() + timeout_s
    previous: tuple[str, str] | None = None
    stable = 0
    while time.time() < deadline:
        tools = card_snapshots(s, "tool")
        fleets = card_snapshots(s, "fleet")
        running = any(card["toolStatus"] == "running" for card in tools) or any(
            card["fleetStatus"] == "running" for card in fleets
        )
        snapshot = (
            json.dumps(tools, sort_keys=True),
            json.dumps(fleets, sort_keys=True),
        )
        if not running and snapshot == previous:
            stable += 1
            if stable >= 8:
                return
        else:
            stable = 0
        previous = snapshot
        time.sleep(0.5)
    raise AssertionError("activity cards did not settle before the timeout")


def ensure_native_disclosure(
    s: DriverSession,
    details_selector: str,
    body_selector: str,
    label: str,
    expected_text: str | None = None,
) -> None:
    """Exercise pointer, Space, and Enter on a native <details> control."""
    summary = f"{details_selector} > summary"

    def is_open() -> bool:
        return bool(
            s.evaluate(
                f"!!document.querySelector({json.dumps(details_selector)})?.open"
            )
        )

    assert s.present(summary), f"{label}: disclosure summary is missing"
    if is_open():
        s.click(summary)
    assert not is_open(), f"{label}: could not establish a closed disclosure"

    s.click(summary)
    assert is_open() and s.present(body_selector), (
        f"{label}: pointer click did not reveal details"
    )
    if expected_text is not None:
        body_text = s.evaluate(
            f"(document.querySelector({json.dumps(body_selector)})||{{}}).innerText||''"
        )
        assert expected_text.lower() in body_text.lower(), (
            f"{label}: expected live activity {expected_text!r}; got {body_text!r}"
        )
    s.press(summary, "Space")
    assert not is_open(), f"{label}: Space did not close details"
    s.press(summary, "Enter")
    assert is_open(), f"{label}: Enter did not reveal details"
    s.press(summary, "Space")
    assert not is_open(), f"{label}: final Space did not close details"
    log(f"PASS  {label}: pointer, Space, and Enter disclosure contract")


def ensure_fleet_row_interaction(
    s: DriverSession,
    fleet_id: str,
    task_id: str,
    expected_activity: str,
) -> None:
    """The fleet child is a role=button instead of native <details>."""
    host = css_test_id(fleet_id)
    fleet_card = f"{host} [data-fleet-id]"
    expanded = s.evaluate(
        f"(document.querySelector({json.dumps(fleet_card)})||{{}}).getAttribute?.('data-expanded')"
    )
    if expanded != "true":
        actual_fleet_id = s.evaluate(
            f"(document.querySelector({json.dumps(fleet_card)})||{{}}).getAttribute?.('data-fleet-id')"
        )
        assert isinstance(actual_fleet_id, str) and actual_fleet_id, (
            "parallel fleet card has no stable fleet id"
        )
        s.click(css_test_id(f"subagent-fleet-toggle-{actual_fleet_id}"))
        assert (
            s.evaluate(
                f"(document.querySelector({json.dumps(fleet_card)})||{{}}).getAttribute?.('data-expanded')"
            )
            == "true"
        ), "could not expand terminal fleet before inspecting its child"
    row = f"{host} .subagent-fleet-row[data-task-id={json.dumps(task_id)}]"
    timeline = f"{host} .subagent-fleet-row__inline-timeline"
    assert s.present(row), "parallel fleet has no interactive child row"
    if s.present(timeline):
        s.click(row)
    assert not s.present(timeline), "could not establish a closed fleet child"

    s.click(row)
    assert s.present(timeline), "fleet child pointer click did not reveal its activity"
    deadline = time.time() + 35
    timeline_text = ""
    while time.time() < deadline:
        timeline_text = s.evaluate(
            f"(document.querySelector({json.dumps(timeline)})||{{}}).innerText||''"
        )
        if expected_activity.lower() in timeline_text.lower():
            break
        time.sleep(0.25)
    assert expected_activity.lower() in timeline_text.lower(), (
        "fleet child did not render its real nested tool activity; "
        f"got {timeline_text!r}"
    )
    s.press(row, "Space")
    assert not s.present(timeline), "fleet child Space did not close its activity"
    s.press(row, "Enter")
    assert s.present(timeline), "fleet child Enter did not reveal its activity"
    s.press(row, "Space")
    assert not s.present(timeline), "fleet child final Space did not close its activity"
    log("PASS  fleet child: pointer, Space, and Enter expansion contract")


def ensure_fleet_card_interaction(s: DriverSession, fleet_host_test_id: str) -> None:
    """Terminal fleets must fold, then expose their details semantically."""
    host = css_test_id(fleet_host_test_id)
    fleet_id = s.evaluate(
        f"(document.querySelector({json.dumps(host)})?.querySelector('[data-fleet-id]')||{{}}).getAttribute?.('data-fleet-id')"
    )
    assert isinstance(fleet_id, str) and fleet_id, "fleet card has no fleet id"
    toggle = css_test_id(f"subagent-fleet-toggle-{fleet_id}")

    def expanded() -> str | None:
        return s.evaluate(
            f"(document.querySelector({json.dumps(toggle)})||{{}}).getAttribute?.('aria-expanded')"
        )

    assert s.present(toggle), "fleet card has no semantic disclosure control"
    assert expanded() == "false", "terminal fleet should start compact"
    s.click(toggle)
    assert expanded() == "true", "fleet-card pointer click did not expand details"
    s.press(toggle, "Space")
    assert expanded() == "false", "fleet-card Space did not collapse details"
    s.press(toggle, "Enter")
    assert expanded() == "true", "fleet-card Enter did not expand details"
    s.press(toggle, "Space")
    assert expanded() == "false", "fleet-card final Space did not restore compact state"
    assert s.present(host), "fleet card vanished after its disclosure interaction"
    log("PASS  fleet card: terminal compact state plus pointer, Space, and Enter")


def assert_no_ordinary_receipt_tab(s: DriverSession) -> None:
    """A terminal ledger receipt must not silently become a Studio tab."""
    labels = s.evaluate(
        "JSON.stringify([...document.querySelectorAll('[data-testid=tc-tabs] [role=tab]')].map((tab)=>tab.innerText.trim()))"
    )
    tabs = json.loads(labels) if labels else []
    assert all(label.casefold() != "run receipt" for label in tabs), (
        f"ordinary subagent work created a receipt tab: {tabs!r}"
    )


def activate_workspace_tab(s: DriverSession, label: str, content: str) -> None:
    """Select a rail tab after a live run update has settled.

    The right rail remounts its tab buttons while a newly-bound run projects
    its first events. The real control remains semantic; retry only the
    automation request itself so this journey still fails if the destination
    content never appears.
    """
    selector = f'[data-testid=run-workspace-rail] button[role=tab]:has-text("{label}")'
    # `click` uses Playwright's selector engine (which supports `:has-text`),
    # whereas `wait_for` intentionally runs native `document.querySelector`.
    # Probe a standard CSS selector before using the richer click selector.
    assert s.wait_for("[data-testid=run-workspace-rail] button[role=tab]"), (
        f"{label} tab did not render"
    )
    last_error: Exception | None = None
    for _ in range(3):
        try:
            s.click(selector)
            if s.wait_for(content, 10):
                return
        except Exception as exc:  # Driver reports a transient DOM race as HTTP 500.
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(f"{label} tab did not become interactive") from last_error


def ensure_agents_panel_interaction(
    s: DriverSession, task_id: str, expected_activity: str
) -> None:
    activate_workspace_tab(s, "Agents", "[data-testid=workspace-agents-tab]")
    details = css_test_id(f"agent-activity-row-details-{task_id}")
    assert s.present(details), "Agents panel is missing the selected fleet child"
    ensure_native_disclosure(
        s,
        details,
        f"{details} .agent-activity-row__details-body",
        "Agents-panel row",
        expected_activity,
    )


P_STREAM = "Write a detailed 220 word explanation of how a bicycle works, no tools."


P_TOOL = "Search the web for what deepagents are and summarize in 2 lines."


P_FLEET = "Use exactly ONE subagent to check whether 97 is prime."


FA_ASSISTANT_LEN = (
    "(()=>{const e=[...document.querySelectorAll("
    "'[data-testid^=tc-chat-message-][data-role=assistant]')];"
    "return e.length?e[e.length-1].innerText.length:0})()"
)


JS_ASSISTANT_COUNT = "document.querySelectorAll('[data-testid^=tc-chat-message-][data-role=assistant]').length"


def fa_q(sel: str) -> str:
    return json.dumps(sel)


def fa_wait_new_turn(s: DriverSession, prev_count: int, timeout_s: int = 40) -> bool:
    """Wait for a new assistant message to be appended (turn count grows)."""
    for _ in range(timeout_s * 4):
        if int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > prev_count:
            return True
        time.sleep(0.25)
    return False


def fa_poll_growth(
    s: DriverSession, seconds: float = 40.0, interval: float = 0.2
) -> list[int]:
    """Rapidly sample the last assistant message length; return the sequence of
    strictly-increasing lengths observed (one entry per growth step)."""
    steps: list[int] = []
    last = -1
    deadline = time.time() + seconds
    stable = 0
    while time.time() < deadline:
        n = int(s.evaluate(FA_ASSISTANT_LEN) or 0)
        if n > last:
            steps.append(n)
            last = n
            stable = 0
        else:
            stable += 1
            # stop once the answer has plateaued (streaming finished) and we have
            # enough evidence of incremental growth
            if len(steps) >= 3 and stable >= 8:
                break
        time.sleep(interval)
    return steps


def fa_tool_card_state(s: DriverSession) -> dict | None:
    js = (
        "(()=>{const c=document.querySelector('[data-testid^=tc-chat-tool-]:not([data-testid$=-args])"
        ":not([data-testid$=-result])');if(!c)return null;"
        "const sum=c.querySelector('summary');return JSON.stringify({"
        "status:c.getAttribute('data-tool-status'),text:c.innerText,"
        "hasDetails:!!(sum&&/Details/.test(sum.innerText))})})()"
    )
    raw = s.evaluate(js)
    return json.loads(raw) if raw else None


def fa_fleet_card_state(s: DriverSession) -> dict | None:
    js = (
        "(()=>{const c=document.querySelector('[data-testid^=tc-chat-fleet-]');"
        "if(!c)return null;return JSON.stringify({text:c.innerText})})()"
    )
    raw = s.evaluate(js)
    return json.loads(raw) if raw else None


def fa_streaming(s: DriverSession) -> None:
    log("── J1 streaming ─────────────────────────────────────────────")
    # first message of the run is the streaming probe
    s.send_first_run_message(P_STREAM)
    assert s.wait_for("[data-testid=tc-chat]", 60), "never landed on the run transcript"
    assert s.wait_for("[data-testid^=tc-chat-message-]", 60), "no message rendered"
    s.shot("j1-run-landed")

    mode = s.run_mode()
    assert mode == "focus", f"expected Focus cockpit, got data-mode={mode!r}"
    log(f"PASS  cockpit is Focus (thread-canvas data-mode={mode})")

    steps = fa_poll_growth(s)
    s.shot("j1-streaming-grown")
    growths = len(steps)
    log(
        f"      observed {growths} growth steps; lengths sample={steps[:6]}{'…' if growths > 6 else ''}"
    )
    assert growths >= 3, (
        f"expected >=3 incremental growth steps (streaming), saw {growths} "
        f"— text arrived atomically (regression: model_delta payload is {{delta,message}})"
    )
    log(f"PASS  streaming grows incrementally ({growths} growth steps, not atomic)")


def fa_tool_card(s: DriverSession) -> None:
    log("── J2 tool card ─────────────────────────────────────────────")
    prev = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_TOOL)
    assert fa_wait_new_turn(s, prev), (
        "no new assistant turn after the web-search prompt"
    )

    card = None
    for _ in range(160):  # up to ~40s for the model to call the tool
        card = fa_tool_card_state(s)
        if card is not None:
            break
        time.sleep(0.25)
    s.shot("j2-tool-card")

    if card is None:
        log(
            "BLOCKED  no inline tool card — the keyed model did not call web_search "
            "for this prompt (capability present, not exercised)"
        )
        return

    assert "web_search" in card["text"], (
        f"tool card did not name web_search: {card['text']!r}"
    )
    log("PASS  inline tool card present and names web_search")

    # wait for it to resolve to done
    # The renderer's durable status token is ``complete``; older staged
    # payloads used ``done``. The visible label remains “done” in both cases.
    done = card["status"] in {"complete", "done"}
    for _ in range(120):
        card = fa_tool_card_state(s)
        if card and card["status"] in {"complete", "done"}:
            done = True
            break
        time.sleep(0.25)
    s.shot("j2-tool-card-done")
    if done:
        log("PASS  tool card reached done state")
    else:
        log(
            f"BLOCKED  tool card did not reach done (status={card and card['status']}) "
            "— model/tool did not complete in window"
        )
    if card and card["hasDetails"]:
        log("PASS  tool card exposes a Details expander")
    else:
        log("BLOCKED  no Details expander (no args/result captured on this call)")


def fa_fleet_card(s: DriverSession) -> None:
    log("── J3 subagent fleet card ───────────────────────────────────")
    prev = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_FLEET)
    assert fa_wait_new_turn(s, prev), "no new assistant turn after the subagent prompt"

    fleet = None
    for _ in range(200):  # up to ~50s — subagent dispatch can be slower
        fleet = fa_fleet_card_state(s)
        if fleet is not None:
            break
        time.sleep(0.25)
    s.shot("j3-fleet-card")

    if fleet is None:
        log(
            "BLOCKED  no inline fleet card — the keyed model did not dispatch a "
            "subagent for this prompt (capability present, not exercised)"
        )
        return

    text = fleet["text"]
    assert "ispatched" in text, f"fleet card missing 'Dispatched' copy: {text!r}"
    if "Dispatched a subagent" in text:
        log("PASS  inline fleet card reads 'Dispatched a subagent' (singular)")
    else:
        log(f"PASS  inline fleet card present (batch copy): {text.splitlines()[0]!r}")

    # observe progression to done
    done = "done" in text.lower() or "1/1" in text
    for _ in range(160):
        fleet = fa_fleet_card_state(s)
        if fleet and ("done" in fleet["text"].lower() or "1/1" in fleet["text"]):
            done = True
            break
        time.sleep(0.25)
    s.shot("j3-fleet-done")
    if done:
        log("PASS  fleet progressed to a done state (0/1 → 1/1 done)")
    else:
        log(
            "BLOCKED  fleet did not reach done in window (dispatch present, "
            "completion not observed)"
        )


def fa_focus_panel(s: DriverSession) -> None:
    log("── J4 focus panel + collapse ────────────────────────────────")
    assert s.wait_for("[data-testid=tc-focus-panel]"), "Run-details focus panel absent"
    width = s.evaluate(
        "(document.querySelector('[data-testid=tc-focus-panel]')||{}).offsetWidth||0"
    )
    s.shot("j4-panel-expanded")
    log(f"PASS  Run-details panel shown (offsetWidth={width}px, ~324 expected)")

    assert s.present("[data-testid^=tc-focus-panel-]"), "no active focus-panel tab body"
    log("PASS  focus panel exposes an active tab body (Agents/Approvals/Sources)")

    # collapse → 46px icon rail
    s.click("[data-testid=tc-focus-panel-collapse]")
    assert s.wait_for("[data-testid=tc-focus-strip]"), (
        "collapse did not reveal the icon rail"
    )
    assert not s.present("[data-testid=tc-focus-panel]"), (
        "panel still present after collapse"
    )
    strip_w = s.evaluate(
        "(document.querySelector('[data-testid=tc-focus-strip]')||{}).offsetWidth||0"
    )
    s.shot("j4-panel-collapsed")
    log(
        f"PASS  collapse → 46px icon rail (tc-focus-strip offsetWidth={strip_w}px, ~46 expected)"
    )

    # re-expand
    s.click("[data-testid=tc-focus-strip-expand]")
    assert s.wait_for("[data-testid=tc-focus-panel]"), (
        "re-expand did not restore the panel"
    )
    assert not s.present("[data-testid=tc-focus-strip]"), (
        "icon rail still present after expand"
    )
    s.shot("j4-panel-reexpanded")
    log("PASS  re-expand restored the full panel")


P_LONG = (
    "Do all of the following in this one response, in order, and do not skip a "
    "step:\n"
    "1. Use the web_search tool exactly once to find the official Python "
    "documentation page for math.isqrt, then summarise that page in one "
    "sentence.\n"
    "2. Use your filesystem tool to list the current working directory and say "
    "how many entries it has.\n"
    "3. Dispatch exactly ONE subagent to state the definition of a prime "
    "number, and do not compute it yourself.\n"
    "Finish with a single final answer that contains the documentation URL, the "
    "entry count, and the subagent's definition."
)


TD_GROUPS = """(()=>{
  const td_groups=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  return JSON.stringify(td_groups.map((g)=>({
    state:g.getAttribute('data-state'),
    open:!!g.open,
    pinned:g.getAttribute('data-pinned'),
    label:(g.querySelector('[data-testid=tool-run-group-label]')||{}).textContent||'',
    members:g.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status], [data-testid^="tc-chat-fleet-"]').length,
    collapsedH:Math.round(g.getBoundingClientRect().height),
    bodyH:Math.round(((g.querySelector('.cs-run-group__body')||{}).getBoundingClientRect?.()||{height:0}).height),
  })));
})()"""


TD_LOOSE = """(()=>{
  const all=[...document.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status], [data-testid^="tc-chat-fleet-"]')];
  return all.filter((n)=>!n.closest('[data-testid=tool-run-group]')).length;
})()"""


TD_ORDER = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    if(li.querySelector('[data-testid=tool-run-group]')) return 'group';
    const m=li.getAttribute('data-testid')||'';
    if(m.startsWith('tc-chat-message-')) return 'msg:'+(li.getAttribute('data-role')||'?');
    if(m.startsWith('tc-chat-tool-')) return 'loose-tool';
    if(m.startsWith('tc-chat-fleet-')) return 'loose-fleet';
    return 'other';
  }));
})()"""


def td_groups(s: DriverSession) -> list[dict]:
    raw = s.evaluate(TD_GROUPS)
    return json.loads(raw) if raw else []


def td_wait_quiet(s: DriverSession, timeout_s: int = 180) -> list[dict]:
    """Wait until no group is running and the DOM stops changing."""
    deadline = time.time() + timeout_s
    previous: str | None = None
    stable = 0
    while time.time() < deadline:
        gs = td_groups(s)
        running = any(g["state"] == "running" for g in gs)
        snap = json.dumps(gs, sort_keys=True)
        if not running and gs and snap == previous:
            stable += 1
            if stable >= 8:
                return gs
        else:
            stable = 0
        previous = snap
        time.sleep(0.5)
    raise AssertionError(f"run never settled within {timeout_s}s; last={previous}")


MARK_ONE = "STEP-ONE:"


MARK_TWO = "STEP-TWO:"


J1_PROMPT = (
    "Follow this exact shape and do not deviate.\n"
    f"1. FIRST, before calling any tool at all, write one short sentence that "
    f"begins with the literal text '{MARK_ONE}' saying what you are about to "
    "look up.\n"
    "2. THEN use the web_search tool exactly once to find the official Python "
    "documentation page for math.isqrt.\n"
    f"3. THEN write one short sentence that begins with the literal text "
    f"'{MARK_TWO}' giving the documentation URL.\n"
    "Do not merge steps 1 and 3 into a single sentence, and do not skip step 1."
)


J1_ROWS = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    const id=li.getAttribute('data-testid')||'';
    const activity=li.querySelector('[data-testid=tool-run-group]')
      || (id.startsWith('tc-chat-tool-')?li:null)
      || (id.startsWith('tc-chat-fleet-')?li:null)
      || li.querySelector('[data-testid^="tc-chat-tool-"],[data-testid^="tc-chat-fleet-"]');
    return {
      id,
      role: li.getAttribute('data-role')||null,
      partType: li.getAttribute('data-part-type')||null,
      partSeq: li.getAttribute('data-part-seq')||null,
      activity: !!activity,
      text: (li.textContent||'').trim().slice(0,400),
    };
  }));
})()"""


J1_RUN_ACTIVE = """(()=>{
  const g=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  const running=g.some((n)=>n.getAttribute('data-state')==='running');
  const spin=!!document.querySelector('[data-tool-status=running]');
  return JSON.stringify({running, spin, groups:g.length});
})()"""


def j1_rows(s: DriverSession) -> list[dict]:
    raw = s.evaluate(J1_ROWS)
    return json.loads(raw) if raw else []


def j1_wait_settled(s: DriverSession, timeout_s: int = 240) -> list[dict]:
    """Wait until nothing is running AND the transcript stops changing.

    The stability window matters more than usual here: the terminal re-seed
    swaps the live overlay for persisted history, so a snapshot taken the
    instant the spinner stops can still be the pre-seed DOM.
    """
    deadline = time.time() + timeout_s
    previous: str | None = None
    stable = 0
    while time.time() < deadline:
        state = json.loads(s.evaluate(J1_RUN_ACTIVE) or "{}")
        current = j1_rows(s)
        snap = json.dumps(current, sort_keys=True)
        busy = state.get("running") or state.get("spin")
        if not busy and current and snap == previous:
            stable += 1
            if stable >= 10:
                return current
        else:
            stable = 0
        previous = snap
        time.sleep(0.5)
    raise AssertionError(
        f"run never settled within {timeout_s}s; last j1_rows={previous}"
    )


MARKS = ("STEP-ONE:", "STEP-TWO:", "STEP-THREE:")


J2_PROMPT = (
    "Follow this exact shape, in this order, and do not deviate.\n"
    "1. FIRST, before calling any tool at all, write one short sentence that "
    f"begins with the literal text '{MARKS[0]}' saying what you are about to "
    "research.\n"
    "2. THEN use the web_search tool exactly THREE times yourself — once for "
    "the official Python documentation page for math.isqrt, once for the "
    "official page for math.gcd, and once for the official page for "
    "math.factorial. Do not delegate these.\n"
    f"3. THEN write one short sentence that begins with the literal text "
    f"'{MARKS[1]}' saying what those three pages covered.\n"
    "4. THEN use the web_search tool exactly TWICE more yourself — once for "
    "the official page for math.comb and once for the official page for "
    "math.perm — and, in the same step, dispatch exactly ONE subagent to state "
    "the definition of a prime number. Do not compute that definition "
    "yourself.\n"
    f"5. FINALLY write one short sentence that begins with the literal text "
    f"'{MARKS[2]}' summarising everything.\n"
    "Do not merge the three sentences, and do not skip any of them."
)


J2_ROWS = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    const id=li.getAttribute('data-testid')||'';
    const group=li.querySelector('[data-testid=tool-run-group]');
    const tools=li.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status]').length
      + (id.startsWith('tc-chat-tool-')?1:0);
    const fleets=li.querySelectorAll('[data-testid^="tc-chat-fleet-"]').length
      + (id.startsWith('tc-chat-fleet-')?1:0);
    return {
      id,
      role: li.getAttribute('data-role')||null,
      partSeq: li.getAttribute('data-part-seq')||null,
      tools, fleets,
      activity: !!group || tools>0 || fleets>0,
      groupLabel: group ? (group.getAttribute('data-state')||'') : null,
      text: (li.innerText||'').trim().slice(0,300),
    };
  }));
})()"""


J2_BUSY = """(()=>{
  const g=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  return JSON.stringify({
    running: g.some((n)=>n.getAttribute('data-state')==='running'),
    spin: !!document.querySelector('[data-tool-status=running]'),
  });
})()"""


def j2_rows(s: DriverSession) -> list[dict]:
    raw = s.evaluate(J2_ROWS)
    return json.loads(raw) if raw else []


def j2_wait_settled(s: DriverSession, timeout_s: int = 420) -> list[dict]:
    """Wait until nothing is running AND the transcript stops changing.

    Longer than J1 on purpose: six activity items plus a delegated subagent is a
    genuinely long run, and a timeout here would report a slow model as a
    rendering failure.
    """
    deadline = time.time() + timeout_s
    previous: str | None = None
    stable = 0
    while time.time() < deadline:
        busy = json.loads(s.evaluate(J2_BUSY) or "{}")
        current = j2_rows(s)
        snap = json.dumps(current, sort_keys=True)
        if (
            not (busy.get("running") or busy.get("spin"))
            and current
            and snap == previous
        ):
            stable += 1
            if stable >= 10:
                return current
        else:
            stable = 0
        previous = snap
        time.sleep(0.5)
    raise AssertionError(
        f"run never settled within {timeout_s}s; last j2_rows={previous}"
    )


J3_PROMPT = (
    "First work out, carefully, the exact probability of drawing one ball of "
    "each colour when drawing 3 without replacement from a bag of 5 red, 4 "
    "blue and 3 green — give it as a reduced fraction and justify why it is in "
    "lowest terms. Then use the web_search tool once to find the official "
    "Python documentation page for math.comb, and say how that function relates "
    "to the calculation you just did."
)


J3_PARTS = """(()=>{
  const ul=document.querySelector('[data-testid=tc-chat] ul')||document.querySelector('ul');
  if(!ul) return '[]';
  return JSON.stringify([...ul.children].map((li)=>{
    const id=li.getAttribute('data-testid')||'';
    return {
      id,
      role: li.getAttribute('data-role')||null,
      partType: li.getAttribute('data-part-type')||null,
      partSeq: li.getAttribute('data-part-seq')||null,
      reasoningNodes: li.querySelectorAll('[data-part-type=reasoning], .aui-reasoning, [data-testid*=reasoning]').length,
      activity: !!li.querySelector('[data-testid=tool-run-group]')
        || id.startsWith('tc-chat-tool-') || id.startsWith('tc-chat-fleet-'),
      text: (li.innerText||'').trim().slice(0,200),
    };
  }));
})()"""


J3_BUSY = """(()=>{
  const g=[...document.querySelectorAll('[data-testid=tool-run-group]')];
  return JSON.stringify({running:g.some((n)=>n.getAttribute('data-state')==='running'),
                         spin:!!document.querySelector('[data-tool-status=running]')});
})()"""


J4_PROMPT = (
    "Work out, carefully and from first principles, the exact probability of "
    "drawing one ball of each colour when drawing 3 without replacement from a "
    "bag of 5 red, 4 blue and 3 green. Give it as a reduced fraction and prove "
    "it is in lowest terms. Do not use any tools."
)


J4_SHIMMER = """(()=>{
  const awaiting=document.querySelector('[data-testid=tc-chat-awaiting]');
  const chip=document.querySelector('[data-testid=cs-thinking]');
  const block=document.querySelector('[data-testid=cs-thinking-block]');
  return JSON.stringify({
    awaitingRow: !!awaiting,
    shimmer: !!chip,
    label: chip ? (chip.querySelector('.cs-thinking__label')||{}).textContent||'' : null,
    block: !!block,
    blockStatus: block ? block.getAttribute('data-status') : null,
  });
})()"""


J5_PROMPT = (
    "Work out, carefully and from first principles, the exact probability of "
    "drawing one ball of each colour when drawing 3 without replacement from a "
    "bag of 5 red, 4 blue and 3 green. Give it as a reduced fraction and prove "
    "it is in lowest terms. Do not use any tools."
)


J5_STATE = """(()=>{
  const chip=document.querySelector('[data-testid=cs-thinking]');
  const blocks=[...document.querySelectorAll('[data-testid=cs-thinking-block]')];
  return JSON.stringify({
    shimmer: !!chip,
    label: chip ? (chip.querySelector('.cs-thinking__label')||{}).textContent||'' : null,
    blocks: blocks.length,
    open: blocks.map((b)=>b.open),
    status: blocks.map((b)=>b.getAttribute('data-status')),
    bodyChars: blocks.map((b)=>((b.querySelector('div')||{}).innerText||'').trim().length),
  });
})()"""


def j5_state(s: DriverSession) -> dict:
    return json.loads(s.evaluate(J5_STATE) or "{}")


P_FIRST = "In one short sentence, what is a bicycle?"


P_SECOND = "Now say the same thing in exactly three words."


MINI = "[data-testid=tc-mini-timeline-slot]"


PILL = "[data-testid=tc-mini-timeline-now]"


SWIM_EMPTY = "[data-testid=tc-swimlanes-empty]"


CANVAS = "[data-testid=thread-canvas]"


JS_INSTALL_SAMPLER = """
(() => {
  if (window.__tlStop) { window.__tlStop(); }
  window.__tlSamples = [];
  const tick = () => {
    const canvas = document.querySelector('[data-testid=thread-canvas]');
    window.__tlSamples.push({
      mini: !!document.querySelector('[data-testid=tc-mini-timeline-slot]'),
      pill: !!document.querySelector('[data-testid=tc-mini-timeline-now]'),
      swimEmpty: !!document.querySelector('[data-testid=tc-swimlanes-empty]'),
      listening: (canvas ? canvas.innerText : '').includes('Listening for run events'),
      mode: canvas ? canvas.getAttribute('data-mode') : null,
      beads: document.querySelectorAll('[data-testid^=tc-mini-timeline-bead-]').length,
    });
  };
  tick();
  const h = setInterval(tick, 50);
  window.__tlStop = () => { clearInterval(h); window.__tlStop = null; };
  return true;
})()
"""


JS_READ_SAMPLES = "(() => { if (window.__tlStop) window.__tlStop(); return JSON.stringify(window.__tlSamples || []); })()"


def tl_analyse(samples: list[dict], phase: str) -> list[str]:
    """Return a list of failure strings; empty means the phase passed."""
    failures: list[str] = []
    if not samples:
        return [f"{phase}: sampler collected ZERO samples (probe never ran)"]

    # Only judge frames where the cockpit canvas is actually mounted — a sample
    # taken mid-navigation has no canvas and no opinion about the strip.
    on_canvas = [s for s in samples if s["mode"] is not None]
    if not on_canvas:
        return [f"{phase}: never saw the thread canvas across {len(samples)} samples"]

    missing_mini = [i for i, s in enumerate(on_canvas) if not s["mini"]]
    missing_pill = [i for i, s in enumerate(on_canvas) if not s["pill"]]
    saw_listening = [i for i, s in enumerate(on_canvas) if s["listening"]]
    saw_swim_empty = [i for i, s in enumerate(on_canvas) if s["swimEmpty"]]

    zero_bead_frames = sum(1 for s in on_canvas if s["beads"] == 0)

    log(
        f"  {phase}: {len(on_canvas)} canvas frames "
        f"({zero_bead_frames} with zero beads), "
        f"modes={sorted({s['mode'] for s in on_canvas})}"
    )

    if missing_mini:
        failures.append(
            f"{phase}: BUG 1 — mini-timeline absent in {len(missing_mini)}/"
            f"{len(on_canvas)} frames (first at sample #{missing_mini[0]})"
        )
    if missing_pill:
        failures.append(
            f"{phase}: BUG 1 — Live pill absent in {len(missing_pill)}/"
            f"{len(on_canvas)} frames (first at sample #{missing_pill[0]})"
        )
    if saw_listening:
        failures.append(
            f"{phase}: BUG 2 — 'Listening for run events…' visible in "
            f"{len(saw_listening)}/{len(on_canvas)} frames"
        )
    if saw_swim_empty:
        failures.append(
            f"{phase}: BUG 2 — tc-swimlanes-empty rendered in "
            f"{len(saw_swim_empty)}/{len(on_canvas)} frames"
        )
    # A phase that never observed a zero-bead frame did not exercise the bug at
    # all — report it so a vacuously-green run cannot be mistaken for proof.
    if zero_bead_frames == 0:
        failures.append(
            f"{phase}: INCONCLUSIVE — no zero-bead frame sampled, so the "
            f"vanishing condition was never reached"
        )
    return failures


def tl_switch_mode(s: DriverSession, mode: str) -> None:
    s.click(f"[data-testid=run-mode-{mode}]")
    time.sleep(1.0)


def await_model_pill(s: DriverSession, timeout_s: int = 60) -> str:
    """Block until the composer's model pill names a real model.

    The catalog resolves asynchronously after the key is stored; sending while
    the pill still reads a bare "Model" produces a run that never starts, which
    looks exactly like a cockpit-mount failure. Wait for the real name instead.
    """
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = (s.model_pill() or "").strip()
        if last and last.lower() not in {"model", "model "}:
            return last
        time.sleep(0.5)
    raise AssertionError(f"model pill never resolved (last={last!r})")


P_CITE = "Search the web for what LangGraph is and summarise it in two sentences with sources."


CHIP = "[data-testid=tc-chat] .citation-chip"


POPOVER_STRINGS = [
    "Open external link?",
    "You're about to visit an external website",
]


JS_CHIPS = """
(() => {
  const chips = [...document.querySelectorAll('[data-testid=tc-chat] .citation-chip')];
  const chat = document.querySelector('[data-testid=tc-chat]');
  const text = chat ? chat.innerText : '';
  return JSON.stringify({
    count: chips.length,
    labels: chips.map(c => c.textContent),
    hrefs: chips.map(c => c.getAttribute('href')),
    targets: chips.map(c => c.getAttribute('target')),
    fontSizes: chips.map(c => getComputedStyle(c).fontSize),
    tops: chips.map(c => getComputedStyle(c).top),
    proseFontSize: chat ? getComputedStyle(chat).fontSize : null,
    rawToken: /\\[\\[\\d+\\]\\]/.test(text),
    text: text.slice(0, 400),
  });
})()
"""


JS_ACTIVE_TAB = (
    "(document.querySelector('[data-testid=run-workspace-rail]')||{})"
    ".getAttribute && document.querySelector("
    "'[data-testid=run-workspace-rail]').getAttribute('data-active-tab')"
)


FI_CREATE_PROMPT = """Create exactly two reviewable artifacts in Studio, then stop.

1. A CSV dataset named `bookings-forecast.csv` with exact content:
```csv
month,new_bookings,renewals
2026-09,120000,84000
2026-10,135000,91000
2026-11,148000,96000
```
2. A Markdown document named `forecast-notes.md` with exact content:
```markdown
# Forecast notes

Assumes renewals hold at the trailing three-month average.
```

Publish both artifacts. Do not stage or write workspace files."""


def fi_read_focus(session: DriverSession) -> dict:
    js = """(() => {
      const cards = [...document.querySelectorAll('[data-testid=tc-inline-artifact]')];
      return {
        mode: (document.querySelector('[data-testid=thread-canvas]') || {}).getAttribute
          ? document.querySelector('[data-testid=thread-canvas]').getAttribute('data-mode')
          : null,
        inlineCards: cards.map((c) => ({
          id: c.getAttribute('data-artifact-id'),
          kind: c.getAttribute('data-artifact-kind'),
          hue: c.getAttribute('data-surface-hue'),
          open: c.getAttribute('data-open'),
          name: (c.querySelector('.tc-inline-artifact__name') || {}).textContent || null,
          toggle: (c.querySelector('[data-testid=tc-inline-artifact-toggle]') || {}).textContent || null,
        })),
        pinnedFocusCards: [...document.querySelectorAll('[data-testid=canvas-focus-card]')]
          .map((c) => (c.querySelector('h2') || {}).textContent || null),
        tabStrip: !!document.querySelector('[data-testid=tc-tabs]'),
        artifactFrames: document.querySelectorAll('[data-testid=artifact-frame]').length,
        loadingFrames: document.querySelectorAll('[data-testid=artifact-loading]').length,
      };
    })()"""
    return session.evaluate(js) or {}


def ensure_completed_agent_is_retained(s: DriverSession, task_id: str) -> None:
    """A later run must not clear terminal children from the Agents history."""
    activate_workspace_tab(s, "Agents", "[data-testid=workspace-agents-tab]")
    details = css_test_id(f"agent-activity-row-details-{task_id}")
    assert s.present(details), (
        "completed child from the preceding run vanished when a new message "
        "bound its own run"
    )
    log("PASS  completed subagent remains visible after the next message")


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in_and_key(s: DriverSession) -> None:
    provider, key = byok_provider()
    STATE["provider"] = provider
    STATE["key"] = key
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    s.sign_in_local()
    s.ftue_add_key(provider, key)
    s.shot("byok-ready")


def _needs(*keys: str) -> None:
    """Skip unless every predecessor product is present."""
    for key in keys:
        require(STATE.get(key) is not None, f"needs {key} from an earlier phase")


# ── the rich-card matrix (ordered, dependent) ────────────────────────────────
def tr1_direct_web_search_card(s: DriverSession) -> None:
    """Exactly ONE built-in web_search card, with real args/result and no invented source.

    Also exercises the desktop disclosure contract for that card by pointer,
    Space and Enter — a card that only opens on click is not accessible.
    """

    before = {card["testId"] for card in card_snapshots(s, "tool")}
    s.send_first_run_message(P_WEB)
    assert s.wait_for("[data-testid=tc-chat]", 60), (
        "first run never opened the transcript"
    )
    tool = wait_new_card(s, "tool", before, lambda card: "web_search" in card["text"])
    tool = wait_terminal_card(s, "tool", tool["testId"])
    assert tool["toolStatus"] in {"complete", "done"}, tool
    s.shot("r1-web-search-complete")

    host = css_test_id(tool["testId"])
    details, body = f"{host} details", f"{host} [data-testid$=-details]"
    assert tool["hasDetails"], "completed web-search card is missing its disclosure"
    s.click(f"{details} > summary")
    detail_text = s.evaluate(
        f"(document.querySelector({json.dumps(body)})||{{}}).innerText||''"
    )
    assert all(label in detail_text.lower() for label in ("args", "result")), (
        f"web-search card must render args/result; got {detail_text!r}"
    )
    assert "math.isqrt" in detail_text.lower(), (
        "web-search card rendered stale/empty arguments instead of the "
        f"accumulated query; got {detail_text!r}"
    )
    # `web_search` is built in. The renderer must not invent a connector source;
    # source metadata is shown only when the runtime supplied MCP provenance.
    assert "\nsource\n" not in detail_text.lower(), (
        f"built-in web_search must not display an invented source; got {detail_text!r}"
    )
    s.click(f"{details} > summary")
    ensure_native_disclosure(s, details, body, "Tool card", "math.isqrt")
    wait_cards_quiet(s)
    new_tools = cards_not_seen(s, "tool", before)
    assert len(new_tools) == 1 and new_tools[0]["testId"] == tool["testId"], (
        f"required exactly one direct web search, got {new_tools!r}"
    )
    STATE["tool"] = tool
    log("exactly one direct web_search card, real args/result, no invented source")


def tr2_one_subagent_is_a_singular_fleet(s: DriverSession) -> None:
    """One requested subagent renders as ONE 1/1 fleet card — and no run selector."""

    before = {card["testId"] for card in card_snapshots(s, "fleet")}
    assistant_before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_SINGLE)
    wait_new_assistant(s, assistant_before)
    single = wait_new_card(
        s,
        "fleet",
        before,
        lambda card: "Dispatched a subagent" in card["text"] and card["childRows"] == 1,
    )
    single = wait_terminal_card(s, "fleet", single["testId"])
    assert single["childRows"] == 1 and "1/1 done" in single["text"], single
    assert_completed_children(single, 1)
    wait_cards_quiet(s)
    new_fleets = cards_not_seen(s, "fleet", before)
    assert len(new_fleets) == 1 and new_fleets[0]["testId"] == single["testId"], (
        f"required exactly one single-agent fleet, got {new_fleets!r}"
    )
    assert not s.present("[data-testid=run-multi-select]"), (
        "the retired multi-run selector returned after a second run"
    )
    assert not s.present("[data-testid=receipt-v2-launch]"), (
        "ordinary subagent work surfaced an audit receipt in the cockpit"
    )
    assert_no_ordinary_receipt_tab(s)
    ensure_fleet_card_interaction(s, single["testId"])
    s.shot("r2-one-subagent-complete")
    STATE["single"] = single
    log("exactly one subagent → a singular 1/1 fleet; no run selector")


def tr3_a_new_run_retains_completed_cards(s: DriverSession) -> None:
    """DEPENDS ON TR-1 + TR-2. Binding a new run must not erase finished cards.

    The transcript and the Agents panel are conversation-scoped, not
    run-scoped. This sends the two-subagent prompt and — while that run is
    still live — asserts TR-1's completed web-search card and TR-2's completed
    fleet child are both still present and still interactive.
    """

    _needs("tool", "single")
    tool, single = STATE["tool"], STATE["single"]

    STATE["fleet_before"] = {card["testId"] for card in card_snapshots(s, "fleet")}
    assistant_before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_MULTI)
    wait_new_assistant(s, assistant_before)

    old_task_id = single["childStates"][0]["taskId"]
    assert old_task_id, single
    ensure_completed_agent_is_retained(s, old_task_id)
    activate_workspace_tab(s, "Chat", "[data-testid=tc-chat]")

    historic_tool = next(
        (c for c in card_snapshots(s, "tool") if c["testId"] == tool["testId"]), None
    )
    assert historic_tool is not None and historic_tool["toolStatus"] in {
        "complete",
        "done",
    }, "the completed web-search card vanished when a new run bound"
    host = css_test_id(tool["testId"])
    ensure_native_disclosure(
        s,
        f"{host} details",
        f"{host} [data-testid$=-details]",
        "Historic tool card",
        "math.isqrt",
    )
    historic_single = next(
        (c for c in card_snapshots(s, "fleet") if c["testId"] == single["testId"]), None
    )
    assert historic_single is not None, (
        "the completed fleet card vanished when a new run bound"
    )
    ensure_fleet_card_interaction(s, single["testId"])
    s.shot("r3-retains-historic-rich-cards")
    log("completed tool + fleet cards survived a new run binding")


def tr4_two_parallel_subagents(s: DriverSession) -> None:
    """DEPENDS ON TR-3's send. Two parallel subagents, with a real nested trace.

    The nested-activity assertions run BEFORE terminal completion on purpose:
    they prove the fleet row and the right-side Agents row expose the same real
    tool trace while the delegated work is still active.
    """

    _needs("fleet_before")
    before = STATE["fleet_before"]
    multi = wait_new_card(
        s,
        "fleet",
        before,
        lambda card: (
            "Dispatched 2 subagents in parallel" in card["text"]
            and card["childRows"] == 2
        ),
    )
    web_child = next(
        (c for c in multi["childStates"] if "math.isqrt" in c["text"].lower()), None
    )
    assert web_child is not None and web_child["taskId"], multi
    ensure_fleet_row_interaction(s, multi["testId"], web_child["taskId"], "web_search")
    ensure_agents_panel_interaction(s, web_child["taskId"], "web_search")

    multi = wait_terminal_card(s, "fleet", multi["testId"])
    assert multi["childRows"] == 2 and "2/2 done" in multi["text"], multi
    assert_completed_children(multi, 2)
    wait_cards_quiet(s)
    new_fleets = cards_not_seen(s, "fleet", before)
    assert len(new_fleets) == 1 and new_fleets[0]["testId"] == multi["testId"], (
        f"required exactly one two-agent fleet, got {new_fleets!r}"
    )
    s.shot("r3-two-subagents-complete")
    log("exactly two parallel subagents as one 2/2 fleet, with a live nested trace")


def tr5_mixed_tool_and_fleet_in_one_message(s: DriverSession) -> None:
    """ONE sent message produces a direct tool card AND a two-agent fleet."""

    activate_workspace_tab(s, "Chat", "[data-testid=tc-chat]")
    tool_before = {card["testId"] for card in card_snapshots(s, "tool")}
    fleet_before = {card["testId"] for card in card_snapshots(s, "fleet")}
    assistant_before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_MIXED)
    wait_new_assistant(s, assistant_before)

    mixed_tool = wait_new_card(
        s, "tool", tool_before, lambda card: "web_search" in card["text"]
    )
    mixed_fleet = wait_new_card(
        s,
        "fleet",
        fleet_before,
        lambda card: (
            "Dispatched 2 subagents in parallel" in card["text"]
            and card["childRows"] == 2
        ),
    )
    wait_terminal_card(s, "tool", mixed_tool["testId"])
    mixed_fleet = wait_terminal_card(s, "fleet", mixed_fleet["testId"])
    wait_cards_quiet(s)

    new_tools = [c for c in card_snapshots(s, "tool") if c["testId"] not in tool_before]
    new_web_tools = [c for c in new_tools if "web_search" in c["text"]]
    new_fleets = [
        c for c in card_snapshots(s, "fleet") if c["testId"] not in fleet_before
    ]
    assert (
        len(new_web_tools) == 1 and new_web_tools[0]["testId"] == mixed_tool["testId"]
    ), f"mixed prompt required exactly one direct web search, got {new_tools!r}"
    assert len(new_fleets) == 1 and new_fleets[0]["testId"] == mixed_fleet["testId"], (
        f"mixed prompt required exactly one two-agent fleet, got {new_fleets!r}"
    )
    assert mixed_fleet["childRows"] == 2 and "2/2 done" in mixed_fleet["text"], (
        mixed_fleet
    )
    assert_completed_children(mixed_fleet, 2)
    s.shot("r4-mixed-run-complete")
    log("one message → one direct web tool and one two-agent fleet")


# ── Focus mode ───────────────────────────────────────────────────────────────
def tr9_focus_panel(s: DriverSession) -> None:
    fa_focus_panel(s)
    # Recorded, not asserted: the reasoning block renders only when the backing
    # model emits reasoning summaries, and a small default model emits none.
    # TR-13/TR-15 own that claim against a model chosen for it.
    log("NOTE reasoning block is covered by TR-13/TR-15, not here")


def tr10_focus_inline_artifacts(s: DriverSession) -> None:
    """An artifact is readable WITHOUT leaving Focus mode.

    Focus used to answer "an artifact exists" with a pinned card above the
    transcript, titled by KIND ("document artifact") rather than by the filename
    the user chose, whose only action left the mode entirely. The replacement
    renders IN the thread, collapsed, and expands in place into the same
    `ArtifactSurface` Studio mounts.
    """

    send_in_run(s, FI_CREATE_PROMPT)
    conversation_id = wait_for_conversation_id(s)
    run_id = wait_for_new_run(s, conversation_id, 0)
    wait_for_terminal_run(s, run_id)

    blocked_unless(
        s.wait_for("[data-testid=tc-inline-artifact]", 90),
        "no inline artifact card rendered — the model published no artifact",
    )
    if s.run_mode() != "focus":
        s.click("[data-testid=run-mode-focus]")
        time.sleep(1.5)
    time.sleep(1.5)
    collapsed = fi_read_focus(s)
    s.shot("focus-collapsed-inline-cards")

    cards = collapsed.get("inlineCards") or []
    assert len(cards) >= 2, f"expected two inline cards, saw {len(cards)}: {cards}"
    names = [c.get("name") or "" for c in cards]
    assert not any(n.endswith(" artifact") for n in names), (
        f"an inline card is still titled by kind, not filename: {names}"
    )
    assert all(c.get("open") == "false" for c in cards), (
        "an artifact auto-expanded; minimized is the default"
    )
    assert not any(
        (t or "").endswith(" artifact")
        for t in (collapsed.get("pinnedFocusCards") or [])
    ), f"a pinned kind-labelled card survived: {collapsed}"

    s.click("[data-testid=tc-inline-artifact-toggle]")
    assert s.wait_for("[data-testid=artifact-frame]", 60), (
        "expanding an inline card did not render the artifact"
    )
    time.sleep(1.5)
    expanded = fi_read_focus(s)
    s.shot("focus-expanded-in-place")
    assert expanded.get("mode") == "focus", (
        f"expanding changed the mode to {expanded.get('mode')!r}; reading an "
        "artifact must not leave Focus"
    )
    assert expanded.get("artifactFrames", 0) >= 1, (
        f"no artifact frame after expanding: {expanded}"
    )


# ── turn interleaving ────────────────────────────────────────────────────────
def _assistant_row(rows_: list[dict], mark: str) -> int:
    """Index of the ASSISTANT row carrying a marker.

    Assistant rows only: the user's own turn quotes every marker verbatim (they
    are in the prompt), so searching every row matches the request instead of
    the reply — and then "no activity between them" is trivially true because
    both indices land on row 0.
    """

    return next(
        (
            i
            for i, r in enumerate(rows_)
            if r["role"] == "assistant" and mark in r["text"]
        ),
        -1,
    )


def _persisted_text_blocks(s: DriverSession) -> list[dict]:
    """The assistant message as the SERVER holds it.

    A green DOM must not hide an empty persisted `content` — that would mean
    the transcript survives only until the next reload.
    """

    conversation_id = s.evaluate(
        "(location.hash.match(/conversation[=/]([^&/?]+)/)||[])[1]||null"
    ) or s.evaluate(
        "(document.querySelector('[data-conversation-id]')||{})"
        ".getAttribute?.('data-conversation-id')||null"
    )
    assert conversation_id, "could not resolve the conversation id from the app"
    payload = s.transport("GET", f"/v1/agent/conversations/{conversation_id}/messages")
    assistant = [
        m for m in (payload.get("messages") or []) if m.get("role") == "assistant"
    ]
    assert assistant, "no assistant message was persisted for this conversation"
    return [b for b in (assistant[-1].get("content") or []) if b.get("type") == "text"]


def tr11_prose_survives_the_run_finishing(s: DriverSession) -> None:
    """`text → tool → text`: both prose halves survive, with activity BETWEEN them.

    Asserting AFTER settle is the point. While a run streams the transcript
    comes from the live projection; the moment it goes terminal
    `useRunTranscript` re-seeds from `/messages` and history wins. The reported
    bug was exactly that seam — the turn looked right mid-stream and collapsed
    to its last sentence when the run finished.
    """

    send_in_run(s, J1_PROMPT)
    assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
    settled = j1_wait_settled(s)
    s.shot("j1-settled")

    first = _assistant_row(settled, MARK_ONE)
    second = _assistant_row(settled, MARK_TWO)
    activity = [i for i, r in enumerate(settled) if r["activity"]]

    blocked_unless(
        first != -1 or second != -1,
        "the model emitted neither marker; prompt not honoured",
    )
    blocked_unless(
        activity, "the turn produced no tool activity; nothing to interleave"
    )
    assert first != -1, (
        f"{MARK_ONE!r} is absent from the settled transcript. Either the model "
        "skipped step 1, or the pre-tool prose was destroyed by final_response."
    )
    assert second != -1, (
        f"{MARK_TWO!r} is absent; the turn never produced its closing prose"
    )
    between = [i for i in activity if first < i < second]
    assert between, (
        f"no activity rendered between the prose halves: {MARK_ONE} at row {first}, "
        f"{MARK_TWO} at row {second}, activity at {activity}"
    )

    blocks = _persisted_text_blocks(s)
    assert len(blocks) >= 2, (
        f"the persisted assistant message carries {len(blocks)} text block(s). "
        "The worker did not fold the turn, so this transcript survives only "
        "until the next reload."
    )
    seqs = [b.get("seq") for b in blocks]
    assert all(isinstance(q, int) for q in seqs), (
        f"a text block has no integer seq: {seqs}"
    )
    assert seqs == sorted(seqs), f"persisted blocks are not seq-ordered: {seqs}"
    log(f"activity interleaved at row(s) {between}; persisted seqs={seqs}")


def tr12_two_activity_batches_stay_ordered(s: DriverSession) -> None:
    """`text → 3 tools → text → (2 tools + 1 subagent) → text` stays ordered.

    The old fold kept one accumulator per KIND, so every sentence collapsed into
    a single blob that `final_response` then overwrote, and the whole reply
    carried a single anchor (its first token) so all six activity cards sorted
    after it. None of that is visible with one tool call and one sentence.
    """

    send_in_run(s, J2_PROMPT)
    assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
    settled = j2_wait_settled(s)
    s.shot("j2-settled")

    idx = [_assistant_row(settled, m) for m in MARKS]
    found = [m for m, i in zip(MARKS, idx) if i != -1]
    activity = [i for i, r in enumerate(settled) if r["activity"]]

    blocked_unless(
        len(found) == len(MARKS),
        f"model produced {len(found)}/3 markers ({found}); the shape under test "
        "did not occur",
    )
    blocked_unless(activity, "the turn produced no activity; nothing to interleave")
    assert idx == sorted(idx), (
        f"the three prose segments are out of order: {list(zip(MARKS, idx))}"
    )
    batch_one = [i for i in activity if idx[0] < i < idx[1]]
    batch_two = [i for i in activity if idx[1] < i < idx[2]]
    assert batch_one, (
        f"no activity between {MARKS[0]} (row {idx[0]}) and {MARKS[1]} (row {idx[1]})"
    )
    assert batch_two, (
        f"no activity between {MARKS[1]} (row {idx[1]}) and {MARKS[2]} (row {idx[2]})"
    )

    tools_one = sum(settled[i]["tools"] for i in batch_one)
    tools_two = sum(settled[i]["tools"] for i in batch_two)
    fleets_two = sum(settled[i]["fleets"] for i in batch_two)
    blocks = _persisted_text_blocks(s)
    seqs = [b.get("seq") for b in blocks]
    assert len(blocks) >= 3, (
        f"persisted {len(blocks)} text block(s); a three-segment turn must persist "
        "three, or it collapses to one blob on the next reload"
    )
    assert all(isinstance(q, int) for q in seqs) and seqs == sorted(seqs), (
        f"persisted blocks are not seq-ordered: {seqs}"
    )
    # Ordering — the thing this owns — has now held. Under-delivery by the model
    # is reported separately, and never as a pass.
    blocked_unless(
        tools_one >= 3 and tools_two >= 2 and fleets_two >= 1,
        f"ordering held, but the model produced batch1={tools_one} tools, "
        f"batch2={tools_two} tools + {fleets_two} subagent fleets (asked for 3, then 2 + 1)",
    )
    log(f"two batches interleaved between three prose segments; seqs={seqs}")


def tr13_thinking_reaches_the_transcript(s: DriverSession) -> None:
    """Reasoning must reach the transcript, and interleave with activity.

    Invisible for a different reason per provider: Anthropic returns thinking
    blocks with an empty field unless `thinking.display: "summarized"` is asked
    for (defaulting to "omitted" on the 5 generation, so we paid for the tokens
    and dropped the text); OpenAI returns a `reasoning` block only when it
    actually reasoned, which is why the prompt is deliberately hard.
    """

    send_in_run(s, J3_PROMPT)
    blocked_unless(s.wait_for("[data-testid=tc-chat]", 120), "transcript never opened")

    deadline, previous, stable = time.time() + 300, None, 0
    rows_: list[dict] = []
    while time.time() < deadline:
        busy = json.loads(s.evaluate(J3_BUSY) or "{}")
        rows_ = json.loads(s.evaluate(J3_PARTS) or "[]")
        snap = json.dumps(rows_, sort_keys=True)
        if not (busy.get("running") or busy.get("spin")) and rows_ and snap == previous:
            stable += 1
            if stable >= 10:
                break
        else:
            stable = 0
        previous = snap
        time.sleep(0.5)

    s.shot("j3-settled")
    reasoning = [
        r for r in rows_ if r["partType"] == "reasoning" or r["reasoningNodes"] > 0
    ]
    # Distinguish "the provider sent none" from "we dropped it": this phase
    # cannot tell, so it must not claim either.
    blocked_unless(
        reasoning,
        "no reasoning rendered — either the model skipped thinking on this "
        "input, or the runtime dropped it. Check the run's events.",
    )
    log(f"{len(reasoning)} reasoning row(s) rendered")


def tr14_thinking_shimmer_is_on_screen(s: DriverSession) -> None:
    """The seconds right after send, when the column used to be empty.

    Screenshots EAGERLY and repeatedly rather than waiting for the run to
    finish — by the time a run settles the shimmer is gone, which is correct
    behaviour and useless evidence.
    """

    send_in_run(s, J4_PROMPT)
    seen_awaiting = seen_block = False
    shots = 0
    deadline = time.time() + 90
    while time.time() < deadline:
        state = json.loads(s.evaluate(J4_SHIMMER) or "{}")
        if state.get("shimmer") and shots < 3:
            shots += 1
            s.shot(f"j4-shimmer-{shots}")
        seen_awaiting = seen_awaiting or bool(state.get("awaitingRow"))
        seen_block = seen_block or bool(state.get("block"))
        if seen_awaiting and seen_block:
            break
        time.sleep(0.25)
    blocked_unless(
        seen_awaiting or seen_block,
        "shimmer never appeared; the run may have failed or answered instantly",
    )
    log(f"awaiting-row={seen_awaiting} reasoning-block={seen_block} shots={shots}")


def tr15_thinking_disclosure_collapsed_then_expands(s: DriverSession) -> None:
    """Collapsed by default, expanding in place to the model's reasoning."""

    send_in_run(s, J5_PROMPT)
    deadline = time.time() + 60
    while time.time() < deadline:
        st = j5_state(s)
        if st.get("shimmer"):
            s.shot("j5-waiting")
        if st.get("blocks"):
            break
        time.sleep(0.25)

    previous, stable = None, 0
    deadline = time.time() + 240
    while time.time() < deadline:
        st = j5_state(s)
        snap = json.dumps(st, sort_keys=True)
        if snap == previous and not st.get("shimmer"):
            stable += 1
            if stable >= 8:
                break
        else:
            stable = 0
        previous = snap
        time.sleep(0.5)

    st = j5_state(s)
    blocked_unless(
        st.get("blocks"),
        "no thinking block rendered — the model may not have reasoned on this input",
    )
    s.shot("j5-collapsed")
    assert not any(st["open"]), (
        f"a thinking block opened itself: {st['open']}. Collapsed-by-default is "
        "the contract — an auto-expanding span pushes the answer down the column "
        "every time the model pauses."
    )
    s.click("[data-testid=cs-thinking-block] > summary")
    time.sleep(0.6)
    opened = j5_state(s)
    s.shot("j5-expanded")
    assert any(opened["open"]), "clicking the header did not expand the span"
    assert max(opened["bodyChars"]) > 0, (
        "the disclosure opened but its body is empty — the reasoning text never "
        "reached the part"
    )
    log(f"expands to {max(opened['bodyChars'])} chars of reasoning")


def tr16_long_run_collapses_to_one_line(s: DriverSession) -> None:
    """PRD-03: a long, many-step run folds to one line, with the answer OUTSIDE it."""

    before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
    send_in_run(s, P_LONG)
    assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"

    # 1 — the group must appear WHILE the run is live, and be expanded.
    appeared = False
    deadline = time.time() + 120
    while time.time() < deadline:
        gs = td_groups(s)
        if gs:
            appeared = True
            running = [g for g in gs if g["state"] == "running"]
            if running:
                assert running[0]["open"], (
                    "D-3.2: a running group must be EXPANDED so the user can "
                    f"watch the work; got {running[0]!r}"
                )
                s.shot("td-running-expanded")
                break
        time.sleep(0.5)
    blocked_unless(
        appeared,
        "no tool-run-group ever rendered — the run produced no grouped activity",
    )

    # 2 — settle, then the collapse contract.
    gs = td_wait_quiet(s)
    s.shot("td-settled-collapsed")
    order = json.loads(s.evaluate(TD_ORDER) or "[]")
    loose = int(s.evaluate(TD_LOOSE) or 0)
    assert gs, "the settled transcript has no group"
    total_members = sum(g["members"] for g in gs)
    blocked_unless(
        total_members >= 2,
        f"the run produced only {total_members} activity item(s); grouping was "
        "never exercised",
    )
    for g in gs:
        assert g["state"] in {"settled", "failed"}, g
        if g["state"] == "settled":
            assert not g["open"], f"D-3.2: a settled group must collapse; got {g!r}"
            assert g["label"].startswith("Worked for"), g
        else:
            # D-3.5 — a failed run keeps its detail on screen.
            assert g["open"], f"D-3.5: a failed group must stay open; got {g!r}"

    # 3 — the ANSWER must sit outside the group. This is the finding.
    assert int(s.evaluate(JS_ASSISTANT_COUNT) or 0) > before, (
        "no assistant answer arrived"
    )
    assert order and order[-1].startswith("msg:"), (
        f"the final transcript item must be the answer, not process; got {order!r}"
    )
    assert not s.evaluate(
        "!!document.querySelector('[data-testid=tool-run-group] "
        "[data-testid^=tc-chat-message-][data-role=assistant]')"
    ), "the answer must never be inside the group"

    # 4 — density actually improved.
    settled = [g for g in gs if g["state"] == "settled"]
    for g in settled:
        assert g["collapsedH"] <= 64, (
            f"a collapsed group should be about one card tall; got {g['collapsedH']}px"
        )

    # 5 — nothing is hidden: the disclosure still opens.
    summary = "[data-testid=tool-run-group-summary]"
    assert s.present(summary), "the group has no disclosure control"
    s.click(summary)
    assert any(g["open"] for g in td_groups(s)), "clicking the summary did not expand"
    s.shot("td-expanded-by-user")

    # 6 — RECORDED, not asserted (PRD-03 D-3.1): does the streaming answer
    #     anchor mid-run and split one turn's work into more than one group?
    log(
        f"FINDING {len(gs)} group(s) — the assistant message "
        f"{'did NOT split' if len(gs) == 1 else 'SPLIT'} the run; loose cards={loose}"
    )

    # 7 — the contract must hold at 640px too.
    s.resize(640, 900)
    time.sleep(1.0)
    assert td_groups(s), "the group vanished at 640px"
    s.shot("td-compact-640")
    scroll = s.document_scroll()
    over_x = scroll["scrollWidth"] - scroll["clientWidth"]
    over_y = scroll["scrollHeight"] - scroll["clientHeight"]
    assert over_x <= 1 and over_y <= 1, (
        f"the document must never scroll; overflow x={over_x} y={over_y}"
    )
    s.resize(1200, 800)
    time.sleep(0.5)


def tr17_mini_timeline_survives_a_send(s: DriverSession) -> None:
    """The mini-timeline strip must never vanish, and the removed line never return.

    A screenshot cannot prove this: the gap is transient, so a lucky capture
    proves nothing. A 50ms DOM sampler is installed BEFORE each send and read
    back afterwards, so an absence of even ONE frame is a hard failure.

    BUG 1 the strip (beads + Live pill) vanished on send — in Studio with a run
    bound its gate reduced to `!timelineEmpty`, and sending starts a NEW run
    whose projection resets to zero beads. BUG 2 the swimlanes band rendered a
    "Listening for run events…" line restating what the strip already says.
    """

    failures: list[str] = []

    # The Studio re-send is the exact repro: BUG 1's gate was
    # `mode === "studio" && !(showSwimlanes && empty)`.
    if s.present("[data-testid=run-mode-studio]"):
        tl_switch_mode(s, "studio")
        assert s.run_mode() == "studio", "did not land in Studio"
        s.evaluate(JS_INSTALL_SAMPLER)
        send_in_run(s, P_SECOND)
        time.sleep(12)
        failures += tl_analyse(
            json.loads(s.evaluate(JS_READ_SAMPLES) or "[]"), "studio-send"
        )
        s.shot("tl-studio-after-send")
    else:
        require(False, "no Studio control in this build (Focus-only flag)")

    if s.present("[data-testid=run-mode-focus]"):
        tl_switch_mode(s, "focus")
        if not s.present(MINI):
            failures.append("focus: mini-timeline absent in Focus")
        if not s.present(PILL):
            failures.append("focus: Live pill absent in Focus")
        s.shot("tl-focus")

    if s.present(SWIM_EMPTY):
        failures.append("final: tc-swimlanes-empty still in the DOM")
    canvas_text = s.evaluate(
        "(document.querySelector('[data-testid=thread-canvas]')||{}).innerText||''"
    )
    if "Listening for run events" in (canvas_text or ""):
        failures.append("final: 'Listening for run events…' still rendered")
    s.shot("tl-final-settled")
    if failures:
        raise AssertionError("; ".join(failures))


def tr18_citation_chips(s: DriverSession) -> None:
    """Chips render, don't leak the raw token, and follow through to Sources.

    The four faults behind the reported `[[8]]` + "Open external link?" symptom:
    no chip renderer on desktop, a surviving `[[N]]` token, Streamdown's
    untrusted-link popover, and a chip click that went nowhere.

    Also prints the `sources_probe` diagnostic chain (stream → persisted →
    rendered), which is all that journey ever did.
    """

    failures: list[str] = []
    send_in_run(s, P_CITE)
    assert s.wait_for("[data-testid=thread-canvas]", 120), "no cockpit"

    for _ in range(60):
        time.sleep(1)
        if json.loads(s.evaluate(JS_CHIPS) or "{}").get("count", 0) > 0:
            break
    probe = json.loads(s.evaluate(JS_CHIPS) or "{}")
    s.shot("transcript-with-citations")

    # Diagnostic chain, inherited from `sources_probe` — printed, never asserted.
    conversation_id = s.evaluate(
        "(location.hash.match(/#\\/convo\\/([^/?#]+)/)||[])[1]||null"
    )
    if conversation_id:
        try:
            sources = s.transport(
                "GET", f"/v1/agent/conversations/{conversation_id}/sources"
            )
            log(
                f"DIAGNOSTIC persisted sources rows={len(sources.get('sources') or [])}"
            )
        except Exception as exc:  # noqa: BLE001 — a probe must never fail the phase
            log(f"DIAGNOSTIC persisted sources unavailable: {exc}")
    log(f"DIAGNOSTIC rail tab now = {s.evaluate(JS_ACTIVE_TAB)!r}")

    # The reported symptom, regardless of whether any chip rendered.
    if probe.get("rawToken"):
        failures.append(f"raw [[N]] token in transcript: {probe.get('text')!r}")
    body = s.evaluate("document.body.innerText") or ""
    for needle in POPOVER_STRINGS:
        if needle in body:
            failures.append(f"Streamdown link popover present: {needle!r}")

    if probe.get("count", 0) == 0:
        if failures:
            raise AssertionError("; ".join(failures))
        blocked_unless(
            False, "model answered without citing — chip render/click unproven"
        )

    chip_px = float(probe["fontSizes"][0].removesuffix("px"))
    prose_px = float((probe["proseFontSize"] or "13px").removesuffix("px"))
    if chip_px >= prose_px:
        failures.append(f"chip font {chip_px}px not smaller than prose {prose_px}px")
    if not probe["tops"][0].startswith("-"):
        failures.append(f"chip not lifted above baseline (top={probe['tops'][0]})")
    for href, target in zip(probe["hrefs"], probe["targets"]):
        if href is not None and not href.startswith("#"):
            failures.append(f"chip href is not in-page: {href!r}")
        if target is not None:
            failures.append(f"chip opens a new tab (target={target!r})")

    s.click(CHIP)
    time.sleep(1.5)
    after = s.evaluate(JS_ACTIVE_TAB)
    s.shot("after-chip-click-sources")
    if after != "sources":
        failures.append(f"chip click did not reveal Sources (tab={after!r})")
    if failures:
        raise AssertionError("; ".join(failures))


def main() -> int:
    plan = JourneyPlan("transcript-rendering")
    plan.boot(
        "source · fresh",
        lambda: DriverSession(name="transcript-rendering"),
        setup=sign_in_and_key,
        phases=[
            (
                "TR-1",
                "one direct web_search card with real args/result",
                tr1_direct_web_search_card,
            ),
            (
                "TR-2",
                "one subagent renders as a singular 1/1 fleet",
                tr2_one_subagent_is_a_singular_fleet,
            ),
            (
                "TR-3",
                "a new run retains completed cards [needs TR-1,TR-2]",
                tr3_a_new_run_retains_completed_cards,
            ),
            (
                "TR-4",
                "two parallel subagents with a live nested trace [needs TR-3]",
                tr4_two_parallel_subagents,
            ),
            (
                "TR-5",
                "one message → one tool card and one two-agent fleet",
                tr5_mixed_tool_and_fleet_in_one_message,
            ),
            ("TR-6", "Focus: streaming grows incrementally", fa_streaming),
            ("TR-7", "Focus: an inline tool card", fa_tool_card),
            ("TR-8", "Focus: an inline subagent fleet card", fa_fleet_card),
            (
                "TR-9",
                "Focus: the run-details panel collapses and re-expands",
                tr9_focus_panel,
            ),
            (
                "TR-10",
                "an artifact is readable without leaving Focus",
                tr10_focus_inline_artifacts,
            ),
            (
                "TR-11",
                "prose survives the run finishing, activity between",
                tr11_prose_survives_the_run_finishing,
            ),
            (
                "TR-12",
                "two activity batches stay ordered across three prose segments",
                tr12_two_activity_batches_stay_ordered,
            ),
            (
                "TR-13",
                "thinking reaches the transcript",
                tr13_thinking_reaches_the_transcript,
            ),
            (
                "TR-14",
                "the Thinking shimmer is on screen during the wait",
                tr14_thinking_shimmer_is_on_screen,
            ),
            (
                "TR-15",
                "the thinking disclosure is collapsed, then expands",
                tr15_thinking_disclosure_collapsed_then_expands,
            ),
            (
                "TR-16",
                "a long run collapses to one line, answer outside",
                tr16_long_run_collapses_to_one_line,
            ),
            (
                "TR-17",
                "the mini-timeline survives a send",
                tr17_mini_timeline_survives_a_send,
            ),
            (
                "TR-18",
                "citation chips render, don't leak, and follow",
                tr18_citation_chips,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
