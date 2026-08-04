#!/usr/bin/env python3
"""Journey — thread scope, re-filing, unfiling, and the SERVER-side filter.

`file_a_chat.py` proves one chat can enter one project. This one covers the
paths that only exist once there is more than one of each, and it is the
journey that actually exercises the BACKEND rather than the binder:

  A. `GET /v1/agent/conversations?filter[project_id]=…` really narrows the rows.
     The facade reads the app-standard `filter[project_id]` alias and rewrites
     it to ai-backend's plain `project_id`; a silently-ignored filter would
     return everything and every UI assertion downstream would still pass,
     because the panel would be showing a correct render of wrong data.
  B. The Threads panel scoped to a project lists that project's chats and NOT
     the others'.
  C. "New run" under a scope creates its conversation already filed there —
     the coupling that justifies putting the scope under the button.
  D. Re-filing an EXISTING chat (the PATCH path, as opposed to `file_a_chat`'s
     create path) persists.
  E. Unfiling writes `project_id: null` rather than being a no-op — `null` and
     "absent" are different things to a PATCH, and only one of them clears.

Conversations are seeded through the app's own authenticated transport, which
is a real facade call, not a fixture: the point of the seeds is to reach the
multi-project state quickly, and every ASSERTION still reads the server back.

    python3 tools/desktop-journeys/projects-filing/scope_and_refile.py

Requires an Anthropic key in services/ai-backend/.env (never printed) — the app
will not leave the first-run gate without a model configured.
Exits non-zero on any failed assertion; 3 = skipped prerequisite.
"""

from __future__ import annotations

import json
import os as _os
import sys as _sys
import time
from pathlib import Path

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _lib import DriverSession, load_env_key  # noqa: E402

PROJECT_A = "Acme renewal"
PROJECT_B = "Kleos research"


def _clear_previous_screenshots() -> None:
    """See file_a_chat.py — a partial run otherwise leaves misleading PNGs."""
    shots = Path(__file__).resolve().parent.parent / "runs" / "projects-scope"
    for png in shots.glob("screenshots/*.png"):
        png.unlink()


# --- transport helpers: every assertion reads the SERVER, not the DOM --------


def _post(s: DriverSession, path: str, body: dict) -> dict:
    """POST through the app's bridge (the `transport` helper is GET-only)."""
    js = (
        '(async()=>{try{const r=await window.bridge.ipc.invoke("transport.request",'
        f'{{method:"POST",path:{json.dumps(path)},body:{json.dumps(body)}}});'
        'if(r&&r.kind==="transport-result"){'
        'if(!r.ok)return "ERR:HTTP "+String(r.error?.status??"unknown")+'
        '" "+String(r.error?.message??"request failed");'
        "return JSON.stringify(r.value);}"
        "return JSON.stringify(r);}"
        'catch(e){return "ERR:"+e.message}})()'
    )
    raw = s.evaluate(js)
    if isinstance(raw, str) and raw.startswith("ERR:"):
        raise RuntimeError(f"POST {path} failed: {raw}")
    return json.loads(raw)


def _create_project(s: DriverSession, name: str, hue: int) -> str:
    # `icon_emoji` and `color_hue` are REQUIRED by the backend's create model —
    # omitting them is a 422, not a defaulted row. (The app's own create sheet
    # always sends both, which is why the UI path never hits this.)
    created = _post(
        s,
        "/v1/projects",
        {"name": name, "description": "", "icon_emoji": "📁", "color_hue": hue},
    )
    project_id = created.get("id")
    assert isinstance(project_id, str) and project_id, (
        f"POST /v1/projects returned no id for {name!r}: {created}"
    )
    return project_id


def _create_chat(s: DriverSession, title: str, project_id: str | None) -> str:
    body: dict = {"title": title}
    if project_id is not None:
        body["project_id"] = project_id
    created = _post(s, "/v1/agent/conversations", body)
    cid = created.get("conversation_id")
    assert isinstance(cid, str) and cid, f"no conversation_id in {created}"
    return cid


