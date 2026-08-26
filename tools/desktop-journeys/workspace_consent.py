#!/usr/bin/env python3
"""workspace-consent — what the agent may read, and how it asks.

The DEFAULT lane, which is what a real desktop user gets: `service-env.ts`
defaults OPERATION_GATEWAY_MODE to "off" and lets WORKSPACE_EFFECT_MODE follow
it, so setting NOTHING is the shipped posture. The enforce-lane and bypass
claims need a different supervisor environment and live in
`workspace_bypass.py`.

The phases are ordered by the state they consume. WC-1 and WC-2 need the VIRGIN
first-run composer (the folder bar only exists before the first message, and
WC-1 must read it EMPTY before WC-2 fills it). WC-3 spends the first message;
everything after it reads the run composer.

    python3 tools/desktop-journeys/workspace_consent.py

Folds in: filesystem-access/{ungranted_path_asks, downloads_asks_approval,
jA_ungranted_asks_and_approve, jC_composer_and_scratch, attach_folder_row,
jG_folder_bar_populated, jE_mcp_state}, agent-todos/todos_with_gate.

The live defect behind most of this: the agent CLAIMED host paths and agent
memory ANSWERED them, so `ls ~/Downloads` returned an empty listing as a
SUCCESS. The whole apparatus — classifier, router, broker, consent card — was
built and left unwired, and 9151 unit tests were green over it.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from _lib import (
    DriverSession,
    JourneyPlan,
    PhaseSkipped,
    byok_provider,
    preflight_staged_runtime,
    require,
)
from _workspace_lib import (
    DEFAULT_LANE,
    approval_events,
    assistant_text,
    dump,
    events,
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


PLUS = 'button[aria-label="Open attachment and tools menu"]'


FOLDER_BAR = ".aui-folder-bar"


FOLDER_BAR_ATTACH = ".aui-folder-bar__attach"


CONTROLS = {
    "plus": PLUS,
    "tools": "[data-testid=first-run-tools-button]",
    "bypass": ".atlas-bypass-pill",
    "model": ".atlas-model-pill",
    "mic": ".atlas-composer-mic",
    "send": 'button[aria-label="Send message"]',
}


MENU_SELECTOR = '.ui-pop__row, [role="menuitem"], button'


def jc_rects(session: DriverSession) -> dict[str, Any]:
    """Where each control actually is on screen — left edge + size."""

    js = (
        "(() => { const sel = " + json.dumps(CONTROLS) + ";"
        " const out = {};"
        " for (const [name, q] of Object.entries(sel)) {"
        "   const el = document.querySelector(q);"
        "   if (!el) { out[name] = null; continue; }"
        "   const r = el.getBoundingClientRect();"
        "   out[name] = { left: Math.round(r.left), width: Math.round(r.width),"
        "     visible: r.width > 0 && r.height > 0,"
        "     text: (el.innerText || '').trim().slice(0, 40),"
        "     disabled: el.hasAttribute('disabled') };"
        " } return out; })()"
    )
    return session.evaluate(js) or {}


def jc_menu_rows(session: DriverSession) -> list[str]:
    js = (
        "(() => Array.from(document.querySelectorAll('"
        + MENU_SELECTOR
        + "')).map((el) => (el.textContent || '').trim()).filter(Boolean)"
        ".map((t) => t.slice(0, 60)))()"
    )
    return session.evaluate(js) or []


def jc_attach_folder_row(session: DriverSession) -> Any:
    js = (
        "(() => { const hit = Array.from(document.querySelectorAll('"
        + MENU_SELECTOR
        + "')).find((el) => /attach folder/i.test(el.textContent || ''));"
        " if (!hit) return null; const r = hit.getBoundingClientRect();"
        " return { text: (hit.textContent || '').trim().slice(0, 120),"
        " visible: r.width > 0 && r.height > 0 }; })()"
    )
    return session.evaluate(js)


def jc_folder_bar(session: DriverSession) -> Any:
    js = (
        "(() => { const bar = document.querySelector('"
        + FOLDER_BAR
        + "'); if (!bar) return null; const r = bar.getBoundingClientRect();"
        " const attach = document.querySelector('" + FOLDER_BAR_ATTACH + "');"
        " return { text: (bar.innerText || '').trim().slice(0, 160),"
        " attach_text: attach ? (attach.innerText || '').trim().slice(0, 120) : null,"
        " visible: r.width > 0 && r.height > 0, top: Math.round(r.top) }; })()"
    )
    return session.evaluate(js)


def jc_order(rects: dict[str, Any]) -> list[str]:
    present = [
        (name, value["left"])
        for name, value in rects.items()
        if isinstance(value, dict) and value.get("visible")
    ]
    return [name for name, _ in sorted(present, key=lambda pair: pair[1])]


def jc_scratch_probe(copilot_home: Path, conversation_id: str) -> dict[str, Any]:
    root = copilot_home / ".tmp"
    conversation = root / conversation_id
    probe: dict[str, Any] = {
        "scratch_root": str(root),
        "root_exists": root.is_dir(),
        "conversation_dir": str(conversation),
        "conversation_exists": conversation.is_dir(),
    }
    if root.is_dir():
        probe["root_children"] = sorted(p.name for p in root.iterdir())[:20]
    if not conversation.is_dir():
        return probe
    listing: list[str] = []
    for path in sorted(conversation.rglob("*")):
        suffix = "/" if path.is_dir() else f" ({path.stat().st_size}B)"
        listing.append(str(path.relative_to(conversation)) + suffix)
    probe["tree"] = listing[:200]
    # The claim is that tool results and subagent transcripts are INSPECTABLE
    # FILES, so count files rather than directories: an empty `tool-results/`
    # proves the provisioner ran and nothing else.
    probe["files"] = [
        str(path.relative_to(conversation))
        for path in sorted(conversation.rglob("*"))
        if path.is_file()
    ][:100]
    probe["tool_result_files"] = [
        name for name in probe["files"] if "/tool-results/" in f"/{name}"
    ]
    probe["subagent_files"] = [
        name for name in probe["files"] if "/subagents/" in f"/{name}"
    ]
    meta = conversation / "meta.json"
    probe["meta_exists"] = meta.is_file()
    if meta.is_file():
        try:
            probe["meta"] = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            probe["meta_error"] = repr(exc)
    return probe


ROW_TITLES_JS: Final = (
    "(() => Array.from(document.querySelectorAll('"
    + MENU_SELECTOR
    + "')).map((el) => (el.textContent || '').trim()).filter(Boolean)"
    ".map((t) => t.slice(0, 80)))()"
)


ROW_PROBE_JS: Final = (
    "(() => { const hit = Array.from(document.querySelectorAll('"
    + MENU_SELECTOR
    + "')).find((el) => /attach folder/i.test(el.textContent || ''));"
    " if (!hit) return null; const r = hit.getBoundingClientRect();"
    " return { text: (hit.textContent || '').trim().slice(0, 160),"
    " visible: r.width > 0 && r.height > 0,"
    " enabled: !hit.hasAttribute('disabled') }; })()"
)


BAR_PROBE_JS: Final = (
    "(() => { const hit = document.querySelector('.aui-folder-bar__attach');"
    " if (!hit) return null; const r = hit.getBoundingClientRect();"
    " return { text: (hit.textContent || '').trim().slice(0, 160),"
    " visible: r.width > 0 && r.height > 0,"
    " enabled: !hit.hasAttribute('disabled') }; })()"
)


def afr_probe_row(session: DriverSession) -> Any:
    """Is an 'Attach Folder' row reachable in whatever menu is open now?"""

    return session.evaluate(ROW_PROBE_JS)


def afr_probe_bar(session: DriverSession) -> Any:
    """Is the composer's folder bar on screen right now?"""

    return session.evaluate(BAR_PROBE_JS)


