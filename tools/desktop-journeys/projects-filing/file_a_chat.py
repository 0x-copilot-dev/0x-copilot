#!/usr/bin/env python3
"""Journey — a chat can be FILED into a project, and the project shows it.

Before this change, Projects on desktop was a container nothing could enter:
every `project_id` in the renderer was a read filter, so a project's card read
"0 chats · 0 files" forever and the detail view was permanently empty. The
counts were not a bug — they were the honest output of a surface with no write.

This journey drives the real packaged app and proves the write EXISTS, in the
harder of the two directions: the create path. Picking a project before the
first message must carry `project_id` onto `POST /v1/agent/conversations`,
because a chat started inside a project is the flow the whole design is built
around. A PATCH-only implementation passes a unit test and fails here.

Every assertion that matters is read back through the app's authenticated
transport, not off the DOM — the DOM proves the UI changed, the server proves
the filing persisted. Those are different claims and only the second one is
the feature.

    python3 tools/desktop-journeys/projects-filing/file_a_chat.py

Requires an Anthropic key in services/ai-backend/.env (never printed).
Exits non-zero on any failed assertion; 3 = skipped prerequisite.
"""

from __future__ import annotations

import json
import os as _os
import sys as _sys
import time

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _lib import DriverSession, load_env_key  # noqa: E402

PROJECT_NAME = "Acme renewal"


def _chip_present(s: DriverSession) -> bool:
    return bool(
        s.evaluate('!!document.querySelector("[data-testid=composer-project-filing]")')
    )


def _chip_label(s: DriverSession) -> str | None:
    return s.evaluate(
        '(document.querySelector("[data-testid=composer-project-filing-trigger]")'
        "||{}).innerText||null"
    )


def _chip_is_below_composer(s: DriverSession) -> bool:
    """The design rule, asserted geometrically rather than by class name.

    Folders (what the agent can REACH) sit above the frame; the project (where
    the work BELONGS) sits below it. A refactor that moved the chip into the
    control row would still render a chip, still pass every unit test, and be
    the wrong product — so measure the pixels.
    """
    return bool(
        s.evaluate(
            "(()=>{"
            'const chip=document.querySelector("[data-testid=composer-project-filing]");'
            'const frame=document.querySelector(".aui-composer-frame")'
            '||document.querySelector("[data-testid=composer-textarea]");'
            "if(!chip||!frame)return false;"
            "return chip.getBoundingClientRect().top >= "
            "frame.getBoundingClientRect().bottom - 2;})()"
        )
    )


def _menu_overlaps_composer(s: DriverSession) -> bool:
    """Do the open filing menu and the composer frame share any pixels?

    Rect intersection, not a class-name check: the failure this guards is a
    positioning bug, and only geometry can see it.
    """
    return bool(
        s.evaluate(
            "(()=>{"
            'const m=document.querySelector("[data-testid=composer-project-filing-menu]");'
            'const f=document.querySelector(".aui-composer-frame")'
            '||document.querySelector("[data-testid=composer-textarea]");'
            "if(!m||!f)return false;"
            "const a=m.getBoundingClientRect(),b=f.getBoundingClientRect();"
            "return !(a.bottom<=b.top||a.top>=b.bottom||"
            "a.right<=b.left||a.left>=b.right);})()"
        )
    )


def _project_ids(s: DriverSession) -> list[str]:
    payload = s.transport("GET", "/v1/projects?limit=50")
    return [p["id"] for p in (payload.get("items") or [])]


def _conversations(s: DriverSession) -> list[dict]:
    payload = s.transport("GET", "/v1/agent/conversations?limit=50")
    return list(payload.get("conversations") or [])