def _conversations(s: DriverSession, project_id: str | None = None) -> list[dict]:
    path = "/v1/agent/conversations?limit=50"
    if project_id is not None:
        path += f"&filter[project_id]={project_id}"
    payload = s.transport("GET", path)
    return list(payload.get("conversations") or [])


def _wait_run_settled(
    s: DriverSession, conversation_id: str, timeout_s: int = 120
) -> str:
    """Block until the chat's latest run stops moving.

    Not politeness — necessity. While a run streams, the composer re-renders on
    every delta and Playwright loses the menu row mid-click ("element was
    detached from the DOM"). Re-filing mid-stream is also not the flow a person
    performs, so the journey waits rather than racing.
    """
    # The signal is the COMPOSER, not `latest_run_status`.
    #
    # Two attempts at the server-side field failed. "not running" returned
    # instantly, because the row carries `latest_run_status: null` for a moment
    # after the send. Waiting for a terminal value then timed out at 120s — the
    # field was STILL null long after the answer had streamed and the composer
    # had gone idle. That is worth a look on its own (the Chats list's status
    # chip is projected from the same field), and it is recorded as an
    # observation at the end of this journey rather than asserted here.
    #
    # What the user sees is authoritative for "can I click now": the send button
    # is `aria-label="Stop response"` while a run is in flight and
    # `"Send message"` when it is not.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        idle = s.evaluate(
            "!!document.querySelector('button[aria-label=\"Send message\"]') && "
            "!document.querySelector('button[aria-label=\"Stop response\"]')"
        )
        if idle:
            # One settling beat: the final render lands just after the swap.
            time.sleep(2)
            row = next(
                (
                    c
                    for c in _conversations(s)
                    if c["conversation_id"] == conversation_id
                ),
                {},
            )
            return str(row.get("latest_run_status"))
        time.sleep(2)
    raise AssertionError(f"composer never went idle for {conversation_id}")


def _project_of(s: DriverSession, conversation_id: str) -> str | None:
    row = s.transport("GET", f"/v1/agent/conversations/{conversation_id}")
    conv = row.get("conversation") if isinstance(row.get("conversation"), dict) else row
    return conv.get("project_id")


# --- DOM helpers -------------------------------------------------------------


def _thread_titles(s: DriverSession) -> list[str]:
    raw = s.evaluate(
        "Array.from(document.querySelectorAll("
        '"[data-testid^=thread-switcher-row-]")).map(e=>e.innerText.trim())'
    )
    return list(raw or [])


def _chip_label(s: DriverSession) -> str:
    return (
        s.evaluate(
            '(document.querySelector("[data-testid=composer-project-filing-trigger]")'
            '||{}).innerText||""'
        )
        or ""
    )