ATTACH = ".aui-folder-bar__attach"


NAME = ".aui-folder-bar__name"


MORE = ".aui-folder-bar__more"


REVOKE = ".aui-folder-bar__revoke"


COMPOSER = ".aui-composer"


FIXTURE_NAMES = ("kaleidoscope", "harbour-notes", "q3-forecast")


def jg_make_fixtures(root: Path) -> list[Path]:
    made: list[Path] = []
    for name in FIXTURE_NAMES:
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "README.md").write_text(
            f"# {name}\n\nFixture for the FS-G folder-bar journey.\n",
            encoding="utf-8",
        )
        made.append(folder)
    return made


def jg_stub_picker(session: DriverSession, folder: Path) -> None:
    """Point the NEXT `showOpenDialog` at ``folder``.

    Patches the live `electron.dialog` singleton, which is the same object
    `main/index.ts` closes over — it calls `dialog.showOpenDialog(...)` at
    request time, not a reference captured at import.
    """

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


def jg_bar(session: DriverSession) -> dict[str, Any]:
    """What the bar currently says, plus its geometry against the frame."""

    js = """
    (() => {
      const bar = document.querySelector('.aui-folder-bar');
      if (!bar) return { present: false };
      const name = bar.querySelector('.aui-folder-bar__name');
      const more = bar.querySelector('.aui-folder-bar__more');
      const revoke = bar.querySelector('.aui-folder-bar__revoke');
      const frame = document.querySelector('.aui-composer');
      const b = bar.getBoundingClientRect();
      const f = frame ? frame.getBoundingClientRect() : null;
      return {
        present: true,
        name: name ? name.textContent : null,
        more: more ? more.textContent : null,
        revoke_label: revoke ? revoke.getAttribute('aria-label') : null,
        attach_label: (bar.querySelector('.aui-folder-bar__attach') || {}).textContent,
        gap_px: f ? Math.round(f.top - b.bottom) : null,
        stack_gap: (() => {
          const s = document.querySelector('.aui-composer-stack');
          return s ? getComputedStyle(s).gap : null;
        })(),
        frame_margin_top: f ? getComputedStyle(frame).marginTop : null,
      };
    })()
    """
    return session.evaluate(js) or {}