def main() -> int:
    key = load_env_key("anthropic")  # value never printed
    print(f"[projects-filing] anthropic key_len={len(key)} (value withheld)")

    with DriverSession(name="projects-filing") as s:
        # 1. Sign in + a key, so the composer is reachable -------------------
        s.sign_in_local()
        s.ftue_add_key("anthropic", key)
        s.shot("composer-no-projects")

        # The chip is HIDDEN with zero projects (the desktop binder passes no
        # `onCreateProject`, so a chip here would be a control that cannot act).
        # Recorded as a fact, not asserted as desirable — see JOURNEYS.md.
        print(f"[projects-filing] chip with 0 projects: present={_chip_present(s)}")

        # 2. Leave the FTUE hero for the workspace shell ----------------------
        # The first-run surface has no nav rail — `open_destination` cannot work
        # until the skip link hands over to the shell. (The rail-click failure
        # this replaced looked like an app crash; it was a missing rail.)
        assert s.wait_for("[data-testid=first-run-skip]"), (
            "no skip link on the first-run surface"
        )
        s.click("[data-testid=first-run-skip]")
        assert s.wait_for("[aria-label='Projects'][data-destination]", 30), (
            "workspace rail never appeared after leaving first-run"
        )

        # 3. Create a project through the real UI ----------------------------
        s.open_destination("Projects")
        assert s.wait_for("[data-testid=projects-destination]"), (
            "Projects destination never rendered"
        )
        assert s.wait_for("[data-testid=projects-create]"), (
            "no create control on Projects — cannot make the first project"
        )
        s.click("[data-testid=projects-create]")
        assert s.wait_for("[data-testid=project-editor]"), "editor sheet never opened"
        s.fill("[data-testid=project-editor-name-input]", PROJECT_NAME)
        time.sleep(0.3)
        s.shot("project-editor")
        # "Save", not "Create" — the create sheet reuses the edit editor
        # verbatim, so it is even titled "Edit project". Noted in JOURNEYS.md.
        s.click('[data-testid=project-editor] button:has-text("Save")')

        deadline = time.time() + 30
        project_ids: list[str] = []
        while time.time() < deadline:
            project_ids = _project_ids(s)
            if project_ids:
                break
            time.sleep(1)
        assert project_ids, "POST /v1/projects never produced a project"
        project_id = project_ids[0]
        print(f"[projects-filing] created project {project_id}")
        s.shot("projects-grid-before")

        # 3. Back to Run — the chip must now exist, BELOW the composer -------
        s.open_destination("Run")
        assert s.wait_for("[data-testid=composer-project-filing]", 30), (
            "filing chip absent on Run even though a project exists"
        )
        assert _chip_is_below_composer(s), (
            "filing chip is not below the composer frame — the above/below "
            "split (folders up, project down) has been broken"
        )
        assert "No project" in (_chip_label(s) or ""), (
            f"a fresh chat should read 'No project', got {_chip_label(s)!r}"
        )
        s.shot("chip-unfiled")

        # 4. File the chat, BEFORE the first message -------------------------
        s.click("[data-testid=composer-project-filing-trigger]")
        assert s.wait_for("[data-testid=composer-project-filing-menu]"), (
            "filing menu never opened"
        )
        time.sleep(0.4)  # let the portal settle its fixed coords
        s.shot("chip-menu-open")
        # The menu must CLEAR the composer, not cover it. The first cut reused
        # the `+` menu's renderer verbatim, which is hard-coded to open upward
        # — correct for a trigger inside the frame, and it drew this panel
        # straight over the Tools / Manual / model row. Unit tests could not see
        # it: they assert the slot was called, never where the pixels landed.
        assert not _menu_overlaps_composer(s), (
            "the filing menu overlaps the composer frame — anchored-popover "
            "placement regressed to opening upward"
        )
        s.click(
            f'[data-testid=composer-project-filing-option][data-project-id="{project_id}"]'
        )
        time.sleep(0.5)
        assert PROJECT_NAME in (_chip_label(s) or ""), (
            f"chip did not adopt the picked project, reads {_chip_label(s)!r}"
        )
        s.shot("chip-filed")

        # 5. Send the first message — this is where the CREATE path runs -----
        before = {c["conversation_id"] for c in _conversations(s)}
        s.fill("[data-testid=composer-textarea]", "Say only: filed.")
        time.sleep(0.3)
        s.click('button[aria-label="Send message"]')

        deadline = time.time() + 90
        created: dict | None = None
        while time.time() < deadline:
            fresh = [c for c in _conversations(s) if c["conversation_id"] not in before]
            if fresh:
                created = fresh[0]
                break
            time.sleep(1.5)
        assert created is not None, "no conversation was created by the first send"

        # THE assertion. The DOM showing a project name proves nothing about
        # what was persisted; this reads the server's own copy of the row.
        assert created.get("project_id") == project_id, (
            "the conversation was created WITHOUT the project — the create path "
            f"dropped project_id. server row: {json.dumps(created)[:400]}"
        )
        print(
            f"[projects-filing] conversation {created['conversation_id']} "
            f"persisted project_id={created.get('project_id')}"
        )
        s.shot("run-filed")

        # 6. The count that started all of this ------------------------------
        s.open_destination("Projects")
        assert s.wait_for("[data-testid=project-card-counts]", 30)
        counts = s.evaluate(
            '(document.querySelector("[data-testid=project-card-counts]")'
            "||{}).innerText||null"
        )
        print(f"[projects-filing] project card counts: {counts!r}")
        assert counts is not None and not counts.strip().startswith("0 chats"), (
            f"project card still reads {counts!r} — filing did not reach the count"
        )
        s.shot("projects-grid-after")

    print(json.dumps({"journey": "projects-filing/file_a_chat", "outcome": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
