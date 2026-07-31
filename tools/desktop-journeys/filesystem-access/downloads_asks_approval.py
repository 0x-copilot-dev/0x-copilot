#!/usr/bin/env python3
"""FS3 — asking for ~/Downloads must produce an APPROVAL, not an empty listing.

The demo of the whole capability: a user says "read my Downloads", the agent has
no grant covering it, and the honest outcome is a consent request naming the
folder — the same mechanism `auth_mcp` / `suggest_mcp_connector` park on.

Before the broker env-name fix this run produced `ls -> []` with a green tick in
479 ms and no approval at all, because `WorkspaceBackendConfig.from_env` read
`DESKTOP_BROKER_URL` while the supervisor forwards
`DESKTOP_WORKSPACE_BROKER_URL`, so no `/workspace/` route was ever composed.

Privacy: this lists the real Downloads because that is the demo, but it asserts
and records only COUNTS and the approval's own presence — no personal filename
is written to the evidence file. Screenshots may show the approval card, which
names the folder, not its contents.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generative-workflows"))
from g2_csv_lifecycle import (  # noqa: E402
    PreflightSkip,
    _byok_provider,
    _events,
    _preflight_staged_runtime,
    _wait_for_conversation_id,
    _wait_for_new_run,
)

from ungranted_path_asks import _journey_environment  # noqa: E402

DOWNLOADS: Final = Path.home() / "Downloads"

#: Any of these on screen means the user was asked rather than lied to.
_APPROVAL_JS: Final = """(() => {
  const hit = Array.from(document.querySelectorAll('*')).find((el) =>
    /grant|allow access|approve|permission|share this folder/i.test(
      (el.textContent || '')) && el.children.length < 12);
  if (!hit) return null;
  const r = hit.getBoundingClientRect();
  return { text: (hit.textContent || '').trim().slice(0, 240),
           visible: r.width > 0 && r.height > 0 };
})()"""


def _result(outcome: str, **extra: Any) -> None:
    print(
        json.dumps({"journey": "FS3", "outcome": outcome, **extra}, sort_keys=True),
        flush=True,
    )


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", reason=str(exc))
        return 0
    if not DOWNLOADS.is_dir():
        _result("skipped", reason="no ~/Downloads on this host")
        return 0

    on_disk = len(list(DOWNLOADS.iterdir()))
    _result("running", provider=provider, downloads_entries_on_disk=on_disk)

    with _journey_environment():
        session = DriverSession(name="filesystem-access-fs3-downloads")
        ev: dict[str, Any] = {"downloads_entries_on_disk": on_disk}
        try:
            with session:
                session.sign_in_local()
                session.ftue_add_key(provider, key)
                session.send_first_run_message(
                    f"List the files in {DOWNLOADS}. If you need permission, ask."
                )
                session.shot("fs3-01-asked-for-downloads")

                conversation_id = _wait_for_conversation_id(session)
                run_id = _wait_for_new_run(session, conversation_id, 0)
                ev["run_id"] = run_id

                # Watch for the consent surface while the run is live: a blocking
                # grant request parks the run, so waiting for a TERMINAL run
                # first would wait forever on the very outcome we want.
                approval = None
                for _ in range(90):
                    approval = session.evaluate(_APPROVAL_JS)
                    if approval:
                        break
                    time.sleep(1.0)
                ev["approval_on_screen"] = approval
                session.shot("fs3-02-approval-or-not")

                events = _events(session, run_id)
                ev["event_count"] = len(events)
                ev["approval_events"] = [
                    str(e.get("event_type"))
                    for e in events
                    if isinstance(e, dict)
                    and "approval" in str(e.get("event_type", ""))
                ]
                ev["tool_names"] = sorted(
                    {
                        str((e.get("payload") or {}).get("tool_name"))
                        for e in events
                        if isinstance(e, dict)
                        and isinstance(e.get("payload"), dict)
                        and (e.get("payload") or {}).get("tool_name")
                    }
                )
                session.shot("fs3-03-final")
        finally:
            out = session.run_dir / "fs3-evidence.json"
            out.write_text(json.dumps(ev, indent=2, sort_keys=True), encoding="utf-8")
            print(f"[fs3] evidence -> {out}", flush=True)
            print(f"[fs3] shots    -> {session.run_dir}", flush=True)

    asked = bool(ev.get("approval_on_screen")) or bool(ev.get("approval_events"))
    _result(
        "passed" if asked else "FAILED",
        asked_for_approval=asked,
        approval_events=ev.get("approval_events"),
        tools=ev.get("tool_names"),
    )
    return 0 if asked else 1


if __name__ == "__main__":
    raise SystemExit(main())
