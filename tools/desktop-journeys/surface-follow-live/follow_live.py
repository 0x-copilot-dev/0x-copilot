#!/usr/bin/env python3
"""surface-follow-live — switching between finished artifacts raises no alert.

Publishes two artifacts, waits for the run to SEAL, then clicks the older tab —
the exact user action that used to raise a full-bleed
`PINNED TO <TAB> · THE RUN HAS MOVED ON` banner offering to follow a stream that
had already ended. See JOURNEYS.md for why the unit test that covered this
interaction passed anyway.
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

# Two artifacts, deliberately of different kinds so they take different tab hues
# and the strip is visibly a strip. Mirrors the reported scenario (a forecast
# table plus a second document published in the same run).
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

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timed_out"}


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "surface-follow-live", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def _read_strip(session: DriverSession) -> dict:
    """One page evaluation. Every field is read from the live DOM, not a fixture."""

    js = """(() => {
      const strip = document.querySelector('[data-testid=tc-tabs]');
      const tabs = [...document.querySelectorAll('[data-testid=tc-tabs] [role=tab]')];
      return {
        stripHeight: strip ? Math.round(strip.getBoundingClientRect().height) : null,
        tabs: tabs.map((t) => ({
          uri: t.getAttribute('data-uri'),
          title: (t.querySelector('.tc-tab__title') || {}).textContent || null,
          active: t.getAttribute('data-active'),
          pinned: t.getAttribute('data-pinned'),
          live: t.getAttribute('data-live'),
          hue: t.getAttribute('data-surface-hue'),
          hasClose: !!t.querySelector('.tc-tab__close'),
          hasPinGlyph: !!t.querySelector('.tc-tab__pin'),
        })),
        followLiveChip: !!document.querySelector('[data-testid=tc-tabs-follow-live]'),
        followLiveBanner: !!document.querySelector('[data-testid=run-follow-live-banner]'),
        scrubBanner: !!document.querySelector('[data-testid=run-viewing-banner]'),
      };
    })()"""
    return session.evaluate(js) or {}


def _assert_no_alert(before: dict, after: dict, clicked_uri: str) -> None:
    tabs = after.get("tabs") or []

    # 3. Switching still works — the whole point is that this is now ordinary.
    active = [t for t in tabs if t.get("active") == "true"]
    assert len(active) == 1, f"expected exactly one active tab, got {len(active)}"
    assert active[0]["uri"] == clicked_uri, (
        f"clicked {clicked_uri!r} but {active[0]['uri']!r} is active"
    )

    # 4-5. The reported defect, and its chip-shaped replacement, are both absent.
    assert not after["followLiveBanner"], (
        "the full-bleed 'PINNED TO … · THE RUN HAS MOVED ON' banner is back on a "
        "TERMINAL run — it is offering to follow a stream that has ended"
    )
    assert not after["followLiveChip"], (
        "the follow-live chip rendered on a terminal run; there is no live tail "
        "to resume following"
    )

    # 6-7. No pin chrome at all: the glyph is the release control, so rendering
    #      it while the chip is (correctly) suppressed is a dead button on a tab
    #      that has also lost its only close affordance.
    pinned = [t for t in tabs if t.get("pinned") == "true"]
    assert not pinned, (
        f"tab(s) {[t['uri'] for t in pinned]} report a pin on a terminal run; "
        "a pin describes a paused auto-follow, and nothing is following"
    )
    glyphs = [t for t in tabs if t.get("hasPinGlyph")]
    assert not glyphs, (
        f"pin glyph rendered on {[t['uri'] for t in glyphs]} with no chip beside "
        "it — that button's onFollowLive is undefined"
    )
    closeless = [t for t in tabs if not t.get("hasClose")]
    assert not closeless, (
        f"tab(s) {[t['uri'] for t in closeless]} have no close button; the pin "
        "swallowed it"
    )

    # 8. A terminal run cannot land work anywhere, so nothing may pulse.
    live = [t for t in tabs if t.get("live") == "true"]
    assert not live, (
        f"tab(s) {[t['uri'] for t in live]} still show the live pulse after the "
        "run reached a terminal status"
    )

    # 9. The measured cost of the old banner: a plain tab click moved the canvas.
    assert before["stripHeight"] == after["stripHeight"], (
        f"the strip changed height on a tab click "
        f"({before['stripHeight']}px → {after['stripHeight']}px); everything "
        "below it just reflowed"
    )


def main() -> int:
    try:
        _preflight_staged_runtime(target=SOURCE_TARGET)
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"follow-live suppression; provider={provider}")
    with _journey_environment():
        session = DriverSession(name="surface-follow-live")
        completed = False
        try:
            with session:
                status = session.rpc("status")
                assert status.get("posture") == "prod", (
                    f"expected production posture, got {status.get('posture')!r}"
                )

                session.sign_in_local()
                session.ftue_add_key(provider, key)
                session.send_first_run_message(CREATE_PROMPT)

                conversation_id = _wait_for_conversation_id(session)
                run_id = _wait_for_new_run(session, conversation_id, 0)
                run = _wait_for_terminal_run(session, run_id)

                # 1. Without a terminal run this journey proves nothing at all.
                run_status = str(run.get("status", ""))
                assert run_status in TERMINAL_STATUSES, (
                    f"run never reached a terminal status (status={run_status!r}); "
                    "the whole journey is about what happens AFTER the tail ends"
                )

                # 2. Two artifacts ⇒ an older tab exists to switch to.
                assert session.wait_for("[data-testid=tc-tabs] [role=tab]", 90), (
                    "no surface tab ever appeared; the run published nothing"
                )
                deadline = time.time() + 60
                before = _read_strip(session)
                while time.time() < deadline and len(before.get("tabs") or []) < 2:
                    time.sleep(1)
                    before = _read_strip(session)
                session.shot("01-terminal-run-newest-tab")

                tabs = before.get("tabs") or []
                assert len(tabs) >= 2, (
                    f"need two surfaces to switch between, the run produced "
                    f"{len(tabs)}: {[t.get('title') for t in tabs]}"
                )

                # 4. The user action from the report: click the OLDER tab. The
                #    strip is newest-first, so anything past index 0 is older.
                older = tabs[1]
                session.click(
                    f'[data-testid=tc-tabs] [role=tab][data-uri="{older["uri"]}"]'
                )
                time.sleep(1.5)
                after = _read_strip(session)
                session.shot("02-older-tab-clicked-no-banner")

                print(
                    json.dumps(
                        {"before": before, "after": after, "runStatus": run_status},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                _assert_no_alert(before, after, older["uri"])
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
