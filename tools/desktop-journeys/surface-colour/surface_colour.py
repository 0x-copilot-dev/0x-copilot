#!/usr/bin/env python3
"""surface-colour — the Studio's identity colour, measured in the real app.

Reuses G2's supervised CSV lifecycle to get a published dataset onto the canvas,
then reads `getComputedStyle` out of the live DOM. See JOURNEYS.md for why this
cannot be a unit test: every layer of the colour system has already produced a
defect that a passing unit test walked straight past.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generative-workflows"))
from g2_csv_lifecycle import (  # noqa: E402
    CREATE_PROMPT,
    PreflightSkip,
    _assert_no_plaintext_secret,
    _byok_provider,
    _events,
    _journey_environment,
    _preflight_staged_runtime,
    _wait_for_conversation_id,
    _wait_for_new_run,
    _wait_for_terminal_run,
)

# The ring is oklch(0.76 0.1 H) on the dark ground. A neutral is either a grey
# rgb() or an oklch with ~zero chroma; either way it is NOT an identity colour.
_OKLCH = re.compile(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)")


def _probe_artifact(session: DriverSession) -> str:
    """Ask the app for the open tab's artifact detail, and report the outcome."""

    uri = session.evaluate(
        "document.querySelector('[data-testid=tc-tabs] [role=tab]')"
        "?.getAttribute('data-uri') ?? ''"
    )
    match = re.search(r"artifact-[a-z]+://([^@]+)@", uri or "")
    if match is None:
        return f"no artifact uri on the tab (uri={uri!r})"
    try:
        detail = session.transport("GET", f"/v1/agent/artifacts/{match.group(1)}")
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return f"detail request failed: {exc}"
    artifact = detail.get("artifact") if isinstance(detail, dict) else None
    if not isinstance(artifact, dict):
        return f"unexpected detail shape: {str(detail)[:160]}"
    return (
        f"detail ok: revision={artifact.get('current_revision')} "
        f"media_type={artifact.get('media_type')} accent={artifact.get('accent')!r}"
    )


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "surface-colour", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def _is_identity_colour(value: str) -> bool:
    """True when a computed colour carries real chroma, i.e. it is a hue."""

    match = _OKLCH.search(value or "")
    if match is None:
        return False
    return float(match.group(2)) > 0.02


def _read_surface_colour(session: DriverSession) -> dict:
    """One page evaluation; every number below is a computed style, not a token."""

    js = """(() => {
      const out = {};
      const tab = document.querySelector('[data-testid=tc-tabs] [role=tab][data-active="true"]')
               ?? document.querySelector('[data-testid=tc-tabs] [role=tab]');
      if (tab) {
        out.tabHue = tab.getAttribute('data-surface-hue');
        const dot = tab.querySelector('.tc-tab__dot');
        if (dot) {
          const cs = getComputedStyle(dot);
          out.tabDotColour = cs.backgroundColor;
          out.tabDotOpacity = cs.opacity;
        }
        out.tabTitle = tab.querySelector('.tc-tab__title')?.textContent ?? null;
        out.tabHeight = getComputedStyle(tab).height;
        out.tabRadius = getComputedStyle(tab).borderTopLeftRadius;
      }
      const mount = document.querySelector('[data-testid=tc-surface-mount]');
      if (mount) out.mountHue = mount.getAttribute('data-surface-hue');
      out.cardTitle =
        document.querySelector('[data-testid=artifact-frame] h1, [data-testid=artifact-frame] h2, [data-testid=artifact-frame] h3')
          ?.textContent ?? null;

      const heads = [...document.querySelectorAll('.ui-dataset-table__header')];
      out.headerCount = heads.length;
      // Measure the node that actually PAINTS. The editable grid puts an
      // <input> in every header cell, and the <th> keeps its own muted colour —
      // reading the <th> there reports grey no matter what the rule does.
      const painted = (th) =>
        th.querySelector('.ui-dataset-cell-input--header') ?? th;
      const numeric = heads.filter((h) => h.classList.contains('sf-col--numeric'));
      out.numericHeaderCount = numeric.length;
      out.gridEditable = !!document.querySelector('.ui-dataset-table--editable');
      if (numeric[0]) {
        out.numericHeaderColour = getComputedStyle(painted(numeric[0])).color;
      }
      const text = heads.find((h) => !h.classList.contains('sf-col--numeric'));
      if (text) out.textHeaderColour = getComputedStyle(painted(text)).color;

      const bars = [...document.querySelectorAll('.sf-value-bar')];
      out.barCount = bars.length;
      out.barsAriaHidden = bars.every((b) => b.getAttribute('aria-hidden') === 'true');
      if (bars[0]) out.barColour = getComputedStyle(bars[0]).backgroundColor;
      out.barWidths = bars.slice(0, 12).map((b) => Math.round(b.getBoundingClientRect().width));
      return out;
    })()"""
    return session.evaluate(js) or {}