def jg_dom_leaks_a_path(session: DriverSession, folders: list[Path]) -> dict[str, Any]:
    """Does any host path reach the rendered DOM?

    Checks the whole document, not just the bar: a path could leak through a
    title attribute, an aria-label, or a data-* value just as easily as through
    visible text.
    """

    js = """
    (needles) => {
      const html = document.documentElement.outerHTML;
      const hits = needles.filter((n) => html.includes(n));
      return { checked: needles.length, hits };
    }
    """
    parents = sorted({str(f.parent) for f in folders})
    needles = [str(f) for f in folders] + parents
    return session.evaluate(f"({js})({json.dumps(needles)})") or {}


FILESYSTEM_FLAG: Final = "RUNTIME_ENABLE_DESKTOP_FILESYSTEM"


@contextmanager
def fixture_directory() -> Iterator[tuple[Path, str, str]]:
    """A real on-disk directory the app was never granted."""

    nonce = uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="fs1-ungranted-") as raw:
        root = Path(raw).resolve()
        canary_name = f"canary-{nonce}.txt"
        canary_body = f"fs1-canary-{nonce}"
        (root / canary_name).write_text(canary_body, encoding="utf-8")
        yield root, canary_name, canary_body


def downloads_entry_count() -> int | None:
    """How many entries Downloads really has — a number, never a name."""

    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return None
    try:
        return len(list(downloads.iterdir()))
    except OSError:
        return None


def fs1_final_text(session: DriverSession, run_id: str) -> str:
    """The assistant's visible answer, lowercased for phrase matching."""

    chunks: list[str] = []
    for event in events(session, run_id):
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for key in ("text", "content", "message", "final_response", "delta"):
            value = payload.get(key)
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(chunks).lower()


def fs1_tool_and_approval_shapes(events: list[dict]) -> dict[str, Any]:
    """Everything needed to tell a refusal from a silent empty success."""

    tool_events: list[dict] = []
    approvals: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if "approval" in name:
            approvals.append(
                {
                    "event": name,
                    "kind": payload.get("approval_kind"),
                    "has_workspace_grant": "workspace_grant" in payload,
                }
            )
        if "tool" in name:
            tool_events.append(
                {
                    "event": name,
                    "tool": payload.get("tool_name") or payload.get("name"),
                    "status": payload.get("status"),
                    "summary": str(payload.get("summary") or "")[:400],
                }
            )
    return {"tools": tool_events, "approvals": approvals}


DOWNLOADS: Final = Path.home() / "Downloads"


APPROVAL_JS: Final = """(() => {
  const hit = Array.from(document.querySelectorAll('*')).find((el) =>
    /grant|allow access|approve|permission|share this folder/i.test(
      (el.textContent || '')) && el.children.length < 12);
  if (!hit) return null;
  const r = hit.getBoundingClientRect();
  return { text: (hit.textContent || '').trim().slice(0, 240),
           visible: r.width > 0 && r.height > 0 };
})()"""


CARD_SELECTOR = "[data-testid^=tc-chat-approval-]"


APPROVE_SELECTOR = "[data-testid^=tc-chat-approval-approve-]"


CARD_TEXT_JS = (
    "(() => Array.from(document.querySelectorAll('"
    + CARD_SELECTOR
    + "')).map((el) => ({ testid: el.getAttribute('data-testid'),"
    " text: (el.innerText || '').trim().slice(0, 600) })))()"
)


TOOLS_RAIL = '[data-destination="connectors"][aria-label]'


RUN_RAIL = '[data-destination="run"][aria-label]'


SKIP_FIRST_RUN = "[data-testid=first-run-skip]"


def je_safe(session: DriverSession, path: str) -> Any:
    try:
        return transport_json(session, "GET", path)
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)[:300]}


APPROVAL_CARD = '[data-testid^="tc-chat-approval-item-"]'


WAITING_LINE = "[data-testid=tc-chat-approvals-waiting]"


