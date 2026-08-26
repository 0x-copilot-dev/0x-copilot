#!/usr/bin/env python3
"""Live proof that a denied tool shows its REAL reason, not a generic sentence.

A `write_file` denied for want of a grant carried
`"Error: permission denied for write on /random.csv"` in `output.content`, while
the card said "0xCopilot couldn't complete this step" — the one line that would
have told the reader they needed a folder grant never reached the screen.

The prompt forces the FILESYSTEM ROOT explicitly. An earlier run let the model
choose, and it picked a writable path and parked on an approval gate instead of
being denied — the code path under test was never reached. Naming `/` removes
that variance: root is ungranted in every lane.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from _lib import DriverSession, byok_provider, preflight_staged_runtime
from _workspace_lib import (
    dump,
    events,
    settle_run,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "error-copy-proof"
PROMPT = (
    "Call write_file exactly once with file_path set to the absolute path "
    "`/random.csv` (the filesystem root, deliberately) and content `hello`. "
    "Do not retry, do not pick a different path, do not call any other tool. "
    "Then reply with just: attempted"
)


def main() -> int:
    provider, key = byok_provider()
    if not key:
        print(f"no {provider} key — cannot run")
        return 2
    preflight_staged_runtime()
    evidence: dict[str, Any] = {}

    with DriverSession(JOURNEY) as s:
        s.sign_in_local()
        s.ftue_add_key(provider, key)
        s.send(PROMPT)
        cid = wait_for_conversation_id(s)
        run_id = wait_for_new_run(s, cid, 0)
        evidence["run_status"] = settle_run(s, run_id).get("status")
        time.sleep(2)
        s.shot("01-denied-tool-card")

        stream = events(s, run_id)
        evidence["wire_error"] = next(
            (
                (e.get("payload") or {}).get("output", {}).get("content")
                for e in stream
                if e.get("event_type") == "tool_result"
                and (e.get("payload") or {}).get("status") in ("failed", "error")
            ),
            None,
        )
        # The card's own error line, read off the live DOM.
        evidence["rendered"] = s.evaluate(
            """
            (() => [...document.querySelectorAll('[data-tool-status=error]')]
              .map((el) => {
                const s = el.querySelector('summary') || el;
                return [...s.querySelectorAll('span')]
                  .map((n) => (n.textContent || '').trim())
                  .filter((t) => t && t.length < 200);
              })
            )()
            """
        )
        dump(s.run_dir, "evidence.json", evidence)

    wire = evidence.get("wire_error") or ""
    flat = " | ".join(t for card in (evidence.get("rendered") or []) for t in card)
    shows_real = "permission denied" in flat.lower()
    shows_generic_only = (
        "couldn't complete this step" in flat.lower()
    ) and not shows_real

    print("\n" + "=" * 62)
    print(f"{JOURNEY} — run={evidence.get('run_status')}")
    print("=" * 62)
    print(f"  wire said     : {wire!r}")
    print(f"  card rendered : {flat[:220]!r}")
    print(f"  [{'PASS' if shows_real else 'FAIL'}] the card shows the real reason")
    print(
        f"  [{'PASS' if not shows_generic_only else 'FAIL'}] it is not ONLY the generic sentence"
    )
    return 0 if shows_real else 1


if __name__ == "__main__":
    sys.exit(main())
