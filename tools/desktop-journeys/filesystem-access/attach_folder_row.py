#!/usr/bin/env python3
"""FS2 — the folder affordance is the BAR on the composer, not a `+` menu row.

THIS ASSERTION IS INVERTED FROM WHAT IT USED TO BE, deliberately. This file
previously demanded that an "Attach Folder" row EXIST in the composer's `+`
menu. PRD-FS-10 deletes that row: a folder grant copies nothing into the message
and OUTLIVES it (it persists until revoked), so housing it beside "Attach Image"
taught the wrong model of what a grant is — and two entry points to one
capability is how the grant model got muddled in the first place. The affordance
now sits ON the composer frame, above the input, before the first message.

So the journey asks two questions of the real app:

  1. the `+` menu must NOT offer Attach Folder (in the FTUE composer or the Run
     composer) — the row is gone, not merely deprecated;
  2. the folder bar must be REACHABLE before the first message, on the FTUE
     composer, where handing the agent a folder matters most. That mount never
     received `workspaceGrantPort`, which is why the old affordance appeared in
     Run and not on first run.

It also checks the visibility rule the PRD calls out as the one that needs care:
after the first message is sent, the bar is GONE (the grant is not — the bar is
scoped to the moment, the access is not).

Asserts by ACCESSIBLE NAME / visible text, not by a test id: these controls are
what a user reaches for, so if they are unreachable by their label the
affordance does not exist for a person regardless of what the DOM contains.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generative-workflows"))
from g2_csv_lifecycle import (  # noqa: E402
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
)

from ungranted_path_asks import _journey_environment  # noqa: E402

PLUS: Final = 'button[aria-label="Open attachment and tools menu"]'

_MENU_SELECTOR: Final = '.ui-pop__row, [role="menuitem"], button'

_ROW_TITLES_JS: Final = (
    "(() => Array.from(document.querySelectorAll('"
    + _MENU_SELECTOR
    + "')).map((el) => (el.textContent || '').trim()).filter(Boolean)"
    ".map((t) => t.slice(0, 80)))()"
)

# The row that must NOT exist any more, anywhere in the open menu.
_ROW_PROBE_JS: Final = (
    "(() => { const hit = Array.from(document.querySelectorAll('"
    + _MENU_SELECTOR
    + "')).find((el) => /attach folder/i.test(el.textContent || ''));"
    " if (!hit) return null; const r = hit.getBoundingClientRect();"
    " return { text: (hit.textContent || '').trim().slice(0, 160),"
    " visible: r.width > 0 && r.height > 0,"
    " enabled: !hit.hasAttribute('disabled') }; })()"
)

# The bar that must exist instead: the composer's folder control, named by what
# a user reads on it. Empty state says "Attach a folder"; once a folder is
# granted it says the BASENAME (never a path — that is the §5 rule this also
# guards, by reporting the text so a leak would be visible in the evidence).
_BAR_PROBE_JS: Final = (
    "(() => { const hit = document.querySelector('.aui-folder-bar__attach');"
    " if (!hit) return null; const r = hit.getBoundingClientRect();"
    " return { text: (hit.textContent || '').trim().slice(0, 160),"
    " visible: r.width > 0 && r.height > 0,"
    " enabled: !hit.hasAttribute('disabled') }; })()"
)


def _probe_row(session: DriverSession) -> Any:
    """Is an 'Attach Folder' row reachable in whatever menu is open now?"""

    return session.evaluate(_ROW_PROBE_JS)


def _probe_bar(session: DriverSession) -> Any:
    """Is the composer's folder bar on screen right now?"""

    return session.evaluate(_BAR_PROBE_JS)


def _result(outcome: str, **extra: Any) -> None:
    print(
        json.dumps({"journey": "FS2", "outcome": outcome, **extra}, sort_keys=True),
        flush=True,
    )


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", reason=str(exc))
        return 0

    _result("running", provider=provider)
    with _journey_environment():
        session = DriverSession(name="filesystem-access-fs2-folder-bar")
        found: dict[str, Any] = {}
        try:
            with session:
                session.sign_in_local()
                session.ftue_add_key(provider, key)
                session.shot("fs2-01-ready")

                assert session.wait_for(PLUS, timeout_s=45), (
                    "composer plus trigger never appeared"
                )

                # (2) The bar is on the FTUE composer, before anything is sent.
                found["ftue_folder_bar"] = _probe_bar(session)
                session.shot("fs2-02-ftue-folder-bar")

                # (1) …and the `+` menu no longer offers the row.
                session.click(PLUS)
                session.shot("fs2-03-plus-menu-open")
                found["ftue_rows"] = session.evaluate(_ROW_TITLES_JS)
                found["ftue_attach_folder_row"] = _probe_row(session)

                # The FTUE composer and the Run composer are DIFFERENT mounts,
                # so ask the same question of the composer a user spends their
                # actual session in.
                session.press("body", "Escape")
                session.send_first_run_message(
                    "Say READY and nothing else. Do not call any tool."
                )
                assert session.wait_for(PLUS, timeout_s=90), (
                    "run composer plus trigger never appeared"
                )
                session.shot("fs2-04-run-composer")

                # The visibility rule: gone once the chat has started.
                found["run_folder_bar"] = _probe_bar(session)

                session.click(PLUS)
                session.shot("fs2-05-run-composer-menu-open")
                found["run_attach_folder_row"] = _probe_row(session)
                found["run_rows"] = session.evaluate(_ROW_TITLES_JS)
        finally:
            out = session.run_dir / "fs2-evidence.json"
            out.write_text(
                json.dumps(found, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"[fs2] evidence -> {out}", flush=True)
            print(f"[fs2] shots    -> {session.run_dir}", flush=True)

    failures: list[str] = []
    if found.get("ftue_attach_folder_row") or found.get("run_attach_folder_row"):
        failures.append(
            "the `+` menu still offers Attach Folder — PRD-FS-10 deletes that row"
        )
    bar = found.get("ftue_folder_bar")
    if not bar or not bar.get("visible"):
        failures.append(
            "no folder bar on the FTUE composer — the mount is missing its "
            "workspaceGrantPort, the exact gap PRD-FS-10 §7 closes"
        )
    elif "/" in str(bar.get("text", "")):
        failures.append(f"the folder bar printed a path: {bar.get('text')!r}")
    if found.get("run_folder_bar"):
        failures.append(
            "the folder bar is still showing after the first message was sent"
        )

    if failures:
        _result(
            "FAILED",
            reason="; ".join(failures),
            ftue_rows=len(found.get("ftue_rows") or []),
            run_rows=len(found.get("run_rows") or []),
        )
        return 1
    _result(
        "passed",
        ftue_bar=str((found.get("ftue_folder_bar") or {}).get("text", "")),
        bar_hidden_after_send=True,
        plus_menu_row_gone=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