def gate_observe(s: DriverSession) -> dict:
    """Read the layout and the checklist exactly as the user sees them.

    ``approvalsOutsideTranscript`` replaces the two dead strip probes
    (``tc-chat-approvals`` / ``tc-chat-conf-cards``). Nothing in the product
    emits either id any more, so asserting their absence asserted nothing at
    all. The invariant they stood for is still real, so it is checked directly:
    NO approval node may render outside the transcript. A pinned strip that grew
    back — under ANY name — puts one here.

    The prefix is deliberately ``tc-chat-approval-`` with the trailing hyphen.
    It matches the stream ``<li>`` (``tc-chat-approval-item-<id>``), the ask
    wrapper (``tc-chat-approval-<id>``) and its three decision controls
    (``-approve-`` / ``-reject-`` / ``-body-approve-<id>``), and it does NOT
    match the reachability line ``tc-chat-approvals-waiting``, which lives above
    the composer on purpose and is asserted present separately.
    """
    js = """(()=>{
      const chat=document.querySelector('[data-testid=tc-chat]');
      if(!chat) return "null";
      const messages=document.querySelector('[data-testid=tc-chat-messages]');
      const card=document.querySelector('[data-testid^="tc-chat-approval-item-"]');
      const root=document.querySelector('[data-testid=tc-todo-list]');
      const rows=root?[...root.querySelectorAll('[data-testid=tc-todo-row]')].map((r)=>({
        status:r.getAttribute('data-status'),
        waiting:r.getAttribute('data-waiting'),
        text:r.innerText,
        spinner:!!r.querySelector('[data-testid=tc-todo-spinner]'),
        stillGlyph:!!r.querySelector('[data-testid=tc-todo-waiting]'),
      })):[];
      return JSON.stringify({
        order:[...chat.children].map((n)=>n.getAttribute('data-testid')).filter(Boolean),
        cardPresent:!!card,
        cardInTranscript:!!(card&&messages&&messages.contains(card)),
        cardPending:card&&card.getAttribute('data-approval-pending'),
        approvalsOutsideTranscript:[...document.querySelectorAll('[data-testid^="tc-chat-approval-"]')]
          .filter((n)=>!(messages&&messages.contains(n)))
          .map((n)=>n.getAttribute('data-testid')),
        waitingLine:(document.querySelector('[data-testid=tc-chat-approvals-waiting]')||{}).innerText||null,
        todoBlocked:root&&root.getAttribute('data-blocked'),
        todoRows:rows,
        toolCards:[...document.querySelectorAll('.tc-activity-card[data-tool-status]')].map((n)=>({
          status:n.getAttribute('data-tool-status'),
          waiting:n.getAttribute('data-tool-waiting'),
          spinner:!!n.querySelector('.tc-tool-card__spinner'),
          stillGlyph:!!n.querySelector('[data-testid=tc-tool-card-waiting]'),
          text:n.innerText,
        })),
      });
    })()"""
    raw = s.evaluate(js)
    return json.loads(raw) if raw and raw != "null" else {}


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in_and_key(s: DriverSession) -> None:
    preflight_staged_runtime()
    provider, key = byok_provider()
    STATE["provider"] = provider
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    STATE["target"] = s.rpc("status").get("target")
    s.sign_in_local()
    s.ftue_add_key(provider, key)
    assert s.wait_for(PLUS, timeout_s=60), "composer never appeared"
    s.shot("ready")


def _fail(failures: list[str]) -> None:
    if failures:
        raise AssertionError("; ".join(failures))


# ── the composer, before anything has been sent ──────────────────────────────
def wc1_folder_affordance_is_the_bar_not_a_menu_row(s: DriverSession) -> None:
    """The folder affordance is the BAR on the composer, not a `+` menu row.

    THIS ASSERTION IS INVERTED FROM WHAT IT USED TO BE, deliberately. It once
    demanded that an "Attach Folder" row EXIST in the `+` menu. PRD-FS-10
    deletes that row: a folder grant copies nothing into the message and
    OUTLIVES it (it persists until revoked), so housing it beside "Attach
    Image" mis-describes what it does.

    Also reads the bar EMPTY, which is the before half of WC-2's pair.
    """

    failures: list[str] = []
    ftue_bar = afr_probe_bar(s)
    STATE["ftue_folder_bar"] = ftue_bar
    STATE["empty_bar"] = jg_bar(s)
    s.shot("ftue-folder-bar")

    s.click(PLUS)
    time.sleep(0.4)
    s.shot("ftue-plus-menu-open")
    rows = s.evaluate(ROW_TITLES_JS)
    if afr_probe_row(s):
        failures.append(
            "the `+` menu still offers Attach Folder — PRD-FS-10 deletes that row"
        )
    if not ftue_bar or not ftue_bar.get("visible"):
        failures.append(
            "no folder bar on the FTUE composer — the mount is missing its "
            "workspaceGrantPort, the exact gap PRD-FS-10 §7 closes"
        )
    elif "/" in str(ftue_bar.get("text", "")):
        failures.append(f"the folder bar printed a path: {ftue_bar.get('text')!r}")
    s.press("body", "Escape")
    time.sleep(0.3)
    log(f"{len(rows or [])} rows in the `+` menu, none of them Attach Folder")
    _fail(failures)


