#!/usr/bin/env python3
"""Required live desktop matrix for tool + subagent rich chat cards.

This is intentionally stricter than a smoke test. It uses the real supervised
Electron app, adds a BYOK provider key through the actual first-run UI, and
requires the requested activity to render. A model that ignores a direct
instruction to call web search or dispatch a stated number of subagents fails
the journey rather than producing a misleading green "BLOCKED" result.

The provider key is loaded from services/ai-backend/.env and sent only to the
password field. Its value is never printed, persisted in artifacts, or logged.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402


PROVIDER = os.environ.get("RICH_CHAT_PROVIDER", "openai")

# Keep these prompts deliberately unambiguous. Each tests a separate live
# rendering contract, culminating in the mixed one-message case.
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


def log(line: str) -> None:
    print(line, flush=True)


def css_test_id(test_id: str) -> str:
    return f"[data-testid={json.dumps(test_id)}]"


def send_in_run(s: DriverSession, text: str) -> None:
    assert s.wait_for("[data-testid=composer-textarea]"), "run composer never appeared"
    s.fill("[data-testid=composer-textarea]", text)
    s.click('button[aria-label="Send message"]')


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


def ensure_completed_agent_is_retained(s: DriverSession, task_id: str) -> None:
    """A later run must not clear terminal children from the Agents history."""
    activate_workspace_tab(s, "Agents", "[data-testid=workspace-agents-tab]")
    details = css_test_id(f"agent-activity-row-details-{task_id}")
    assert s.present(details), (
        "completed child from the preceding run vanished when a new message "
        "bound its own run"
    )
    log("PASS  completed subagent remains visible after the next message")


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] in {"-h", "--help"}:
            print(
                "Usage: python3 tools/desktop-journeys/chat-rich-cards/rich_chat.py\n"
                "Runs the strict R1–R11 desktop rich-chat matrix. "
                "Set RICH_CHAT_PROVIDER=openai|anthropic to select the BYOK key."
            )
            return 0
        raise SystemExit(f"unsupported argument: {sys.argv[1]!r}; use --help")
    key = load_env_key(PROVIDER)
    with DriverSession(name="chat-rich-cards") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        s.shot("00-byok-ready")
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        # R1 — one direct web-search tool card with complete payload rendering.
        log("── R1 direct web search ───────────────────────────────────")
        tool_before_ids = {card["testId"] for card in card_snapshots(s, "tool")}
        s.send_first_run_message(P_WEB)
        assert s.wait_for("[data-testid=tc-chat]", 60), (
            "first run never opened the transcript"
        )
        tool = wait_new_card(
            s,
            "tool",
            tool_before_ids,
            lambda card: "web_search" in card["text"],
        )
        tool = wait_terminal_card(s, "tool", tool["testId"])
        assert tool["toolStatus"] in {"complete", "done"}, tool
        s.shot("r1-web-search-complete")
        tool_host = css_test_id(tool["testId"])
        tool_details = f"{tool_host} details"
        tool_body = f"{tool_host} [data-testid$=-details]"
        assert tool["hasDetails"], "completed web-search card is missing its disclosure"
        s.click(f"{tool_details} > summary")
        detail_text = s.evaluate(
            f"(document.querySelector({json.dumps(tool_body)})||{{}}).innerText||''"
        )
        assert all(label in detail_text.lower() for label in ("args", "result")), (
            f"web-search card must render args/result; got {detail_text!r}"
        )
        assert "math.isqrt" in detail_text.lower(), (
            "web-search card rendered stale/empty arguments instead of the "
            f"accumulated query; got {detail_text!r}"
        )
        # `web_search` is built in. The renderer must not invent a connector
        # source; source metadata is shown only when the runtime supplied MCP
        # provenance for that invocation.
        assert "\nsource\n" not in detail_text.lower(), (
            "built-in web_search must not display an invented source; "
            f"got {detail_text!r}"
        )
        s.click(f"{tool_details} > summary")
        log("── R5 tool-card interaction ────────────────────────────────")
        ensure_native_disclosure(
            s,
            tool_details,
            tool_body,
            "Tool card",
            "math.isqrt",
        )
        wait_cards_quiet(s)
        new_tools = cards_not_seen(s, "tool", tool_before_ids)
        assert len(new_tools) == 1 and new_tools[0]["testId"] == tool["testId"], (
            f"R1 required exactly one direct web search, got {new_tools!r}"
        )
        log(
            "PASS  exactly one direct web_search card rendered real args/result and no invented source"
        )

        # R2 — the exact one-subagent prompt requested in this task.
        log("── R2 exactly one subagent ─────────────────────────────────")
        fleet_before_ids = {card["testId"] for card in card_snapshots(s, "fleet")}
        assistant_before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
        send_in_run(s, P_SINGLE)
        wait_new_assistant(s, assistant_before)
        single = wait_new_card(
            s,
            "fleet",
            fleet_before_ids,
            lambda card: (
                "Dispatched a subagent" in card["text"] and card["childRows"] == 1
            ),
        )
        single = wait_terminal_card(s, "fleet", single["testId"])
        assert single["childRows"] == 1 and "1/1 done" in single["text"], single
        assert_completed_children(single, 1)
        wait_cards_quiet(s)
        new_fleets = cards_not_seen(s, "fleet", fleet_before_ids)
        assert len(new_fleets) == 1 and new_fleets[0]["testId"] == single["testId"], (
            f"R2 required exactly one single-agent fleet, got {new_fleets!r}"
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
        log(
            "PASS  exactly one subagent rendered as a singular 1/1 fleet; no run selector"
        )

        # R3 — real parallel fanout, distinct from a single-agent card.
        log("── R3 exactly two parallel subagents ───────────────────────")
        fleet_before_ids = {card["testId"] for card in card_snapshots(s, "fleet")}
        assistant_before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
        send_in_run(s, P_MULTI)
        wait_new_assistant(s, assistant_before)
        # R8 — the run stream now follows R3, but R2's terminal child belongs
        # to this conversation and must remain in the side-panel history.
        old_task_id = single["childStates"][0]["taskId"]
        assert old_task_id, single
        ensure_completed_agent_is_retained(s, old_task_id)
        # R10 — the transcript itself is conversation-scoped too. A new live
        # run must not erase R2's compact, expandable fleet card above it.
        activate_workspace_tab(s, "Chat", "[data-testid=tc-chat]")
        # R11 — direct main-agent tool cards use the same conversation ledger.
        # Starting R3 must not make R1's completed built-in web search vanish.
        historic_tool = next(
            (
                card
                for card in card_snapshots(s, "tool")
                if card["testId"] == tool["testId"]
            ),
            None,
        )
        assert historic_tool is not None and historic_tool["toolStatus"] in {
            "complete",
            "done",
        }, "completed R1 web-search card vanished when R3 bound a new run"
        historic_tool_host = css_test_id(tool["testId"])
        ensure_native_disclosure(
            s,
            f"{historic_tool_host} details",
            f"{historic_tool_host} [data-testid$=-details]",
            "Historic tool card",
            "math.isqrt",
        )
        historic_single = next(
            (
                card
                for card in card_snapshots(s, "fleet")
                if card["testId"] == single["testId"]
            ),
            None,
        )
        assert historic_single is not None, (
            "completed R2 fleet card vanished when R3 bound a new run"
        )
        ensure_fleet_card_interaction(s, single["testId"])
        s.shot("r3-retains-historic-rich-cards")
        multi = wait_new_card(
            s,
            "fleet",
            fleet_before_ids,
            lambda card: (
                "Dispatched 2 subagents in parallel" in card["text"]
                and card["childRows"] == 2
            ),
        )
        web_child = next(
            (
                child
                for child in multi["childStates"]
                if "math.isqrt" in child["text"].lower()
            ),
            None,
        )
        assert web_child is not None and web_child["taskId"], multi
        # R6/R7 intentionally run before terminal completion. This proves the
        # fleet and the right-side Agents row expose the same real nested tool
        # trace while the delegated work is still active.
        log("── R6–R7 live nested-activity interactions ─────────────────")
        ensure_fleet_row_interaction(
            s,
            multi["testId"],
            web_child["taskId"],
            "web_search",
        )
        ensure_agents_panel_interaction(s, web_child["taskId"], "web_search")
        multi = wait_terminal_card(s, "fleet", multi["testId"])
        assert multi["childRows"] == 2 and "2/2 done" in multi["text"], multi
        assert_completed_children(multi, 2)
        wait_cards_quiet(s)
        new_fleets = cards_not_seen(s, "fleet", fleet_before_ids)
        assert len(new_fleets) == 1 and new_fleets[0]["testId"] == multi["testId"], (
            f"R3 required exactly one two-agent fleet, got {new_fleets!r}"
        )
        s.shot("r3-two-subagents-complete")
        log("PASS  exactly two parallel subagents rendered as one 2/2 fleet")

        # R4 — direct tool + two delegated agents in ONE sent message.
        log("── R4 web search plus subagents in one message ─────────────")
        activate_workspace_tab(s, "Chat", "[data-testid=tc-chat]")
        tool_before_ids = {card["testId"] for card in card_snapshots(s, "tool")}
        fleet_before_ids = {card["testId"] for card in card_snapshots(s, "fleet")}
        assistant_before = int(s.evaluate(JS_ASSISTANT_COUNT) or 0)
        send_in_run(s, P_MIXED)
        wait_new_assistant(s, assistant_before)
        mixed_tool = wait_new_card(
            s,
            "tool",
            tool_before_ids,
            lambda card: "web_search" in card["text"],
        )
        mixed_fleet = wait_new_card(
            s,
            "fleet",
            fleet_before_ids,
            lambda card: (
                "Dispatched 2 subagents in parallel" in card["text"]
                and card["childRows"] == 2
            ),
        )
        wait_terminal_card(s, "tool", mixed_tool["testId"])
        mixed_fleet = wait_terminal_card(s, "fleet", mixed_fleet["testId"])
        wait_cards_quiet(s)
        new_tools = [
            card
            for card in card_snapshots(s, "tool")
            if card["testId"] not in tool_before_ids
        ]
        new_web_tools = [card for card in new_tools if "web_search" in card["text"]]
        new_fleets = [
            card
            for card in card_snapshots(s, "fleet")
            if card["testId"] not in fleet_before_ids
        ]
        assert (
            len(new_web_tools) == 1
            and new_web_tools[0]["testId"] == mixed_tool["testId"]
        ), f"mixed prompt required exactly one direct web search, got {new_tools!r}"
        assert (
            len(new_fleets) == 1 and new_fleets[0]["testId"] == mixed_fleet["testId"]
        ), f"mixed prompt required exactly one two-agent fleet, got {new_fleets!r}"
        assert mixed_fleet["childRows"] == 2 and "2/2 done" in mixed_fleet["text"], (
            mixed_fleet
        )
        assert_completed_children(mixed_fleet, 2)
        s.shot("r4-mixed-run-complete")
        log("PASS  one message rendered one direct web tool and one two-agent fleet")

        s.shot("r5-r7-card-interactions")

    log("ALL REQUIRED RICH-CHAT DESKTOP ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
