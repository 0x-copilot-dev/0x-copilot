#!/usr/bin/env python3
"""FS2 — the Attach Folder row must actually render in the desktop composer.

The row is deliberately gated: ComposerPlusMenu renders it only when the host
supplies `onAttachFolder`, which AssistantComposer supplies only when a
`WorkspaceGrantPort` is non-null. Web has no such port and must NOT show it.
That gate is correct, and it is also exactly the kind of seam where a feature
can be fully built, fully unit-tested, and still render nowhere — so the only
honest check is to open the real menu in the real app and look.

Asserts by ACCESSIBLE NAME, not by a test id: the row is what a user reaches
for, so if it is unreachable by its label the affordance does not exist for a
person regardless of what the DOM contains.
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

_ROW_PROBE_JS: Final = (
    "(() => { const hit = Array.from(document.querySelectorAll('"
    + _MENU_SELECTOR
    + "')).find((el) => /attach folder/i.test(el.textContent || ''));"
    " if (!hit) return null; const r = hit.getBoundingClientRect();"
    " return { text: (hit.textContent || '').trim().slice(0, 160),"
    " visible: r.width > 0 && r.height > 0,"
    " enabled: !hit.hasAttribute('disabled') }; })()"
)


def _probe_row(session: DriverSession) -> Any:
    """Is an 'Attach Folder' row reachable in whatever menu is open now?"""

    return session.evaluate(_ROW_PROBE_JS)


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
        session = DriverSession(name="filesystem-access-fs2-attach-folder")
        found: dict[str, Any] = {}
        try:
            with session:
                session.sign_in_local()
                session.ftue_add_key(provider, key)
                session.shot("fs2-01-ready")

                assert session.wait_for(PLUS, timeout_s=45), (
                    "composer plus trigger never appeared"
                )
                session.click(PLUS)
                session.shot("fs2-02-plus-menu-open")

                # Read every row the menu offers, by its visible title.
                found["ftue_rows"] = session.evaluate(_ROW_TITLES_JS)
                found["ftue_attach_folder"] = _probe_row(session)

                # The FTUE composer and the Run composer are DIFFERENT mounts.
                # The desktop host wires RunComposer, so an absent row on the
                # onboarding screen does not settle the question — leave FTUE
                # and ask the same question of the composer a user spends its
                # actual session in.
                session.press("body", "Escape")
                session.send_first_run_message(
                    "Say READY and nothing else. Do not call any tool."
                )
                assert session.wait_for(PLUS, timeout_s=90), (
                    "run composer plus trigger never appeared"
                )
                session.shot("fs2-03-run-composer")
                session.click(PLUS)
                session.shot("fs2-04-run-composer-menu-open")
                found["run_attach_folder"] = _probe_row(session)
                found["run_rows"] = session.evaluate(_ROW_TITLES_JS)
                found["attach_folder_present"] = (
                    found["ftue_attach_folder"] or found["run_attach_folder"]
                )
        finally:
            out = session.run_dir / "fs2-evidence.json"
            out.write_text(
                json.dumps(found, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"[fs2] evidence -> {out}", flush=True)
            print(f"[fs2] shots    -> {session.run_dir}", flush=True)

    if not found.get("attach_folder_present"):
        _result(
            "FAILED",
            reason="Attach Folder is unreachable in BOTH the FTUE and Run composers",
            ftue_rows=len(found.get("ftue_rows") or []),
            run_rows=len(found.get("run_rows") or []),
        )
        return 1
    _result(
        "passed",
        ftue=bool(found.get("ftue_attach_folder")),
        run=bool(found.get("run_attach_folder")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