def wc2_populated_folder_bar(s: DriverSession) -> None:
    """The bar with folders actually ATTACHED — the half only unit tests had seen.

    Every screenshot this program produced showed the EMPTY state. The named
    chip, the `+N` collapse, most-recently-granted-first ordering, and revoke
    had never run against the real app. Needs macOS native automation to drive
    the OS folder picker, which is stubbed here rather than driven.
    """

    fixture_root = Path.home() / ".0xcopilot-journey-fixtures" / "fs-g"
    folders = jg_make_fixtures(fixture_root)
    STATE["fs_g_folders"] = folders
    failures: list[str] = []

    require(s.wait_for(FOLDER_BAR, timeout_s=60), "no folder bar on this composer")

    jg_stub_picker(s, folders[0])
    s.click(ATTACH)
    require(s.wait_for(NAME, timeout_s=30), "the folder picker could not be stubbed")
    for _ in range(40):
        if (jg_bar(s).get("name") or "") == folders[0].name:
            break
        time.sleep(0.25)
    one = jg_bar(s)
    s.shot("bar-one-folder")

    jg_stub_picker(s, folders[1])
    s.click(ATTACH)
    time.sleep(1.5)
    s.shot("bar-two-folders")

    jg_stub_picker(s, folders[2])
    s.click(ATTACH)
    time.sleep(1.5)
    three = jg_bar(s)
    s.shot("bar-three-folders")

    leak = jg_dom_leaks_a_path(s, folders)
    if session_revoke := s.present(REVOKE):
        s.click(REVOKE)
        time.sleep(1.5)
        s.shot("bar-after-revoke")

    if (one.get("name") or "") != folders[0].name:
        failures.append(f"one grant: chip read {one.get('name')!r}")
    if three.get("more") != "+2":
        failures.append(f"three grants: collapse read {three.get('more')!r}")
    if (three.get("name") or "") != folders[2].name:
        failures.append(
            f"newest-first: chip read {three.get('name')!r}, expected {folders[2].name!r}"
        )
    if one.get("gap_px") != 6:
        failures.append(f"populated bar-to-frame gap {one.get('gap_px')}px, expected 6")
    if leak.get("hits"):
        failures.append(f"host path in the DOM: {leak['hits']}")
    if not session_revoke:
        failures.append("no revoke control on a populated bar")
    _fail(failures)


# ── the ungranted ask ────────────────────────────────────────────────────────
def wc3_an_ungranted_path_never_empty_succeeds(s: DriverSession) -> None:
    """A real directory holding a known file must never come back as empty.

    SPENDS THE FIRST MESSAGE. A run that correctly stops to ASK never becomes
    terminal, so parked IS the pass here — waiting for a terminal run would
    fail on the very outcome this exists to prove.
    """

    evidence: dict[str, Any] = {"downloads_entries_on_disk": downloads_entry_count()}
    with fixture_directory() as (root, canary, _body):
        evidence["fixture_root"] = str(root)
        evidence["canary_name"] = canary
        STATE["fs1_root"] = str(root)

        s.send_first_run_message(
            f"List the files in the directory {root}. Report exactly what you "
            "find. If you cannot read it, say so plainly and do not guess."
        )
        s.shot("fs1-prompt-sent")
        conversation_id = wait_for_conversation_id(s)
        run_id = wait_for_new_run(s, conversation_id, 0)
        STATE["conversation_id"] = conversation_id
        evidence["run_id"] = run_id
        try:
            settle_run(s, run_id)
        except AssertionError as exc:
            evidence["parked"] = "waiting_for_approval" in str(exc)
        time.sleep(1.5)
        s.shot("fs1-outcome")

        stream = events(s, run_id)
        evidence["event_count"] = len(stream)
        evidence.update(fs1_tool_and_approval_shapes(stream))
        answer = (assistant_text(s, run_id) or "").lower()
        evidence["mentions_canary"] = canary.lower() in answer
        evidence["claims_empty"] = any(
            phrase in answer
            for phrase in (
                "is empty",
                "no files",
                "empty directory",
                "directory is empty",
                "folder is empty",
                "contains no files",
            )
        )
        evidence["answer_tail"] = answer[-1200:]
    dump(s.run_dir, "wc3-evidence.json", evidence)

    assert not (evidence["claims_empty"] and not evidence["mentions_canary"]), (
        "an ungranted path still empty-succeeds: the model reported an empty "
        f"listing for a directory that holds {evidence['canary_name']!r}"
    )
    log(
        f"claims_empty={evidence['claims_empty']} "
        f"mentions_canary={evidence['mentions_canary']} "
        f"approvals={len(evidence.get('approvals') or [])}"
    )


