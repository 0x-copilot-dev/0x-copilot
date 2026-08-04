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


def _canvas_state(session: DriverSession) -> str:
    """Which terminal state the artifact canvas is in, or `loading`.

    `ArtifactFrame` renders exactly one of these testids, so this is the whole
    state machine and not a heuristic.
    """

    return (
        session.evaluate(
            "(() => {"
            "for (const id of ['artifact-frame','artifact-error','artifact-deleted','artifact-loading'])"
            "  if (document.querySelector('[data-testid='+id+']')) return id;"
            "return 'absent';"
            "})()"
        )
        or "absent"
    )


def _wait_for_canvas_settled(session: DriverSession, timeout_s: int = 60) -> str:
    """Poll until the canvas leaves `artifact-loading`, and report where it went.

    A screenshot taken before this settles records "Loading artifact…" and looks
    exactly like a broken content fetch. That misread cost a round trip here, so
    the wait is explicit and its outcome is asserted rather than assumed.
    """

    deadline = time.time() + timeout_s
    state = _canvas_state(session)
    while time.time() < deadline and state in {"artifact-loading", "absent"}:
        time.sleep(0.5)
        state = _canvas_state(session)
    return state


def _probe_canvas(session: DriverSession) -> dict:
    """Ask the app itself which half of the artifact fetch is stuck.

    `useArtifactSurface` sets `status` to ready/error/deleted on EVERY settled
    branch, so a canvas parked on `artifact-loading` means a promise never
    settled at all. There are only two awaits that can do that: the metadata
    GET, and the artifact-content IPC stream. This drives both, each behind a
    timeout, so the report names which one hangs instead of describing the
    symptom again.
    """

    js = """(async () => {
      const out = {};
      const tab = document.querySelector('[data-testid=tc-tabs] [role=tab][data-active="true"]');
      const uri = tab ? tab.getAttribute('data-uri') : null;
      out.activeUri = uri;
      const m = /^artifact-([a-z]+):\\/\\/([^@]+)@([0-9]+)$/.exec(uri || '');
      if (!m) { out.parse = 'FAILED'; return JSON.stringify(out); }
      out.kind = m[1]; out.artifactId = m[2]; out.revision = Number(m[3]);

      const withTimeout = (p, ms, label) => Promise.race([
        p.then((v) => ({ ok: true, value: v })).catch((e) => ({ ok: false, error: String(e && e.message || e) })),
        new Promise((res) => setTimeout(() => res({ ok: false, error: 'TIMEOUT after ' + ms + 'ms', hung: true, label }), ms)),
      ]);

      const meta = await withTimeout(
        window.bridge.ipc.invoke('transport.request', {
          method: 'GET', path: '/v1/agent/artifacts/' + encodeURIComponent(m[2]),
        }), 8000, 'metadata');
      out.metadata = meta.hung ? 'HUNG' : (meta.ok ? 'ok' : 'error: ' + meta.error);
      if (meta.ok && meta.value && meta.value.value) {
        const a = meta.value.value.artifact || {};
        const r = meta.value.value.current_revision || {};
        out.metaKind = a.kind; out.metaMediaType = a.media_type;
        out.metaCurrentRevision = r.revision; out.metaByteSize = r.byte_size;
      }

      const opened = await withTimeout(
        window.bridge.ipc.invoke('transport.artifact-content.open', {
          artifactId: m[2], revision: Number(m[3]),
        }), 8000, 'content-open');
      out.contentOpen = opened.hung ? 'HUNG' : (opened.ok ? 'ok' : 'error: ' + opened.error);
      // The handle is `artifact-stream-N` off a counter the main process bumps
      // on EVERY open. Since this probe's own open is the last one, N reports
      // how many streams the app itself opened first — which separates the two
      // ways a canvas can sit on its spinner: a component that never fetched
      // (N stays ~0) from an effect re-running in a loop (N large).
      if (opened.ok && opened.value) out.streamHandle = opened.value.handle;
      if (opened.ok && opened.value && opened.value.handle) {
        const read = await withTimeout(
          window.bridge.ipc.invoke('transport.artifact-content.read', {
            handle: opened.value.handle,
          }), 8000, 'content-read');
        out.contentRead = read.hung ? 'HUNG' : (read.ok ? ('ok done=' + read.value.done) : 'error: ' + read.error);
      }
      return JSON.stringify(out);
    })()"""
    raw = session.evaluate(js)
    try:
        return json.loads(raw) if isinstance(raw, str) else {"probe": "no result"}
    except Exception:  # noqa: BLE001 — diagnostic only
        return {"probe": f"unparseable: {str(raw)[:200]}"}


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

                # Let the artifact CONTENT resolve before the screenshot. The
                # tab strip is projected from the ledger fold and lands almost
                # immediately; the artifact body is a separate fetch behind it.
                # Shooting between the two records "Loading artifact…" — which
                # is indistinguishable from a broken content fetch in a still
                # image, and was read as exactly that here.
                canvas_before = _wait_for_canvas_settled(session)
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
                canvas_after = _wait_for_canvas_settled(session)
                session.shot("02-older-tab-clicked-no-banner")

                # 10. Switching tabs must actually SHOW the other artifact. The
                #     strip assertions below would all hold over a canvas stuck
                #     on its spinner, so the journey has to say which it saw.
                for label, state in (
                    ("newest tab", canvas_before),
                    ("after switching to the older tab", canvas_after),
                ):
                    if state != "artifact-frame":
                        probe = _probe_canvas(session)
                        raise AssertionError(
                            f"{label}: the canvas settled on {state!r} instead "
                            f"of rendering the artifact; probe="
                            f"{json.dumps(probe, sort_keys=True)}"
                        )

                print(
                    json.dumps(
                        {
                            "before": before,
                            "after": after,
                            "runStatus": run_status,
                            "canvasBefore": canvas_before,
                            "canvasAfter": canvas_after,
                        },
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
