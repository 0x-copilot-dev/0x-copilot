#!/usr/bin/env python3
"""Live desktop proof for the agent's todo checklist (T1–T5 in JOURNEYS.md).

Drives the real supervised app with a real BYOK key and a prompt that asks for
three tracked steps, then asserts what a user actually sees: a pinned checklist
above the composer whose rows advance from spinner to tick, with neither the raw
`write_todos` tool card nor the deleted Focus "Plan" anywhere in sight.

The value of running this against the packaged app rather than a harness is the
class of failure it catches: every layer between the worker and the pixel can
drop the new event *silently* — most sharply the client's `isRuntimeEventEnvelope`
guard, which discards an envelope whose `event_type` is missing from the shared
allowlist and reports nothing.

The provider key is read from services/ai-backend/.env and typed only into the
password field. Its value is never printed, screenshotted, or logged.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import DriverSession, load_env_key  # noqa: E402

PROVIDER = os.environ.get("AGENT_TODOS_PROVIDER", "openai")

# Deliberately unambiguous, and deliberately tool-free: the point is the
# checklist, so the run must not depend on web access or a connector. Asking for
# the steps to be done ONE AT A TIME is what forces repeated `write_todos` calls
# and therefore a visible status transition rather than one static list.
PROMPT = (
    "Use the write_todos tool to plan this work as exactly THREE todos, then "
    "carry them out one at a time, marking each todo completed before you start "
    "the next one: (1) state the definition of a prime number, (2) determine "
    "whether 97 is prime by trial division, (3) determine whether 91 is prime by "
    "trial division. Do not delegate to subagents. Give all three answers."
)

TODO_STATUSES = {"pending", "in_progress", "completed"}


def log(line: str) -> None:
    print(line, flush=True)


def panel(s: DriverSession) -> dict | None:
    """Read the checklist as the user sees it, or None when no panel is mounted."""
    js = """(()=>{
      const root=document.querySelector('[data-testid=tc-todo-list]');
      if(!root) return "null";
      const rows=[...root.querySelectorAll('[data-testid=tc-todo-row]')].map((r)=>({
        status:r.getAttribute('data-status'),
        text:r.innerText,
        spinner:!!r.querySelector('[data-testid=tc-todo-spinner]'),
      }));
      const count=root.querySelector('[data-testid=tc-todo-list-count]');
      const next=root.nextElementSibling;
      return JSON.stringify({
        rows,
        count:count&&count.innerText,
        complete:root.getAttribute('data-complete'),
        collapsed:root.getAttribute('data-collapsed'),
        generation:root.getAttribute('data-generation'),
        summary:(root.querySelector('[data-testid=tc-todo-list-summary]')||{}).innerText||null,
        nextTestId:next&&next.getAttribute('data-testid'),
      });
    })()"""
    raw = s.evaluate(js)
    return json.loads(raw) if raw and raw != "null" else None


def wait_for_panel(s: DriverSession, timeout_s: int = 120) -> dict:
    """Wait until the checklist has rendered at least one row."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        view = panel(s)
        if view and view["rows"]:
            return view
        time.sleep(0.5)
    raise AssertionError(
        f"the todo panel never rendered a row within {timeout_s}s — the agent "
        "either never called write_todos, or the event never reached the client"
    )


def write_todos_cards(s: DriverSession) -> list[str]:
    """Any inline tool card mentioning write_todos — there must never be one."""
    js = """JSON.stringify(
      [...document.querySelectorAll('[data-testid^="tc-chat-tool-"]')]
        .map((n)=>n.innerText)
        .filter((t)=>t.toLowerCase().includes('write_todos'))
    )"""
    return json.loads(s.evaluate(js) or "[]")


def observe(s: DriverSession, seen: dict, timeout_s: int = 180) -> dict:
    """Poll the panel until the run settles, recording every state it passed through.

    Returns the accumulated observations: which statuses were ever rendered, the
    highest completed count reached, and whether a spinner was ever on screen.
    """
    deadline = time.time() + timeout_s
    stable = 0
    last = None
    while time.time() < deadline:
        view = panel(s)
        if view:
            seen["last"] = view
            seen["max_completed"] = max(
                seen["max_completed"],
                sum(1 for r in view["rows"] if r["status"] == "completed"),
            )
            for row in view["rows"]:
                seen["statuses"].add(row["status"])
                if row["spinner"]:
                    seen["spinner_seen"] = True
            # The raw card must be absent at EVERY sampled moment, not merely at
            # the end — a card that flashes during the run is still the bug.
            leaked = write_todos_cards(s)
            assert not leaked, f"a raw write_todos tool card rendered: {leaked!r}"
            snapshot = json.dumps(view, sort_keys=True)
            stable = stable + 1 if snapshot == last else 0
            last = snapshot
            if stable >= 12 and seen["max_completed"] > 0:
                return seen
        time.sleep(0.5)
    return seen


