#!/usr/bin/env python3
"""composer-and-budgets — the composer's controls, and the limits that govern a run.

Five originals that all asked one question: does a control the user can see
actually reach the runtime? The Tools pill, the todo checklist, the tool-call
cap, and the two ways a run reports its own health.

The phases are ordered by the state they consume. CB-1 needs the VIRGIN
first-run composer; CB-2 needs a bound run; CB-7 changes a persisted Settings
value and therefore runs LAST — a tool-call cap set earlier would silently
rewrite CB-6's premise, which is about overrunning the DEFAULT budget.

    python3 tools/desktop-journeys/composer_and_budgets.py

Folds in: composer-tools/tools_popover, agent-todos/todo_panel,
chat-rich-cards/{declined_capability, budget_overrun, tool_budget_setting}.

The provider key is read from services/ai-backend/.env and only ever reaches the
password field — never printed, logged, or written to an artifact.
"""

from __future__ import annotations

import json
import time

from _lib import DriverSession, JourneyPlan, byok_provider


STATE: dict[str, object] = {}


def log(line: str) -> None:
    print(f"  {line}", flush=True)


PLUS = 'button[aria-label="Open attachment and tools menu"]'


TOOLS_BUTTON = "[data-testid=first-run-tools-button]"


TOOLS_MENU = "[data-testid=composer-tools-popover]"


WEB_SEARCH = "[data-testid=first-run-tools-websearch]"


RUN_HEADER = "[data-testid=run-header]"