def wc4_the_card_names_the_folder_and_approve_returns_it(s: DriverSession) -> None:
    """The half a user actually experiences: the card NAMES the folder, and
    clicking Approve comes back with the REAL listing.

    WC-3 stops one step short on purpose (a parked run is its pass). This goes
    all the way through the user's own Approve click.
    """

    nonce = uuid.uuid4().hex[:12]
    evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="wc4-") as raw:
        root = Path(raw).resolve()
        canary = f"canary-{nonce}.txt"
        (root / canary).write_text(f"wc4-{nonce}", encoding="utf-8")
        evidence["fixture_root"] = str(root)

        # WC-3 already bound the conversation, so a new run is the NEXT one:
        # count what exists before sending rather than assuming zero.
        conversation_id = STATE.get("conversation_id") or wait_for_conversation_id(s)
        before_runs = len(runs_for(s, conversation_id))
        s.send(
            f"List the files in the directory {root}. Report exactly what you "
            "find. If you cannot read it, say so plainly and do not guess."
        )
        run_id = wait_for_new_run(s, conversation_id, before_runs)
        evidence["run_id"] = run_id

        # Catch the card while it is on screen. Poll fast: a card resolved by
        # something other than the user can be brief.
        cards: list[dict[str, Any]] = []
        deadline = time.time() + 150
        while time.time() < deadline:
            found = s.evaluate(CARD_TEXT_JS)
            if isinstance(found, list) and found:
                cards = found
                break
            time.sleep(0.2)
        evidence["cards_seen"] = cards
        clicked = False
        if cards:
            s.shot("wc4-approval-card")
            evidence["card_names_folder"] = any(
                root.name.lower() in str(c.get("text", "")).lower()
                or str(root).lower() in str(c.get("text", "")).lower()
                for c in cards
            )
            if s.present(APPROVE_SELECTOR):
                s.click(APPROVE_SELECTOR)
                clicked = True
        evidence["user_clicked_approve"] = clicked

        final = settle_run(s, run_id)
        evidence["run_status"] = final.get("status")
        time.sleep(2)
        s.shot("wc4-outcome")
        stream = events(s, run_id)
        evidence["approvals"] = approval_events(stream)
        evidence["tools"] = tool_calls(stream)
        answer = assistant_text(s, run_id) or ""
        evidence["mentions_canary"] = canary.lower() in answer.lower()
    dump(s.run_dir, "wc4-evidence.json", evidence)

    failures: list[str] = []
    if not evidence.get("cards_seen"):
        failures.append("no consent card was ever rendered for an ungranted folder")
    elif not evidence.get("card_names_folder"):
        failures.append("the consent card did not name the folder it was asking about")
    if evidence.get("run_status") != "completed":
        failures.append(f"run did not complete: {evidence.get('run_status')!r}")
    if not evidence.get("mentions_canary"):
        failures.append("the approved listing did not contain the canary file")
    _fail(failures)


def wc5_downloads_asks_for_approval(s: DriverSession) -> None:
    """ "Read my Downloads" must produce a CONSENT REQUEST, not an empty listing.

    The demo of the whole capability, and the same mechanism `auth_mcp` /
    `suggest_mcp_connector` park on.
    """

    require(DOWNLOADS.is_dir(), "no ~/Downloads on this host")
    evidence: dict[str, Any] = {
        "downloads_entries_on_disk": len(list(DOWNLOADS.iterdir()))
    }

    s.send(f"List the files in {DOWNLOADS}. If you need permission, ask.")
    s.shot("wc5-asked-for-downloads")
    conversation_id = wait_for_conversation_id(s)
    run_id = wait_for_new_run(s, conversation_id, 0)
    evidence["run_id"] = run_id

    # Watch for the consent surface while the run is LIVE: a blocking grant
    # request parks the run, so waiting for a terminal run first would wait
    # forever on the very outcome we want.
    approval = None
    for _ in range(90):
        approval = s.evaluate(APPROVAL_JS)
        if approval:
            break
        time.sleep(1.0)
    evidence["approval_on_screen"] = approval
    s.shot("wc5-approval-or-not")

    stream = events(s, run_id)
    evidence["approval_events"] = [
        str(e.get("event_type"))
        for e in stream
        if isinstance(e, dict) and "approval" in str(e.get("event_type", ""))
    ]
    dump(s.run_dir, "wc5-evidence.json", evidence)

    assert bool(evidence["approval_on_screen"]) or bool(evidence["approval_events"]), (
        "asking for ~/Downloads produced neither a consent card nor an approval "
        "event — the ungranted read was answered silently"
    )