def _open_filing_menu(s: DriverSession, timeout_s: int = 30) -> None:
    """Open the composer's filing menu, waiting out any in-flight write.

    The trigger carries `disabled` while a filing PATCH is in flight, and the
    click is a silent no-op against a disabled button — so a bare click
    followed by `wait_for(menu)` fails with no indication of why.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        enabled = s.evaluate(
            "(()=>{const b=document.querySelector("
            '"[data-testid=composer-project-filing-trigger]");'
            "return !!b && !b.disabled;})()"
        )
        if enabled:
            break
        time.sleep(1)
    assert s.present("[data-testid=composer-project-filing-trigger]"), (
        "no filing trigger on the composer"
    )
    s.click("[data-testid=composer-project-filing-trigger]")
    assert s.wait_for("[data-testid=composer-project-filing-menu]", 15), (
        "filing menu did not open"
    )


def _open_threads_panel(s: DriverSession) -> None:
    """Open the cockpit's Threads panel if it is not already docked open.

    The panel is collapsed by default and its toggle lives in the run header,
    not the rail — `open_destination` cannot reach it.
    """
    if s.present("[data-testid=thread-switcher-title]"):
        return
    assert s.wait_for("[data-testid=thread-switcher-toggle]", 20), (
        "no Threads toggle in the run header"
    )
    s.click("[data-testid=thread-switcher-toggle]")
    time.sleep(1.5)


def main() -> int:
    _clear_previous_screenshots()
    key = load_env_key("anthropic")  # value never printed
    print(f"[projects-scope] anthropic key_len={len(key)} (value withheld)")

    with DriverSession(name="projects-scope") as s:
        s.sign_in_local()
        s.ftue_add_key("anthropic", key)
        assert s.wait_for("[data-testid=first-run-skip]")
        s.click("[data-testid=first-run-skip]")
        assert s.wait_for("[aria-label='Run'][data-destination]", 30), (
            "workspace rail never appeared"
        )

        # --- Seed: two projects, three chats -------------------------------
        a_id = _create_project(s, PROJECT_A, 210)
        b_id = _create_project(s, PROJECT_B, 150)
        a1 = _create_chat(s, "Renewal pricing model", a_id)
        a2 = _create_chat(s, "Redline MSA section 7", a_id)
        b1 = _create_chat(s, "Screening rubric v2", b_id)
        loose = _create_chat(s, "Hello world", None)
        print(f"[projects-scope] projects A={a_id} B={b_id}")

        # === A. The server-side filter actually narrows =====================
        # If the facade dropped the alias, this returns all four and the panel
        # assertions below would still "pass" against wrong data.
        in_a = {c["conversation_id"] for c in _conversations(s, a_id)}
        in_b = {c["conversation_id"] for c in _conversations(s, b_id)}
        all_ids = {c["conversation_id"] for c in _conversations(s)}

        assert in_a == {a1, a2}, (
            f"filter[project_id]={a_id} returned {sorted(in_a)}, expected the "
            f"two chats filed there — the facade alias is not reaching ai-backend"
        )
        assert in_b == {b1}, f"project B filter returned {sorted(in_b)}"
        assert {a1, a2, b1, loose} <= all_ids, (
            "the unscoped list is missing seeded chats"
        )
        assert loose not in in_a and loose not in in_b, (
            "an unfiled chat leaked into a project-scoped list"
        )
        print(f"[projects-scope] server filter OK: A={len(in_a)} B={len(in_b)}")

        # Every row the server returns must carry the field the UI reads.
        for row in _conversations(s, a_id):
            assert row.get("project_id") == a_id, (
                f"row {row.get('conversation_id')} is missing project_id: {row}"
            )

        # === B. The Threads panel, scoped ===================================
        # Bounce off Run and back so the cockpit REMOUNTS: the project list is
        # fetched on mount and memoised in a module-level cache, and these
        # projects were seeded through the transport after that first fetch, so
        # without a remount the picker would still hold the empty list it read
        # at boot. A user creating a project through the sheet does not need
        # this — that path calls the hook's own `reload`.
        s.open_destination("Chats")
        s.open_destination("Run")
        _open_threads_panel(s)
        assert s.wait_for("[data-testid=thread-switcher-scope]", 30), (
            "no scope control in the Threads panel"
        )
        s.shot("threads-unscoped")

        unscoped = _thread_titles(s)
        assert any("Hello world" in t for t in unscoped), (
            f"unscoped panel is missing the unfiled chat: {unscoped}"
        )

        s.click("[data-testid=thread-switcher-scope-trigger]")
        assert s.wait_for("[data-testid=thread-switcher-scope-menu]")
        s.shot("threads-scope-menu")
        s.click(f'[data-testid=thread-switcher-scope-option][data-project-id="{a_id}"]')
        time.sleep(2)

        scoped = _thread_titles(s)
        assert any("Renewal pricing" in t for t in scoped), (
            f"scoped panel does not list project A's chats: {scoped}"
        )
        assert not any("Screening rubric" in t for t in scoped), (
            f"project B's chat leaked into a panel scoped to A: {scoped}"
        )
        assert not any("Hello world" in t for t in scoped), (
            f"an unfiled chat leaked into a scoped panel: {scoped}"
        )
        s.shot("threads-scoped-to-a")
        print(f"[projects-scope] panel scoped to A lists {len(scoped)} rows")

        # === C. "New run" inherits the scope ================================
        before = {c["conversation_id"] for c in _conversations(s)}
        s.click("[data-testid=thread-switcher-new]")
        time.sleep(2)
        # The chat does not exist until the first send, so the chip is what
        # carries the inheritance until then — assert it, then send.
        assert PROJECT_A in _chip_label(s), (
            f"a new run under scope A opened unfiled; chip reads {_chip_label(s)!r}"
        )
        s.shot("new-run-inherits-scope")

        s.fill("[data-testid=composer-textarea]", "Say only: scoped.")
        time.sleep(0.3)
        s.click('button[aria-label="Send message"]')

        deadline = time.time() + 90
        fresh: dict | None = None
        while time.time() < deadline:
            new = [c for c in _conversations(s) if c["conversation_id"] not in before]
            if new:
                fresh = new[0]
                break
            time.sleep(1.5)
        assert fresh is not None, "New run never created a conversation"
        assert fresh.get("project_id") == a_id, (
            "a run started under scope A was filed elsewhere: "
            f"{json.dumps(fresh)[:300]}"
        )
        print(
            f"[projects-scope] New run → {fresh['conversation_id']} filed in "
            f"{fresh.get('project_id')}"
        )

        # === D. Re-file an EXISTING chat (the PATCH path) ===================
        moved_id = fresh["conversation_id"]
        settled = _wait_run_settled(s, moved_id)
        print(f"[projects-scope] run settled ({settled}); re-filing")

        _open_filing_menu(s)
        s.click(
            f'[data-testid=composer-project-filing-option][data-project-id="{b_id}"]'
        )
        time.sleep(1.5)
        assert PROJECT_B in _chip_label(s), (
            f"chip did not move to project B, reads {_chip_label(s)!r}"
        )

        deadline = time.time() + 20
        while time.time() < deadline:
            if _project_of(s, moved_id) == b_id:
                break
            time.sleep(1)
        assert _project_of(s, moved_id) == b_id, (
            "the UI moved the chat to B but the server still has "
            f"{_project_of(s, moved_id)!r} — the PATCH did not persist"
        )
        s.shot("refiled-to-b")
        print(f"[projects-scope] PATCH re-file persisted: {moved_id} → {b_id}")

        # === E. Unfiling clears the field ===================================
        # `null` and "absent" are different to a PATCH; only one of them clears.
        _open_filing_menu(s)
        s.click("[data-testid=composer-project-filing-none]")
        time.sleep(1.5)

        deadline = time.time() + 20
        while time.time() < deadline:
            if _project_of(s, moved_id) is None:
                break
            time.sleep(1)
        assert _project_of(s, moved_id) is None, (
            "unfiling left the chat filed as "
            f"{_project_of(s, moved_id)!r} — the write sent no explicit null"
        )
        assert "No project" in _chip_label(s), (
            f"chip did not return to 'No project': {_chip_label(s)!r}"
        )
        s.shot("unfiled")
        print("[projects-scope] unfile cleared project_id on the server")

        # --- Observation, deliberately NOT an assertion ---------------------
        # Unrelated to filing, found while building this journey: the
        # conversation list's `latest_run_status` still read "running" after the
        # composer had gone idle and the answer had fully streamed. The Chats
        # list projects its status chip from that same field
        # (`chatArchiveStatus`), so a stale value there would show a finished
        # chat as still working. Printed rather than asserted because it is
        # outside this change's scope and needs its own investigation.
        final = next(
            (c for c in _conversations(s) if c["conversation_id"] == moved_id),
            {},
        )
        print(
            "[projects-scope] OBSERVATION latest_run_status="
            f"{final.get('latest_run_status')!r} while the composer is idle"
        )

    print(
        json.dumps({"journey": "projects-filing/scope_and_refile", "outcome": "passed"})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