def wait_until_absent(
    session: DriverSession, selector: str, timeout_s: int = 8
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not session.present(selector):
            return True
        time.sleep(0.15)
    return False


def row_receives_pointer(session: DriverSession) -> bool:
    return bool(
        session.evaluate(
            """(() => {
              const row = document.querySelector('[data-testid=first-run-tools-websearch]');
              if (!row) return false;
              const rect = row.getBoundingClientRect();
              const target = document.elementFromPoint(rect.left + 12, rect.top + 12);
              return target !== null && row.contains(target);
            })()"""
        )
    )


def open_tools_with_pointer(session: DriverSession) -> None:
    session.click(TOOLS_BUTTON)
    assert session.wait_for(TOOLS_MENU, timeout_s=10), "Tools pill did not open"


def assert_single_entry_point(session: DriverSession, label: str) -> None:
    assert session.present(TOOLS_BUTTON), f"{label}: composer Tools pill is absent"
    # The attachment menu is allowed to keep its attach/MCP/skills features,
    # but must no longer become a duplicate route to per-run Tools.
    session.click(PLUS)
    assert not session.present("[data-testid=composer-plus-menu-tools]"), (
        f"{label}: attachment `+` menu still exposes a duplicate Tools entry"
    )
    session.click(PLUS)


def exercise_tools(
    session: DriverSession,
    label: str,
    *,
    keyboard: bool = False,
    outside_selector: str = RUN_HEADER,
) -> None:
    """Pointer-open, verify hit target, toggle, click-out; optionally keys."""
    assert_single_entry_point(session, label)
    open_tools_with_pointer(session)
    time.sleep(0.2)  # let the shared pop recipe settle before compositor sampling
    session.shot(f"{label}-tools-open")
    assert row_receives_pointer(session), f"{label}: Web Search row is clipped/covered"

    before = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    assert before in ("true", "false"), f"{label}: Web Search has no aria state"
    session.click(WEB_SEARCH)
    after = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    assert after != before, f"{label}: pointer click did not toggle Web Search"

    session.click(outside_selector)
    assert wait_until_absent(session, TOOLS_MENU), (
        f"{label}: click-out did not close Tools"
    )

    if not keyboard:
        return

    # Use real Enter/Space events, not synthetic DOM state changes.
    session.press(TOOLS_BUTTON, "Enter")
    assert session.wait_for(TOOLS_MENU, timeout_s=5), (
        f"{label}: Enter did not open Tools"
    )
    before = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    session.press(WEB_SEARCH, "Space")
    after = session.evaluate(
        "document.querySelector('[data-testid=first-run-tools-websearch]').getAttribute('aria-checked')"
    )
    assert after != before, f"{label}: Space did not toggle Web Search"
    session.press(WEB_SEARCH, "Escape")
    assert wait_until_absent(session, TOOLS_MENU), (
        f"{label}: Escape did not close Tools"
    )


def set_mode(session: DriverSession, mode: str) -> None:
    session.click(f"[data-testid=run-mode-{mode}]")
    deadline = time.time() + 8
    while time.time() < deadline:
        # A blank New Chat has no ThreadCanvas yet, so `data-mode` is absent
        # until its first send. The segmented control is the source of truth in
        # both blank and bound states; when a canvas exists, assert it agrees.
        selected = session.evaluate(
            f"document.querySelector('[data-testid=run-mode-{mode}]')?.getAttribute('aria-selected')"
        )
        canvas_mode = session.run_mode()
        if selected == "true" and (canvas_mode is None or canvas_mode == mode):
            return
        time.sleep(0.15)
    raise AssertionError(
        f"mode did not become {mode!r}; selected={selected!r}; canvas={session.run_mode()!r}"
    )


def wait_for_bound_run(session: DriverSession) -> None:
    deadline = time.time() + 120
    while time.time() < deadline:
        if session.on_run() and session.present("[data-testid=run-mode-switcher]"):
            return
        time.sleep(0.5)
    raise AssertionError("first message never reached a bound Run cockpit")


TODO_PROMPT = (
    "Use the write_todos tool to plan this work as exactly THREE todos, then "
    "carry them out one at a time, marking each todo completed before you start "
    "the next one: (1) state the definition of a prime number, (2) determine "
    "whether 97 is prime by trial division, (3) determine whether 91 is prime by "
    "trial division. Do not delegate to subagents. Give all three answers."
)


TODO_STATUSES = {"pending", "in_progress", "completed"}


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


DC_PROMPT = (
    "Run `ls /workspace/` to find the mounted folder, read seed.csv inside it, "
    "then write the file back to that same path with one extra column named "
    "`note` whose value is `checked` on every row. Use your filesystem tools."
)


DC_ASSISTANT_COUNT = (
    'document.querySelectorAll("[data-testid^=tc-chat-message-]'
    '[data-role=assistant]").length'
)


DC_CANVAS_ALARM = """(()=>{
  const body=document.body?document.body.innerText:'';
  return /RUN INTERRUPTED|This run needs attention|This operation failed/i
    .test(body) ? body.slice(0,400) : '';
})()"""


DC_RUN_LEVEL_ACTION = """(()=>{
  const hits=[...document.querySelectorAll('button')]
    .map((b)=>(b.innerText||'').trim())
    .filter((t)=>/^Retry run$|^Start a new run with this goal$/.test(t));
  const beat=document.querySelector('[data-testid="run-terminal-beat"]');
  return JSON.stringify({buttons:hits, terminalBeat:!!beat});
})()"""


DC_CANVAS_STATE = """(()=>{
  const p=document.querySelector('[data-testid="canvas-lifecycle-panel"]');
  return p?JSON.stringify({
    lifecycle:p.getAttribute('data-lifecycle'),
    text:(p.innerText||'').slice(0,200),
  }):'';
})()"""


DC_TOOL_CARDS = """(()=>JSON.stringify(
  [...document.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status]')]
    .map((n)=>({
      testId:n.getAttribute('data-testid'),
      status:n.getAttribute('data-tool-status'),
      text:(n.innerText||'').slice(0,240),
    }))
))()"""


DC_LAST_ASSISTANT = """(()=>{
  const nodes=[...document.querySelectorAll(
    '[data-testid^=tc-chat-message-][data-role=assistant]')];
  const last=nodes[nodes.length-1];
  return last?(last.innerText||'').slice(0,600):'';
})()"""


def dc_wait_for_answer(s: DriverSession, before: int, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if int(s.evaluate(DC_ASSISTANT_COUNT) or 0) > before:
            return
        time.sleep(0.5)
    raise AssertionError(f"no assistant answer within {timeout_s}s")


PROMPTS = [
    (
        "report-1",
        "Catch me up on this week's AI agent releases, then draft a short "
        "update I can bo_send the team.",
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


BO_ASSISTANT_COUNT = (
    'document.querySelectorAll("[data-testid^=tc-chat-message-]'
    '[data-role=assistant]").length'
)


BO_INTERRUPT_TEXT = """(()=>{
  const beat=document.querySelector('[data-testid="run-terminal-beat"]');
  if(beat) return ('RUN TERMINAL BEAT: '+(beat.innerText||'')).slice(0,400);
  const body=document.body?document.body.innerText:'';
  return /didn't return a result|Run interrupted|Run timed out/i
    .test(body) ? body.slice(0,400) : '';
})()"""


BO_TOOL_CARDS = """(()=>JSON.stringify(
  [...document.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status]')]
    .map((n)=>({
      testId:n.getAttribute('data-testid'),
      status:n.getAttribute('data-tool-status'),
      text:(n.innerText||'').slice(0,240),
    }))
))()"""


BO_LAST_ASSISTANT = """(()=>{
  const nodes=[...document.querySelectorAll(
    '[data-testid^=tc-chat-message-][data-role=assistant]')];
  const last=nodes[nodes.length-1];
  return last?(last.innerText||'').slice(0,600):'';
})()"""


def bo_tool_cards(s: DriverSession) -> list[dict]:
    raw = s.evaluate(BO_TOOL_CARDS)
    return json.loads(raw) if raw else []


def bo_wait_for_answer(s: DriverSession, before: int, timeout_s: int = 300) -> None:
    """Wait for a new assistant turn, failing fast on the interrupt banner."""

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        banner = s.evaluate(BO_INTERRUPT_TEXT) or ""
        if banner:
            raise AssertionError(
                f"run was interrupted instead of finalizing:\n{banner}"
            )
        if int(s.evaluate(BO_ASSISTANT_COUNT) or 0) > before:
            return
        time.sleep(0.5)
    raise AssertionError(f"no assistant answer within {timeout_s}s")


BO_SEND_BUTTON = 'button[aria-label="Send message"]'


def bo_send(s: DriverSession, text: str, first: bool) -> None:
    if first:
        s.send_first_run_message(text)
        assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
        return
    assert s.wait_for("[data-testid=composer-textarea]", 30), "composer never appeared"
    s.fill("[data-testid=composer-textarea]", text)
    # The composer disables Send while the prior run is still settling; clicking
    # a disabled control is a harness race, not a product failure.
    assert s.wait_for(f"{BO_SEND_BUTTON}:not([disabled])", 60), (
        "bo_send button never became enabled"
    )
    s.click(BO_SEND_BUTTON)


def bo_run_prompt(s: DriverSession, label: str, prompt: str, first: bool) -> None:
    log(f"── {label} ─────────────────────────────────────────")
    before = int(s.evaluate(BO_ASSISTANT_COUNT) or 0)
    seen_before = {card["testId"] for card in bo_tool_cards(s)}
    bo_send(s, prompt, first)
    bo_wait_for_answer(s, before)

    # Let any trailing card settle before reading terminal state.
    time.sleep(3)
    cards = bo_tool_cards(s)
    new_cards = [c for c in cards if c["testId"] not in seen_before]
    errored = [c for c in new_cards if c["status"] == "error"]
    searches = [c for c in new_cards if "web_search" in c["text"]]

    answer = s.evaluate(BO_LAST_ASSISTANT) or ""
    s.shot(f"budget-{label}")

    log(f"      tool cards: {len(new_cards)} (web_search: {len(searches)})")
    assert not errored, f"errored tool cards after budget overrun: {errored!r}"
    assert answer.strip(), "assistant turn appeared but carried no answer text"
    banner = s.evaluate(BO_INTERRUPT_TEXT) or ""
    assert not banner, f"interrupt banner present after the answer:\n{banner}"
    log(f"PASS  {label}: finalized with an answer; no errored cards, no interrupt")


LIMIT = 2


TB_PROMPT = (
    "Research each of these with its own separate web search, then summarize: "
    "(1) the latest Python release, (2) the latest Node.js LTS, (3) the "
    "latest Postgres major version, (4) the latest Rust release. Use a "
    "separate search for each one."
)


TB_SEND_BUTTON = 'button[aria-label="Send message"]'


CAP_INPUT = "[data-testid=tool-calls-per-run-input]"


SETTINGS_BUTTON = '[aria-label="Settings"]'


NAV_MODEL_BEHAVIOR = '[data-slug="model-behavior"]'


NAV_PROVIDER_KEYS = '[data-slug="provider-keys"]'


TB_ASSISTANT_COUNT = (
    'document.querySelectorAll("[data-testid^=tc-chat-message-]'
    '[data-role=assistant]").length'
)


TB_INTERRUPT_TEXT = """(()=>{
  const beat=document.querySelector('[data-testid="run-terminal-beat"]');
  if(beat) return ('RUN TERMINAL BEAT: '+(beat.innerText||'')).slice(0,400);
  const body=document.body?document.body.innerText:'';
  return /didn't return a result|Run interrupted|Run timed out/i
    .test(body) ? body.slice(0,400) : '';
})()"""


TB_TOOL_CARDS = """(()=>JSON.stringify(
  [...document.querySelectorAll('[data-testid^="tc-chat-tool-"][data-tool-status]')]
    .map((n)=>({
      testId:n.getAttribute('data-testid'),
      status:n.getAttribute('data-tool-status'),
      text:(n.innerText||'').slice(0,240),
    }))
))()"""


def tb_open_model_behavior(s: DriverSession) -> None:
    """Open Settings from the app rail and select Model & behavior."""

    s.click(SETTINGS_BUTTON)
    assert s.wait_for("[data-testid=settings-surface]", 20), "Settings never opened"
    s.click(NAV_MODEL_BEHAVIOR)
    assert s.wait_for(CAP_INPUT, 20), "Tool-calls field never rendered"


def tb_set_limit(s: DriverSession, limit: int) -> None:
    """Open Settings → Model & behavior, set the cap, Save, and verify."""

    tb_open_model_behavior(s)

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


def tb_assert_persisted(s: DriverSession, limit: int) -> None:
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


def tb_run_prompt_and_check(s: DriverSession, limit: int) -> None:
    """Send a search-hungry prompt; require finalize AND an enforced cap."""

    s.rpc("press", key="Escape")
    time.sleep(1)
    s.open_destination("Run")
    before = int(s.evaluate(TB_ASSISTANT_COUNT) or 0)

    assert s.wait_for("[data-testid=composer-textarea]", 30), "composer never appeared"
    s.fill("[data-testid=composer-textarea]", TB_PROMPT)
    assert s.wait_for(f"{TB_SEND_BUTTON}:not([disabled])", 60), "send never enabled"
    s.click(TB_SEND_BUTTON)

    deadline = time.time() + 300
    while time.time() < deadline:
        banner = s.evaluate(TB_INTERRUPT_TEXT) or ""
        assert not banner, f"run was interrupted instead of finalizing:\n{banner}"
        if int(s.evaluate(TB_ASSISTANT_COUNT) or 0) > before:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("no assistant answer within 300s")

    time.sleep(3)
    cards = json.loads(s.evaluate(TB_TOOL_CARDS) or "[]")
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


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in_and_key(s: DriverSession) -> None:
    provider, key = byok_provider()
    STATE["provider"] = provider
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    s.sign_in_local()
    s.ftue_add_key(provider, key)


# ── the Tools pill: one entry point, every placement ─────────────────────────
def cb1_tools_pill_on_the_first_run_composer(s: DriverSession) -> None:
    """The shared Tools pill, before a conversation exists.

    The rich per-run Tools controls have ONE entry point: the composer Tools
    pill. The attachment `+` menu deliberately contains no Tools entry. No
    selector assertion is accepted until the row is the actual hit target at
    its rendered coordinates.
    """

    exercise_tools(
        s,
        "first-run",
        # The hero heading lies behind the upward-opening panel at the current
        # desktop viewport; use the inert top brand to exercise a genuine,
        # visible click-out target instead.
        outside_selector="[data-testid=first-run-brand]",
    )


def cb2_tools_pill_on_a_bound_run(s: DriverSession) -> None:
    """The bound Run composer, in Studio and Focus.

    The Studio path additionally proves keyboard Enter/Space, Escape, and
    click-out — the accessibility contract, not just the visual state.
    """

    s.send_first_run_message("Reply with exactly: ready")
    wait_for_bound_run(s)
    STATE["bound"] = True
    set_mode(s, "studio")
    exercise_tools(s, "bound-studio", keyboard=True)
    set_mode(s, "focus")
    exercise_tools(s, "bound-focus")


def cb3_tools_pill_on_a_new_chat(s: DriverSession) -> None:
    """The empty Run composer reached through New chat, in both modes."""

    s.open_destination("Chats")
    assert s.wait_for("[data-testid=chats-new-chat]", timeout_s=30), (
        "Chats did not expose New chat"
    )
    s.click("[data-testid=chats-new-chat]")
    assert s.wait_for("[data-testid=run-empty-composer]", timeout_s=30), (
        "New chat did not open the empty Run composer"
    )
    set_mode(s, "studio")
    exercise_tools(s, "new-chat-studio")
    set_mode(s, "focus")
    exercise_tools(s, "new-chat-focus")


# ── the agent's todo checklist ───────────────────────────────────────────────
def cb4_todo_checklist_renders_and_advances(s: DriverSession) -> None:
    """T1-T5: a pinned checklist that advances, with no raw card and no Plan.

    The value of running this against the packaged app is the class of failure
    it catches: every layer between the worker and the pixel can drop the new
    event SILENTLY — most sharply the client's `isRuntimeEventEnvelope` guard,
    which discards an envelope whose `event_type` it does not know.
    """

    s.send(TODO_PROMPT)
    assert s.wait_for("[data-testid=tc-chat]", 60), (
        "the run never opened the transcript"
    )

    # T1 — the checklist renders, pinned above the composer.
    first = wait_for_panel(s)
    s.shot("t1-todo-panel-visible")
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

    # T2 — rows advance from spinner to tick.
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
    assert seen["spinner_seen"] or "in_progress" in seen["statuses"], (
        "no row was ever in_progress — the panel never showed live work"
    )
    assert seen["max_completed"] > 0, (
        "no row ever reached completed — the tick transition never happened; "
        f"final panel was {seen['last']!r}"
    )

    # T3 — the raw write_todos card never appeared.
    assert not write_todos_cards(s), "a raw write_todos tool card is on screen"

    # T4 — the invented Plan is gone, in both modes.
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
    assert focus_view and (focus_view["rows"] or focus_view["summary"]), (
        "the checklist did not survive the switch to Focus"
    )

    # T5 — the backend really emitted the snapshots.
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
    log(f"{len(snapshots)} todo_list_updated event(s), first carried {len(rows)} rows")


# ── how a run reports its own health ─────────────────────────────────────────
def cb5_declined_capability_is_not_a_failure(s: DriverSession) -> None:
    """Asking for a folder that was never attached must not read as a failed run.

    The reported failure: the workspace backend correctly answered "Local
    workspace access is unavailable", the agent adapted and explained it in
    chat, and the user nevertheless saw the Studio canvas shouting "RUN
    INTERRUPTED / This run needs attention" beside a complete, correct answer,
    under a "Retry run" button wired to an SSE reconnect that could not retry
    anything.
    """

    before = int(s.evaluate(DC_ASSISTANT_COUNT) or 0)
    s.send(DC_PROMPT)
    assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"
    dc_wait_for_answer(s, before)
    time.sleep(4)  # let the terminal frames land before reading end-state
    s.shot("declined-capability")

    answer = s.evaluate(DC_LAST_ASSISTANT) or ""
    cards = json.loads(s.evaluate(DC_TOOL_CARDS) or "[]")
    canvas = s.evaluate(DC_CANVAS_STATE) or ""
    alarm = s.evaluate(DC_CANVAS_ALARM) or ""
    actions = json.loads(s.evaluate(DC_RUN_LEVEL_ACTION) or "{}")

    # The run answered the user. Everything below is about HOW that was
    # reported, so a missing answer means this never reached the state under test.
    assert answer.strip(), "assistant turn appeared but carried no answer text"

    # Fault 1 — a declined capability is not a failure.
    errored = [c for c in cards if c["status"] == "error"]
    assert not errored, (
        f"a declined capability was still rendered as a failed step: {errored!r}"
    )
    # Fault 2 — the canvas reports on the canvas, not on the run.
    assert not alarm, f"the canvas rendered a verdict on the run:\n{alarm}"
    if canvas:
        state = json.loads(canvas)["lifecycle"]
        assert state in {"chat_only", "complete_empty", "presenting"}, (
            f"unexpected canvas lifecycle {state!r} for an answered run"
        )
    # Fault 3 — no action the system cannot perform.
    assert not actions.get("buttons"), (
        f"a run-level action was offered on a run that answered: {actions!r}"
    )
    assert not actions.get("terminalBeat"), (
        "a terminal verdict beat rendered for a run that completed"
    )


def cb6_overrunning_the_tool_budget_still_finalizes(s: DriverSession) -> None:
    """A research turn that exceeds the per-run tool allowance must FINALIZE.

    The reported failure: any run making more than the allowance died with "RUN
    INTERRUPTED … The tool reported an error and didn't return a result",
    losing every result it had already gathered. The cause was a hard-cap
    rejection raised as a run-fatal exception that escaped through
    `astream_runtime`, even though its own message told the model to "finalize".

    Runs before CB-7 on purpose: this is about the DEFAULT budget.
    """

    for label, prompt in PROMPTS:
        # `first=False` always — CB-2 already spent the first-run composer.
        bo_run_prompt(s, label, prompt, first=False)


def cb7_settings_tool_limit_governs_the_runtime(s: DriverSession) -> None:
    """LAST, because it writes a persisted setting.

    Settings that look right but do not reach the runtime are the failure mode
    worth testing: set the cap, reload the section and require it to have
    persisted, then send a prompt that wants more searches than the cap allows
    and require the run to finalize AND to have executed no more than the cap.
    A limit the UI stores but the runtime ignores passes the first two steps and
    is still broken.
    """

    assert s.wait_for(SETTINGS_BUTTON, 60), "app rail never mounted"
    tb_set_limit(s, LIMIT)
    tb_assert_persisted(s, LIMIT)
    tb_run_prompt_and_check(s, LIMIT)


def main() -> int:
    plan = JourneyPlan("composer-and-budgets")
    plan.boot(
        "source · fresh",
        lambda: DriverSession(name="composer-and-budgets"),
        setup=sign_in_and_key,
        phases=[
            (
                "CB-1",
                "the Tools pill works on the first-run composer",
                cb1_tools_pill_on_the_first_run_composer,
            ),
            (
                "CB-2",
                "the Tools pill works on a bound run, Studio and Focus",
                cb2_tools_pill_on_a_bound_run,
            ),
            (
                "CB-3",
                "the Tools pill works on a New chat, Studio and Focus",
                cb3_tools_pill_on_a_new_chat,
            ),
            (
                "CB-4",
                "the todo checklist renders, advances, and came from the server",
                cb4_todo_checklist_renders_and_advances,
            ),
            (
                "CB-5",
                "a declined capability stays out of the failure taxonomy",
                cb5_declined_capability_is_not_a_failure,
            ),
            (
                "CB-6",
                "overrunning the default tool budget still finalizes",
                cb6_overrunning_the_tool_budget_still_finalizes,
            ),
            (
                "CB-7",
                "the Settings tool-call limit persists and governs the runtime",
                cb7_settings_tool_limit_governs_the_runtime,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