def wc6_checklist_and_consent_coexist_honestly(s: DriverSession) -> None:
    """A parked run must LOOK parked: waiting rows, waiting cards, no spinners.

    Three decisions that came out of seeing it live: the consent card is IN the
    transcript anchored where it was asked (not in a pinned strip), the
    checklist stays directly above the composer, and a blocked row waits rather
    than spins — the tool card above it was the last surface still saying
    "Running" over a run that was doing nothing.
    """

    fixture = Path(tempfile.mkdtemp(prefix="wc6-gate-"))
    (fixture / f"canary-{uuid.uuid4().hex[:10]}.txt").write_text("ok\n")

    s.send(
        "Use the write_todos tool to plan this as exactly THREE todos, then do "
        "them one at a time, marking each completed before the next: "
        f"(1) list the files in the directory {fixture}, (2) report the exact "
        "file names you found, (3) state how many files there were. If you "
        "cannot read the directory, say so plainly and do not guess."
    )
    assert s.wait_for("[data-testid=tc-chat]", 60), "transcript never opened"

    deadline = time.time() + 180
    view: dict = {}
    while time.time() < deadline:
        view = gate_observe(s)
        if view.get("todoRows") and view.get("cardPending") == "true":
            break
        time.sleep(0.25)
    else:
        s.shot("wc6-no-overlap")
        raise PhaseSkipped(
            f"the gate and the checklist never overlapped; last seen {json.dumps(view)}"
        )

    s.shot("wc6-gate-and-checklist")
    # The card is in the transcript, and NOTHING approval-shaped is anywhere
    # else — stated so that a pinned strip under a NEW name fails too.
    assert view["cardInTranscript"], (
        f"the consent card is not anchored in the transcript: {view!r}"
    )
    assert view["approvalsOutsideTranscript"] == [], (
        "approval nodes rendered outside the transcript — a pinned strip is "
        f"back: {view['approvalsOutsideTranscript']!r}"
    )
    order = view["order"]
    assert order[-1] == "tc-chat-composer-slot", order
    assert order[-2] == "tc-todo-list", (
        f"the checklist must sit directly above the composer; got {order!r}"
    )
    assert view["waitingLine"], "no reachability affordance while parked"

    assert view["todoBlocked"] == "true", view
    active = [r for r in view["todoRows"] if r["status"] == "in_progress"]
    assert active, f"no in-progress row to check: {view['todoRows']!r}"
    for row in active:
        assert row["waiting"] == "true", row
        assert not row["spinner"], f"a parked row is still spinning: {row!r}"
        assert row["stillGlyph"], f"the waiting glyph is missing: {row!r}"
        assert "waiting for you" in row["text"], row

    stalled = [c for c in view["toolCards"] if c["status"] == "running"]
    assert stalled, f"no running tool card to check: {view['toolCards']!r}"
    for card in stalled:
        assert card["waiting"] == "true", card
        assert not card["spinner"], f"a parked tool card still spins: {card!r}"
        assert card["stillGlyph"], f"the waiting glyph is missing: {card!r}"
        # "Needs you" is the card the decision is actually ABOUT; "Waiting"
        # is every other open call while the graph is interrupted. Both are
        # parked, which is what this block asserts.
        assert "Waiting" in card["text"] or "Needs you" in card["text"], card
    log(
        f"{len(active)} parked row(s) and {len(stalled)} parked card(s) read as waiting"
    )


# ── the composer, after the first message ────────────────────────────────────
def wc7_the_bar_is_gone_and_the_control_row_is_ordered(s: DriverSession) -> None:
    """The folder bar is scoped to the moment before the first message.

    Measured once a run has SETTLED: while a run is in flight the trailing
    button is Stop, not Send, so an in-flight reading would report a missing
    control that is merely doing its job.
    """

    failures: list[str] = []
    assert s.wait_for(PLUS, timeout_s=90), "run composer never appeared"
    time.sleep(3)
    run_bar = jc_folder_bar(s)
    controls = jc_rects(s)
    order = jc_order(controls)
    s.shot("wc7-run-composer")

    s.click(PLUS)
    time.sleep(0.4)
    s.shot("wc7-run-plus-menu")
    if jc_attach_folder_row(s):
        failures.append("the `+` menu still offers Attach Folder")
    s.press("body", "Escape")

    if run_bar and run_bar.get("visible"):
        failures.append("the folder bar is still showing after the first message")
    expected = ["plus", "tools", "bypass", "model", "mic", "send"]
    present_expected = [name for name in expected if name in order]
    if [name for name in order if name in expected] != present_expected:
        failures.append(f"control row order is {order}, expected {expected}")
    missing = [name for name in expected if name not in order]
    if missing:
        failures.append(f"control row is missing {missing}")
    log(f"control row order = {order}")
    _fail(failures)


def wc8_the_agents_scratch_is_inspectable_on_disk(s: DriverSession) -> None:
    """DEPENDS ON WC-3. `<COPILOT_HOME>/.tmp/<conversation_id>/` really exists.

    Two tiers are claimed to be inspectable files: the run's `tool-results/`
    and its `subagents/`. Both homes are probed because a CLI launch and a
    supervised launch do not necessarily agree about COPILOT_HOME.
    """

    conversation_id = STATE.get("conversation_id")
    require(conversation_id, "needs the conversation WC-3 created")
    launched_home = Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".0xcopilot"))

    launched = jc_scratch_probe(launched_home, conversation_id)
    default = jc_scratch_probe(Path.home() / ".0xcopilot", conversation_id)
    chosen = (
        launched
        if launched.get("conversation_exists")
        else default
        if default.get("conversation_exists")
        else None
    )
    dump(
        s.run_dir,
        "wc8-evidence.json",
        {"launched": launched, "default": default, "conversation_id": conversation_id},
    )

    failures: list[str] = []
    if chosen is None:
        failures.append(
            "no `<COPILOT_HOME>/.tmp/<conversation_id>/` was created at either "
            f"{launched.get('scratch_root')!r} or {default.get('scratch_root')!r}"
        )
    else:
        if not chosen.get("meta_exists"):
            failures.append("the conversation scratch has no meta.json")
        if not chosen.get("tree"):
            failures.append("the conversation scratch is empty")
        if not chosen.get("tool_result_files") and not chosen.get("subagent_files"):
            failures.append(
                "the run's tool-results/ and subagents/ tiers exist but hold no "
                "files — nothing about this run is inspectable there"
            )
    # The app's own title, so the meta.json claim can be checked against it.
    try:
        transport_json(s, "GET", f"/v1/agent/conversations/{conversation_id}")
    except Exception as exc:  # noqa: BLE001 — a probe must not fail the phase
        log(f"conversation lookup failed: {exc!r}"[:200])
    _fail(failures)