def latest_run_events(s: DriverSession) -> list[dict]:
    """Replay the bound run's events through the app's authenticated transport."""
    conversation_id = s.evaluate(
        "(location.hash.match(/conversation[=/]([^&/?]+)/)||[])[1]||null"
    )
    if not conversation_id:
        conversation_id = s.evaluate(
            "(document.querySelector('[data-conversation-id]')||{})"
            ".getAttribute?.('data-conversation-id')||null"
        )
    assert conversation_id, "could not resolve the conversation id from the app"
    conversation = s.transport("GET", f"/v1/agent/conversations/{conversation_id}")
    run_id = conversation.get("latest_run_id") or conversation.get(
        "latest_run_id_any_status"
    )
    assert run_id, f"conversation {conversation_id} exposed no run id"
    replay = s.transport("GET", f"/v1/agent/runs/{run_id}/events")
    return replay.get("events", replay if isinstance(replay, list) else [])


def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] in {"-h", "--help"}:
            print(
                "Usage: python3 tools/desktop-journeys/agent-todos/todo_panel.py\n"
                "Runs T1-T5. Set AGENT_TODOS_PROVIDER=openai|anthropic."
            )
            return 0
        raise SystemExit(f"unsupported argument: {sys.argv[1]!r}; use --help")

    key = load_env_key(PROVIDER)
    with DriverSession(name="agent-todos") as s:
        s.sign_in_local()
        s.ftue_add_key(PROVIDER, key)
        log(f"PASS  BYOK ready for {PROVIDER}; credential value withheld")

        s.send_first_run_message(PROMPT)
        assert s.wait_for("[data-testid=tc-chat]", 60), (
            "the first run never opened the transcript"
        )

        # T1 — the checklist renders, pinned above the composer.
        log("── T1 checklist renders ────────────────────────────────────")
        first = wait_for_panel(s)
        s.shot("t1-todo-panel-visible")
        log(f"      first render: {json.dumps(first['rows'], indent=None)}")
        assert len(first["rows"]) >= 3, (
            f"expected at least the three requested todos; got {first['rows']!r}"
        )
        for row in first["rows"]:
            assert row["status"] in TODO_STATUSES, row
            assert row["text"].strip(), f"a todo row rendered with no text: {row!r}"
        assert first["nextTestId"] == "tc-chat-composer-slot", (
            "the checklist must sit directly above the composer; its next sibling "
            f"was {first['nextTestId']!r}"
        )
        log(f"PASS  T1 {len(first['rows'])} rows, pinned above the composer")

        # T2 — rows advance from spinner to tick.
        log("── T2 spinner → tick ───────────────────────────────────────")
        seen = {
            "statuses": set(),
            "max_completed": 0,
            "spinner_seen": False,
            "last": first,
        }
        for row in first["rows"]:
            seen["statuses"].add(row["status"])
            if row["spinner"]:
                seen["spinner_seen"] = True
        seen = observe(s, seen)
        s.shot("t2-todo-panel-progressed")
        final = seen["last"]
        log(f"      statuses seen: {sorted(seen['statuses'])}")
        log(f"      final panel: {json.dumps(final, indent=None)}")
        assert seen["spinner_seen"] or "in_progress" in seen["statuses"], (
            "no row was ever in_progress — the panel never showed live work"
        )
        assert seen["max_completed"] > 0, (
            "no row ever reached completed — the tick transition never happened; "
            f"final panel was {final!r}"
        )
        log(
            f"PASS  T2 reached {seen['max_completed']} completed row(s); "
            f"spinner observed={seen['spinner_seen']}"
        )

        # T3 — the raw write_todos card never appeared (also checked every poll).
        log("── T3 no raw write_todos card ──────────────────────────────")
        assert not write_todos_cards(s), "a raw write_todos tool card is on screen"
        log("PASS  T3 write_todos never rendered as a tool card")

        # T4 — the invented Plan is gone, in both modes.
        log("── T4 the invented Plan is gone ────────────────────────────")
        assert not s.present("[data-testid=focus-plan]"), "Studio still renders a Plan"
        s.click("[data-testid=run-mode-focus]")
        assert s.wait_for("[data-testid=tc-focus-panel]", 20), (
            "Focus mode never opened its Activity panel"
        )
        s.shot("t4-focus-no-plan")
        assert not s.present("[data-testid=focus-plan]"), (
            "the Focus Activity panel still renders the deleted Plan"
        )
        focus_view = panel(s)
        assert (
            focus_view and focus_view["rows"] or (focus_view and focus_view["summary"])
        ), "the checklist did not survive the switch to Focus"
        log("PASS  T4 no Plan in Studio or Focus; checklist survives the mode switch")

        # T5 — the backend really emitted the snapshots.
        log("── T5 backend emitted todo_list_updated ────────────────────")
        events = latest_run_events(s)
        snapshots = [e for e in events if e.get("event_type") == "todo_list_updated"]
        assert snapshots, (
            "the run's replay carries no todo_list_updated event, so the panel "
            "rendered something the server never sent"
        )
        payload = snapshots[0].get("payload") or {}
        assert payload.get("list_id"), payload
        assert isinstance(payload.get("generation"), int), payload
        rows = payload.get("todos")
        assert isinstance(rows, list) and rows, payload
        for row in rows:
            assert isinstance(row.get("content"), str) and row["content"].strip(), row
            assert row.get("status") in TODO_STATUSES, row
        log(
            f"PASS  T5 {len(snapshots)} todo_list_updated event(s); "
            f"first carried {len(rows)} structured rows"
        )

        s.shot("99-done")
        log("")
        log("JOURNEY PASSED — agent todos render live in the packaged desktop app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