def _assert_colour(observed: dict, canvas_accent: str | None) -> None:
    # 1-2. The tab carries an identity, and it renders as a real hue.
    assert observed.get("tabHue"), "canvas tab carries no data-surface-hue"
    expected = canvas_accent or "sky"
    assert observed["tabHue"] == expected, (
        f"tab hue is {observed['tabHue']!r}; the artifact-dataset default is "
        f"'sky' and the record's accent is {canvas_accent!r}"
    )
    dot = observed.get("tabDotColour", "")
    assert _is_identity_colour(dot), (
        f"tab dot computed to {dot!r} — a neutral, not an identity hue. This is "
        "the 'only black/grey/white' symptom in the shipped app."
    )

    # 3b. The tab must name the artifact, not its kind. A tab reading
    #     "dataset artifact" beside a header reading "forecast.csv" is the same
    #     merge defect as the accent, and the one a user actually notices.
    tab_title = observed.get("tabTitle") or ""
    card_title = observed.get("cardTitle") or ""
    assert not tab_title.startswith("dataset artifact"), (
        f"tab shows the synthesized kind label {tab_title!r}; the record's title "
        f"({card_title!r}) never reached it"
    )
    if card_title:
        assert card_title.split(".")[0][:8] in tab_title, (
            f"tab {tab_title!r} and surface header {card_title!r} disagree"
        )

    # 3. Tab and card must not disagree about what they are showing.
    assert observed.get("mountHue") == observed["tabHue"], (
        f"surface mount hue {observed.get('mountHue')!r} disagrees with the "
        f"tab's {observed['tabHue']!r}"
    )

    # 4. The assertion that catches the inert-rule defect. A class alone proves
    #    nothing: the renderer composes inline styles, which outrank stylesheet
    #    rules, so only a measured DIFFERENCE shows the rule actually applied.
    assert observed.get("numericHeaderCount", 0) >= 1, (
        "no numeric column was detected in the published CSV; expected at least "
        f"one of {observed.get('headerCount')} headers to carry sf-col--numeric"
    )
    numeric_colour = observed.get("numericHeaderColour", "")
    text_colour = observed.get("textHeaderColour", "")
    assert numeric_colour and text_colour, "could not read both header colours"
    assert numeric_colour != text_colour, (
        f"numeric and text headers both computed to {numeric_colour!r} — "
        ".sf-col--numeric is inert (an inline `color` is outranking it)"
    )
    assert _is_identity_colour(numeric_colour), (
        f"numeric header computed to {numeric_colour!r}, which carries no hue"
    )

    # 5. Value bars: present, decorative, and actually ordered by magnitude.
    assert observed.get("barCount", 0) >= 2, (
        f"expected value bars behind numeric cells, found {observed.get('barCount')}"
    )
    assert observed.get("barsAriaHidden") is True, (
        "a value bar is exposed to assistive tech; it restates the number and "
        "must stay decorative"
    )
    assert _is_identity_colour(observed.get("barColour", "")), (
        f"value bar computed to {observed.get('barColour')!r} — no hue"
    )
    widths = observed.get("barWidths") or []
    assert len(set(widths)) > 1, (
        f"every value bar is the same width ({widths}); the bars are not encoding "
        "magnitude"
    )


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"surface colour; provider={provider}")
    with _journey_environment():
        session = DriverSession(name="surface-colour")
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
                _wait_for_terminal_run(session, run_id)

                if not session.wait_for("[data-testid=artifact-dataset-renderer]", 90):
                    # Self-diagnosing on the most likely failure: the run may have
                    # answered in chat without publishing, or the canvas may have
                    # presented something else. Report WHICH, so a red run names
                    # its cause instead of only its symptom.
                    session.shot("no-dataset-diagnostic")
                    diagnostic = {
                        "lifecycle": session.evaluate(
                            "document.querySelector('[data-testid=canvas-lifecycle-panel]')"
                            "?.getAttribute('data-lifecycle') ?? 'absent'"
                        ),
                        "tabs": session.evaluate(
                            "[...document.querySelectorAll('[data-testid=tc-tabs] [role=tab]')]"
                            ".map(t => t.getAttribute('data-uri'))"
                        ),
                        "mountTier": session.evaluate(
                            "document.querySelector('[data-testid=tc-surface-mount]')"
                            "?.getAttribute('data-tier') ?? 'absent'"
                        ),
                        "artifactEvents": [
                            e.get("event_type")
                            for e in _events(session, run_id)
                            if str(e.get("event_type", "")).startswith("artifact.")
                        ],
                        # "Loading artifact…" on screen means the CONTENT fetch
                        # never resolved. Probe the same endpoint the surface
                        # uses, so the report distinguishes "renderer broke"
                        # from "the artifact never arrived".
                        "frameText": session.evaluate(
                            "document.querySelector('[data-testid=artifact-frame]')"
                            "?.textContent?.slice(0, 120) ?? 'absent'"
                        ),
                        "artifactProbe": _probe_artifact(session),
                    }
                    raise AssertionError(
                        "Studio never presented the dataset for the published CSV; "
                        f"diagnostic={json.dumps(diagnostic, sort_keys=True)}"
                    )

                # 6. The accent's wire contract, read through the app itself.
                canvas = session.transport(
                    "GET", f"/v1/agent/conversations/{conversation_id}/canvas"
                )
                subjects = canvas.get("subjects") or []
                assert subjects, "conversation canvas returned no subjects"
                artifact = next(
                    (s for s in subjects if s.get("kind") == "artifact"), None
                )
                assert artifact is not None, "no artifact subject on the canvas"
                assert "accent" in artifact, (
                    "the canvas subject has no `accent` field — the seam a "
                    "publish_artifact colour travels through is missing on the wire"
                )
                canvas_accent = artifact.get("accent")

                observed = _read_surface_colour(session)
                session.shot("surface-colour-canvas")
                print(
                    json.dumps(
                        {
                            "observed": observed,
                            "canvas_accent": canvas_accent,
                            "canvas_subjects": [
                                {
                                    k: s.get(k)
                                    for k in (
                                        "subject_key",
                                        "title",
                                        "accent",
                                        "revision",
                                        "renderer_hint",
                                    )
                                }
                                for s in subjects
                            ],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                _assert_colour(observed, canvas_accent)
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
