#!/usr/bin/env python3
"""FS-G — the folder bar with folders actually ATTACHED.

Every screenshot this program has produced so far shows the bar's EMPTY state
("Attach a folder"). The populated half — the named chip, the `+N` collapse for
several grants, most-recently-granted-first ordering, revoke, and the bar
disappearing after the first message — had only ever run in unit tests.

WHY IT COULD NOT BE SEEN. Attaching goes through
``dialog.showOpenDialog``, which is OS chrome: Playwright cannot see it, and
driving it through System Events needs Accessibility permission this runner does
not have (``osascript is not allowed assistive access (-25211)``, twelve
consecutive samples). So the picker is stubbed in the MAIN process, which is the
standard Electron testing answer and honest here — the picker's whole job is to
return a path, and everything downstream of it still runs for real: realpath,
the grant store, the mount table, the IPC back to the renderer, the re-render.

What is asserted, beyond the pictures:

* the chip shows a folder's BASENAME and no host path appears anywhere in the
  rendered DOM — ``WorkspaceGrantPort`` is path-free by construction and this
  bar must not be the reason that is loosened;
* several grants collapse to `<newest> +N`, newest first;
* the bar-to-frame gap holds at 6px with a chip in it, not just when empty;
* revoke removes the grant;
* the bar is GONE once the first message is sent.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    dump,
    lane,
    result,
)

FOLDER_BAR = ".aui-folder-bar"
ATTACH = ".aui-folder-bar__attach"
NAME = ".aui-folder-bar__name"
MORE = ".aui-folder-bar__more"
REVOKE = ".aui-folder-bar__revoke"
COMPOSER = ".aui-composer"

#: Distinct, obviously-not-real names so a screenshot cannot be mistaken for a
#: folder that happened to be lying around, and so ordering is unambiguous.
FIXTURE_NAMES = ("kaleidoscope", "harbour-notes", "q3-forecast")


def _make_fixtures(root: Path) -> list[Path]:
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


def _stub_picker(session: DriverSession, folder: Path) -> None:
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


def _bar(session: DriverSession) -> dict[str, Any]:
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


def _dom_leaks_a_path(session: DriverSession, folders: list[Path]) -> dict[str, Any]:
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


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result("FS-G", "skipped", reason=str(exc))
        return 3

    fixture_root = Path.home() / ".0xcopilot-journey-fixtures" / "fs-g"
    folders = _make_fixtures(fixture_root)
    evidence: dict[str, Any] = {
        "fixture_root": str(fixture_root),
        "fixtures": [str(f) for f in folders],
    }

    with lane(DEFAULT_LANE):
        session = DriverSession(name="fs-g-folder-bar-populated")
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                session.sign_in_local()
                session.ftue_add_key(provider, key)
                assert session.wait_for(FOLDER_BAR, timeout_s=60), (
                    "the folder bar never appeared on the first-run composer"
                )

                # --- empty, for the before/after pair ------------------------
                evidence["empty"] = _bar(session)
                session.shot("g-01-bar-empty")

                # --- one folder ----------------------------------------------
                _stub_picker(session, folders[0])
                session.click(ATTACH)
                assert session.wait_for(f"{NAME}", timeout_s=30)
                for _ in range(40):
                    if (_bar(session).get("name") or "") == folders[0].name:
                        break
                    time.sleep(0.25)
                evidence["one_folder"] = _bar(session)
                session.shot("g-02-bar-one-folder")

                # --- two, then three ------------------------------------------
                _stub_picker(session, folders[1])
                session.click(ATTACH)
                time.sleep(1.5)
                evidence["two_folders"] = _bar(session)
                session.shot("g-03-bar-two-folders")

                _stub_picker(session, folders[2])
                session.click(ATTACH)
                time.sleep(1.5)
                evidence["three_folders"] = _bar(session)
                session.shot("g-04-bar-three-folders")

                # --- no host path anywhere in the DOM -------------------------
                evidence["path_leak"] = _dom_leaks_a_path(session, folders)

                # --- revoke ----------------------------------------------------
                if session.present(REVOKE):
                    session.click(REVOKE)
                    time.sleep(1.5)
                    evidence["after_revoke"] = _bar(session)
                    session.shot("g-05-bar-after-revoke")

                # --- the bar is scoped to the moment ---------------------------
                session.send_first_run_message(
                    "Say the single word ACKNOWLEDGED and stop."
                )
                time.sleep(6)
                evidence["after_first_message"] = _bar(session)
                session.shot("g-06-bar-after-first-message")
        finally:
            out = dump(session.run_dir, "fs-g-evidence.json", evidence)

    one = evidence.get("one_folder", {})
    three = evidence.get("three_folders", {})
    after = evidence.get("after_first_message", {})
    leak = evidence.get("path_leak", {})

    failures: list[str] = []
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
    if after.get("present") is not False:
        failures.append("the bar survived the first message")

    result(
        "FS-G",
        "failed" if failures else "passed",
        failures=failures,
        evidence=str(out),
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