def wc9_report_what_mcp_is_actually_configured(s: DriverSession) -> None:
    """REPORTED, not asserted: is there an MCP server this session could call?

    Deliberately narrow, and answered from the running app's own authenticated
    surfaces plus the Tools destination a user sees. It exists so the MCP
    journeys can say "not connected" with evidence instead of guessing.
    """

    evidence: dict[str, Any] = {}
    catalog = je_safe(s, "/v1/mcp/catalog")
    servers = je_safe(s, "/v1/mcp/servers")
    tools = je_safe(s, "/v1/mcp/tools")

    def entries(value: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
        if isinstance(value, dict):
            for key_name in keys:
                found = value.get(key_name)
                if isinstance(found, list):
                    return [v for v in found if isinstance(v, dict)]
        return []

    catalog_entries = entries(catalog, "entries", "catalog", "servers")
    server_entries = entries(servers, "servers", "items")
    tool_entries = entries(tools, "tools", "items")
    evidence["catalog_slugs"] = sorted(
        str(e.get("slug") or e.get("id") or e.get("display_name"))
        for e in catalog_entries
    )
    evidence["installed_server_count"] = len(server_entries)
    evidence["available_tool_count"] = len(tool_entries)
    evidence["linear_installed"] = any(
        str(e.get("slug")) == "linear" for e in server_entries
    )
    evidence["linear_in_catalog"] = any(
        str(e.get("slug")) == "linear" for e in catalog_entries
    )

    if s.present(SKIP_FIRST_RUN):
        s.click(SKIP_FIRST_RUN)
    assert s.wait_for(TOOLS_RAIL, timeout_s=30), "no Tools destination in the nav rail"
    s.click(TOOLS_RAIL)
    time.sleep(3)
    s.shot("wc9-tools-destination")
    evidence["tools_surface_text"] = (
        s.evaluate(
            "((document.querySelector('main') || document.body).innerText || '')"
            ".trim().slice(0, 2500)"
        )
        or ""
    )
    dump(s.run_dir, "wc9-mcp-state.json", evidence)
    log(
        f"linear_in_catalog={evidence['linear_in_catalog']} "
        f"linear_installed={evidence['linear_installed']} "
        f"servers={evidence['installed_server_count']} "
        f"tools={evidence['available_tool_count']}"
    )
    STATE["mcp"] = evidence


def main() -> int:
    plan = JourneyPlan("workspace-consent")
    plan.boot(
        "source · fresh · DEFAULT lane",
        lambda: DriverSession(name="workspace-consent"),
        setup=sign_in_and_key,
        env=DEFAULT_LANE,
        phases=[
            (
                "WC-1",
                "the folder affordance is the composer bar, not a `+` menu row",
                wc1_folder_affordance_is_the_bar_not_a_menu_row,
            ),
            (
                "WC-2",
                "the populated folder bar: chip, +N collapse, newest-first, revoke",
                wc2_populated_folder_bar,
            ),
            (
                "WC-3",
                "an ungranted host path never empty-succeeds",
                wc3_an_ungranted_path_never_empty_succeeds,
            ),
            (
                "WC-4",
                "the consent card names the folder, and Approve returns it",
                wc4_the_card_names_the_folder_and_approve_returns_it,
            ),
            (
                "WC-5",
                "asking for ~/Downloads produces an approval, not a listing",
                wc5_downloads_asks_for_approval,
            ),
            (
                "WC-6",
                "the checklist and a consent card coexist honestly",
                wc6_checklist_and_consent_coexist_honestly,
            ),
            (
                "WC-7",
                "the folder bar is gone after the first message; controls ordered",
                wc7_the_bar_is_gone_and_the_control_row_is_ordered,
            ),
            (
                "WC-8",
                "the agent's scratch is inspectable on disk [needs WC-3]",
                wc8_the_agents_scratch_is_inspectable_on_disk,
            ),
            (
                "WC-9",
                "report what MCP is actually configured (reported, not asserted)",
                wc9_report_what_mcp_is_actually_configured,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
