#!/usr/bin/env python3
"""focus-inline-artifacts — an artifact is readable WITHOUT leaving Focus mode.

Focus used to answer "an artifact exists" with a pinned card above the
transcript, titled by KIND ("document artifact") rather than by the filename the
user chose, whose only action left the mode entirely. This drives the
replacement in the real packaged app: the card renders IN the thread, collapsed,
and expands in place into the same `ArtifactSurface` Studio mounts.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import SOURCE_TARGET, DriverSession  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generative-workflows"))
from g2_csv_lifecycle import (  # noqa: E402
    PreflightSkip,
    _assert_no_plaintext_secret,
    _byok_provider,
    _journey_environment,
    _preflight_staged_runtime,
    _wait_for_conversation_id,
    _wait_for_new_run,
    _wait_for_terminal_run,
)

CREATE_PROMPT = """Create exactly two reviewable artifacts in Studio, then stop.

1. A CSV dataset named `bookings-forecast.csv` with exact content:
```csv
month,new_bookings,renewals
2026-09,120000,84000
2026-10,135000,91000
2026-11,148000,96000
```
2. A Markdown document named `forecast-notes.md` with exact content:
```markdown
# Forecast notes

Assumes renewals hold at the trailing three-month average.
```

Publish both artifacts. Do not stage or write workspace files."""


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "focus-inline-artifacts", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def _read_focus(session: DriverSession) -> dict:
    js = """(() => {
      const cards = [...document.querySelectorAll('[data-testid=tc-inline-artifact]')];
      return {
        mode: (document.querySelector('[data-testid=thread-canvas]') || {}).getAttribute
          ? document.querySelector('[data-testid=thread-canvas]').getAttribute('data-mode')
          : null,
        inlineCards: cards.map((c) => ({
          id: c.getAttribute('data-artifact-id'),
          kind: c.getAttribute('data-artifact-kind'),
          hue: c.getAttribute('data-surface-hue'),
          open: c.getAttribute('data-open'),
          name: (c.querySelector('.tc-inline-artifact__name') || {}).textContent || null,
          toggle: (c.querySelector('[data-testid=tc-inline-artifact-toggle]') || {}).textContent || null,
        })),
        pinnedFocusCards: [...document.querySelectorAll('[data-testid=canvas-focus-card]')]
          .map((c) => (c.querySelector('h2') || {}).textContent || null),
        tabStrip: !!document.querySelector('[data-testid=tc-tabs]'),
        artifactFrames: document.querySelectorAll('[data-testid=artifact-frame]').length,
        loadingFrames: document.querySelectorAll('[data-testid=artifact-loading]').length,
      };
    })()"""
    return session.evaluate(js) or {}


def main() -> int:
    try:
        _preflight_staged_runtime(target=SOURCE_TARGET)
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"focus inline artifacts; provider={provider}")
    with _journey_environment():
        session = DriverSession(name="focus-inline-artifacts")
        completed = False
        try:
            with session:
                session.sign_in_local()
                session.ftue_add_key(provider, key)
                session.send_first_run_message(CREATE_PROMPT)

                conversation_id = _wait_for_conversation_id(session)
                run_id = _wait_for_new_run(session, conversation_id, 0)
                _wait_for_terminal_run(session, run_id)

                assert session.wait_for("[data-testid=tc-inline-artifact]", 90), (
                    "no inline artifact card ever rendered in the transcript"
                )
                # Focus is the mode this feature is for. The cockpit may open in
                # Studio, so switch deliberately rather than assuming.
                if session.run_mode() != "focus":
                    session.click("[data-testid=run-mode-focus]")
                    time.sleep(1.5)
                time.sleep(1.5)
                collapsed = _read_focus(session)
                session.shot("01-focus-collapsed-inline-cards")

                cards = collapsed.get("inlineCards") or []
                assert len(cards) >= 2, (
                    f"expected two inline cards, saw {len(cards)}: {cards}"
                )
                # The reported defect: cards titled by KIND, not by filename.
                names = [c.get("name") or "" for c in cards]
                assert not any(n.endswith(" artifact") for n in names), (
                    f"an inline card is still titled by kind, not filename: {names}"
                )
                assert all(c.get("open") == "false" for c in cards), (
                    "an artifact auto-expanded; minimized is the default"
                )
                # The pinned band must no longer carry artifacts.
                assert not any(
                    (t or "").endswith(" artifact")
                    for t in (collapsed.get("pinnedFocusCards") or [])
                ), f"a pinned kind-labelled card survived: {collapsed}"

                # Expand in place — the whole point: no mode switch to read it.
                session.click("[data-testid=tc-inline-artifact-toggle]")
                assert session.wait_for("[data-testid=artifact-frame]", 60), (
                    "expanding an inline card did not render the artifact"
                )
                time.sleep(1.5)
                expanded = _read_focus(session)
                session.shot("02-focus-expanded-in-place")

                assert expanded.get("mode") == "focus", (
                    f"expanding changed the mode to {expanded.get('mode')!r}; "
                    "reading an artifact must not leave Focus"
                )
                assert expanded.get("artifactFrames", 0) >= 1, (
                    f"no artifact frame after expanding: {expanded}"
                )
                print(
                    json.dumps(
                        {"collapsed": collapsed, "expanded": expanded},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                completed = True
        finally:
            _assert_no_plaintext_secret(key, (session.run_dir, session._user_data_dir))

    if completed:
        _result("passed")
        return 0
    _result("failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
