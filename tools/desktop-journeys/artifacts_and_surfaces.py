#!/usr/bin/env python3
"""artifacts-and-surfaces — what Studio draws when the agent authors something.

The big win of grouping these: five originals each paid for their own boot AND
their own dataset publication to assert five different things about the SAME
artifact. Here the dataset is published ONCE (AS-2) and every later phase reads
it.

Ordering is load-bearing. AS-3 asserts the canvas presents the artifact WITHOUT
navigation, so it must run before AS-4 opens it from the Sources rail — once
you have clicked into it, "did it present on its own?" is unanswerable. AS-1
runs first and in its own conversation because it asserts the ABSENCE of every
rich surface, which only means something before anything has published one.

    python3 tools/desktop-journeys/artifacts_and_surfaces.py

Folds in: generative-workflows/{g0_plain_chat, g2a_csv_artifact_surface,
g2b_csv_canvas_autopresent, g2c_canvas_survives_followup,
g2d_artifact_edit_regressions}, surface-colour, surface-follow-live,
surface-floor.

AS-9 needs the loopback fixture MCP server (`surface-floor/fixture_mcp.py`)
listening on 8931; AS-10 needs `local-mailbox/fixture_mcp.py` on 8932. Both
skip rather than fail when nothing is there. Every connector in a desktop
profile needs an OAuth authorization an automated journey must not complete in
the user's name, and without SOME connected MCP server the PRESENT stage never
fires and no surface is ever shaped.

    python3 tools/desktop-journeys/surface-floor/fixture_mcp.py &   # AS-9
    python3 tools/desktop-journeys/local-mailbox/fixture_mcp.py &   # AS-10

AS-9 is the eight-shape surface matrix (the old standalone `floor_e2e.py`,
folded in here): the same three incidents served in eight MCP envelopes, one
real run each, asserting that the projected surface binds the CONNECTOR's own
columns rather than the transport envelope's `ID / Type / Text`. It is by far
the most expensive phase in this file — `SURFACE_FLOOR_SHAPES=1,4` drives a
subset, and is the phase equivalent of `floor_e2e.py --shapes 1,4`.

The provider key is read from services/ai-backend/.env and only ever reaches the
password field — never printed, logged, or written to an artifact.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import time
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from _lib import (
    SOURCE_TARGET,
    ARTIFACT_JOURNEY_ENVIRONMENT,
    SECRET_ENVIRONMENT_NAMES,
    TERMINAL_STATUSES,
    DriverSession,
    JourneyPlan,
    assert_no_plaintext_secret,
    byok_provider,
    preflight_staged_runtime,
    require,
    runs_for_conversation,
    wait_for_conversation_id,
    wait_for_new_run,
    wait_for_terminal_run,
)

STATE: dict[str, Any] = {}


def log(line: str) -> None:
    print(f"  {line}", flush=True)


def new_chat(s: DriverSession) -> str | None:
    """Leave the current conversation for a clean one, and say which one.

    The returned id is the conversation being left, and it matters: the app
    does not clear the route on "New chat", so a caller that sends next MUST
    pass it to `wait_for_conversation_id(excluding=...)` or it can bind the
    conversation it just walked out of. See that helper for the full account.
    """

    left = current_conversation_id(s)
    s.open_destination("Chats")
    assert s.wait_for("[data-testid=chats-new-chat]", 30), "Chats has no New chat"
    s.click("[data-testid=chats-new-chat]")
    assert s.wait_for("[data-testid=run-empty-composer]", 30), (
        "New chat did not open the empty cockpit"
    )
    time.sleep(1)
    return left


def current_conversation_id(s: DriverSession) -> str | None:
    """The conversation the route names right now, or ``None`` if unbound."""

    match = re.fullmatch(
        r"#/convo/([^/?#]+)(?:[?#].*)?", str(s.evaluate("window.location.hash") or "")
    )
    return match.group(1) if match is not None else None


PLAIN_PROMPT = (
    "What is the difference between a Python tuple and a list? "
    "Answer in exactly three concise bullet points from your internal knowledge. "
    "Do not browse, call tools, read files, create artifacts, or make changes."
)


RICH_UI_SELECTORS = {
    "tool card": '[data-testid^="tc-chat-tool-"]',
    "subagent fleet card": '[data-testid^="tc-chat-fleet-"]',
    "surface tab strip": "[data-testid=tc-tabs]",
    "artifact frame": "[data-testid=artifact-frame]",
    "staged-write card": "[data-testid=effect-stage-card]",
    "staged-write approval bar": "[data-testid=tc-approve-bar]",
    "staged draft": "[data-testid=tc-staged-draft]",
    "staged row table": "[data-testid=tc-staged-table]",
    "workspace stage": "[data-testid=tc-workspace-stage]",
    "receipt launcher": "[data-testid=receipt-v2-launch]",
    "receipt surface": "[data-testid=receipt-v2-surface]",
}


FOLLOW_LIVE_CREATE_PROMPT = """Create exactly two reviewable artifacts in Studio, then stop.

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


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    revision: int
    kind: str
    content_ref: str


ARTIFACT_NAME: Final = "forecast.csv"


APPLY_EVENTS: Final = frozenset({"write.applied", "effect.applied"})


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _dataset_artifacts(events: list[dict[str, Any]]) -> list[ArtifactReference]:
    known_kinds: dict[str, str] = {}
    references: list[ArtifactReference] = []
    for event in events:
        if event.get("event_type") not in ARTIFACT_EVENTS:
            continue
        payload = _payload(event)
        artifact_id = payload.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            continue
        kind = payload.get("kind")
        if isinstance(kind, str):
            known_kinds[artifact_id] = kind
        resolved_kind = known_kinds.get(artifact_id)
        if resolved_kind != "dataset":
            continue
        references.append(
            ArtifactReference(
                artifact_id=artifact_id,
                revision=_required_positive_int(payload, "revision"),
                kind=resolved_kind,
                content_ref=_required_text(payload, "content_ref"),
            )
        )
    return references


def _artifact_detail(session: DriverSession, artifact_id: str) -> dict[str, Any]:
    detail = session.transport("GET", f"/v1/agent/artifacts/{artifact_id}")
    assert isinstance(detail, dict), "artifact detail is malformed"
    return detail


def _parse_csv_bytes(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError("CSV artifact is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames
    assert headers is not None and all(headers), "CSV has no complete header row"
    assert len(headers) == len(set(headers)), "CSV has duplicate headers"
    rows = list(reader)
    assert rows, "CSV has no data rows"
    assert all(None not in row for row in rows), "CSV contains malformed extra cells"
    assert all(all(value is not None for value in row.values()) for row in rows), (
        "CSV contains a short row"
    )
    return list(headers), rows


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    assert isinstance(value, str) and value, f"event payload omitted {key}"
    return value


def _required_positive_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    assert isinstance(value, int) and value > 0, f"event payload omitted {key}"
    return value


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


ARTIFACT_EVENTS: Final = frozenset({"artifact.created", "artifact.revised"})


UNRELATED_TOOL_MARKERS: Final = frozenset(
    {
        "web_search",
        "browser",
        "mail",
        "discord",
        "timeline",
        "slack",
        "gmail",
        "twitter",
        "x.com",
    }
)


INITIAL_HEADERS: Final = ("month", "region", "bookings", "forecast")


ARTIFACT_CONTENT_REF: Final = re.compile(
    r"^artifact://(?P<artifact_id>[^/]+)/revisions/(?P<revision>[1-9][0-9]*)$"
)


CREATE_PROMPT: Final = """Create a reviewable CSV dataset artifact named
forecast.csv. It must be a valid UTF-8 RFC-4180-style CSV with exactly these
headers in this order: month,region,bookings,forecast. Include at least three
monthly rows and integer bookings and forecast values. Keep it as an editable
dataset/table in Studio. Do not write any local workspace file, do not stage an
effect, do not browse, and do not use connectors or unrelated tools."""


def run_events(session: DriverSession, run_id: str) -> list[dict[str, Any]]:
    replay = session.transport("GET", f"/v1/agent/runs/{run_id}/events")
    events = replay.get("events", [])
    assert isinstance(events, list), "event replay omitted events"
    assert all(isinstance(event, dict) for event in events), "event replay is malformed"
    return events


def dataset_artifact_from_run(events: list[dict[str, Any]]) -> ArtifactReference:
    artifacts = _dataset_artifacts(events)
    assert artifacts, "agent did not create a dataset artifact"
    return artifacts[-1]


def artifact_detail(session: DriverSession, artifact_id: str) -> dict[str, Any]:
    detail = session.transport("GET", f"/v1/agent/artifacts/{artifact_id}")
    assert isinstance(detail, dict), "artifact detail is malformed"
    return detail


def assert_artifact_named_forecast(detail: Mapping[str, Any]) -> None:
    artifact = detail.get("artifact")
    title = artifact.get("title") if isinstance(artifact, dict) else None
    filename = detail.get("suggested_filename")
    assert title == ARTIFACT_NAME or filename == ARTIFACT_NAME, (
        "agent did not create the requested forecast.csv artifact"
    )


def read_artifact_bytes(session: DriverSession, artifact: ArtifactReference) -> bytes:
    """Read immutable artifact bytes through the real Electron-main IPC stream."""

    javascript = f"""(async()=>{{
      const opened=await window.bridge.ipc.invoke("transport.artifact-content.open",{{
        artifactId:{json.dumps(artifact.artifact_id)},revision:{artifact.revision}
      }});
      const bytes=[];
      try {{
        for (;;) {{
          const next=await window.bridge.ipc.invoke("transport.artifact-content.read",{{handle:opened.handle}});
          if (next.done) break;
          if (next.chunk===null) throw new Error("empty artifact chunk");
          for (const value of next.chunk) {{
            bytes.push(value);
            if (bytes.length>131072) throw new Error("artifact exceeds G2 bound");
          }}
        }}
      }} finally {{
        await window.bridge.ipc.invoke("transport.artifact-content.close",{{handle:opened.handle}});
      }}
      let binary="";
      for (const value of bytes) binary+=String.fromCharCode(value);
      return btoa(binary);
    }})()"""
    raw = session.evaluate(javascript)
    assert isinstance(raw, str), "artifact stream did not return base64"
    try:
        return base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise AssertionError("artifact stream returned invalid base64") from exc


def assert_dataset_surface(session: DriverSession) -> None:
    required = {
        "artifact frame": "[data-testid=artifact-frame]",
        "dataset renderer": "[data-testid=artifact-dataset-renderer]",
        "cell editor": '[role=grid][aria-label="Dataset cell editor"]',
        "bookings cell": '[aria-label="bookings, row 2"]',
        "revision actions": '[aria-label="Dataset revision actions"]',
        "revision history": "[data-testid=artifact-revision-history]",
    }
    missing = [
        name for name, selector in required.items() if not session.present(selector)
    ]
    assert not missing, f"G2 dataset table/editor is missing: {missing}"


def open_artifact_from_sources(session: DriverSession) -> None:
    session.click('[role=tab]:has-text("Sources")')
    assert session.wait_for("[data-testid=sources-v2-tab]"), (
        "Studio did not show the Sources provenance rail for the dataset"
    )
    source_text = str(
        session.evaluate(
            'document.querySelector("[data-testid=sources-v2-tab]").innerText'
        )
    )
    assert "Artifact" in source_text, "dataset provenance did not identify its artifact"
    if not session.present("[data-testid=artifact-frame]"):
        assert session.present("[data-testid=sources-v2-open-artifact]"), (
            "dataset source is not user-openable from provenance"
        )
        session.click("[data-testid=sources-v2-open-artifact]")
        assert session.wait_for("[data-testid=artifact-frame]"), (
            "opening the dataset source did not render an artifact surface"
        )


def assert_initial_csv_semantics(content: bytes) -> None:
    headers, rows = _parse_csv_bytes(content)
    assert tuple(headers) == INITIAL_HEADERS, (
        "dataset artifact does not use the required forecast CSV columns"
    )
    assert len(rows) >= 3, "dataset artifact must contain at least three forecast rows"
    for row in rows:
        assert row["month"] and row["region"], "forecast row is missing its identity"
        int(row["bookings"])
        int(row["forecast"])


def assert_only_workspace_or_artifact_tools(events: list[dict[str, Any]]) -> None:
    for event in events:
        event_type = event.get("event_type")
        payload = _payload(event)
        values = [
            str(event_type or "").lower(),
            *(
                str(payload[key]).lower()
                for key in ("capability", "tool", "tool_name", "name", "operation")
                if isinstance(payload.get(key), str)
            ),
        ]
        joined = " ".join(values)
        leaked = sorted(marker for marker in UNRELATED_TOOL_MARKERS if marker in joined)
        assert not leaked, f"G2 used unrelated tooling: {leaked}"
        capability = payload.get("capability")
        if isinstance(capability, str):
            assert capability in {"workspace", "artifact"}, (
                f"G2 used unsupported capability {capability!r}"
            )


def assert_no_workspace_apply(events: list[dict[str, Any]]) -> None:
    for event in events:
        if event.get("event_type") not in APPLY_EVENTS:
            continue
        # G2 permits only artifact and workspace activity. There can therefore
        # be no benign effect application before the native approval decision.
        raise AssertionError("workspace write was applied before explicit approval")


TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled", "run_rejected"}


def assert_artifact_precedes_the_seal(events: list[dict]) -> None:
    """The ledger half: causal artifact facts live inside the sealed prefix."""

    ordered = sorted(events, key=lambda e: e.get("sequence_no", 0))
    names = [(e.get("sequence_no"), e.get("event_type")) for e in ordered]
    seal = next(
        (seq for seq, name in names if name in TERMINAL_EVENTS),
        None,
    )
    assert seal is not None, f"run never sealed: {names}"
    for event_type in ("artifact.created", "artifact.presentation_decided"):
        seq = next((seq for seq, name in names if name == event_type), None)
        assert seq is not None, f"{event_type} missing from the run ledger: {names}"
        assert seq < seal, (
            f"{event_type} landed at {seq}, after the seal at {seal} — "
            "no live client can receive it"
        )
    # Nothing at all may follow the seal on the green path.
    assert names[-1][1] in TERMINAL_EVENTS, (
        f"events appended after the seal: {[n for n in names if n[0] > seal]}"
    )


def assert_canvas_presents_without_navigation(session: DriverSession) -> None:
    """The UI half: the table is on screen with no click of any kind."""

    assert session.wait_for("[data-testid=artifact-frame]", 60), (
        "Studio never presented an artifact frame after the run completed"
    )
    assert session.wait_for("[data-testid=artifact-dataset-renderer]", 60), (
        "Studio presented no dataset table for the published CSV"
    )
    # The regression's exact signature: the canvas' terminal narrative-only
    # empty state must NOT be what the user is looking at.
    panel = session.evaluate(
        'document.querySelector("[data-testid=canvas-lifecycle-panel]")'
        "?.getAttribute(\"data-lifecycle\") ?? 'absent'"
    )
    assert panel in ("absent", "presenting"), (
        f"canvas showed the {panel!r} empty state instead of the dataset"
    )


FOLLOW_UP_PROMPT = (
    "In one short sentence, and using no tools at all, what does the region "
    "column mean?"
)


def canvas_state(session: DriverSession) -> dict:
    raw = session.evaluate(
        "(function(){"
        "var p=document.querySelector('[data-testid=canvas-lifecycle-panel]');"
        "return JSON.stringify({"
        "frame:!!document.querySelector('[data-testid=artifact-frame]'),"
        "table:!!document.querySelector('[data-testid=artifact-dataset-renderer]'),"
        "emptyState:p?p.getAttribute('data-lifecycle'):null,"
        "tabs:Array.from(document.querySelectorAll('[data-testid=tc-tabs] [role=tab]'))"
        ".map(function(t){return (t.textContent||'').trim();})"
        "});})()"
    )
    return json.loads(str(raw))


def assert_canvas_shows_the_dataset(state: dict, *, when: str) -> None:
    assert state["frame"], f"{when}: Studio showed no artifact frame"
    assert state["table"], f"{when}: Studio showed no dataset table"
    assert state["emptyState"] is None, (
        f"{when}: the canvas empty state ({state['emptyState']!r}) was showing "
        "instead of the dataset"
    )


ADD_ROW_PROMPT = "Add one more row to that CSV. Keep the same columns."


STALE_CLAIM = "A newer revision exists"


CELL = ".ui-dataset-table--editable tbody input.ui-dataset-cell-input"


SAVE = '[aria-label="Dataset revision actions"] button.ui-button--primary'


def current_revision(session: DriverSession, artifact_id: str) -> int:
    detail = _artifact_detail(session, artifact_id)
    artifact = detail.get("artifact") if isinstance(detail, dict) else None
    assert isinstance(artifact, dict), f"no artifact record for {artifact_id}"
    revision = artifact.get("current_revision")
    assert isinstance(revision, int), f"no current_revision on {artifact_id}"
    return revision


def dataset_artifact_ids(session: DriverSession, conversation_id: str) -> set[str]:
    """Every dataset artifact the conversation canvas holds.

    Read from the conversation canvas rather than one run's events, because the
    duplicate in BUG 2 was produced by a LATER run than the original.
    """
    canvas = session.transport(
        "GET", f"/v1/agent/conversations/{conversation_id}/canvas"
    )
    subjects = canvas.get("subjects", []) if isinstance(canvas, dict) else []
    return {
        subject["subject_id"]
        for subject in subjects
        if isinstance(subject, dict)
        and subject.get("kind") == "artifact"
        and str(subject.get("renderer_hint", "")).endswith("dataset")
    }


def wait_for_revision(
    session: DriverSession, artifact_id: str, at_least: int, timeout_s: int = 30
) -> int:
    """Poll the facade until the artifact reaches ``at_least``, or give up.

    Polling rather than sleeping keeps a slow machine from being reported as a
    regression, and returns the revision actually observed so the caller can
    assert on a real number rather than a timeout.
    """
    seen = 0
    for _ in range(timeout_s * 2):
        seen = current_revision(session, artifact_id)
        if seen >= at_least:
            return seen
        time.sleep(0.5)
    return seen


OKLCH = re.compile(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)")


def probe_artifact(session: DriverSession) -> str:
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


def is_identity_colour(value: str) -> bool:
    """True when a computed colour carries real chroma, i.e. it is a hue."""

    match = OKLCH.search(value or "")
    if match is None:
        return False
    return float(match.group(2)) > 0.02


def read_surface_colour(session: DriverSession) -> dict:
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


def assert_colour(observed: dict, canvas_accent: str | None) -> None:
    # 1-2. The tab carries an identity, and it renders as a real hue.
    assert observed.get("tabHue"), "canvas tab carries no data-surface-hue"
    expected = canvas_accent or "sky"
    assert observed["tabHue"] == expected, (
        f"tab hue is {observed['tabHue']!r}; the artifact-dataset default is "
        f"'sky' and the record's accent is {canvas_accent!r}"
    )
    dot = observed.get("tabDotColour", "")
    assert is_identity_colour(dot), (
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
    assert is_identity_colour(numeric_colour), (
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
    assert is_identity_colour(observed.get("barColour", "")), (
        f"value bar computed to {observed.get('barColour')!r} — no hue"
    )
    widths = observed.get("barWidths") or []
    assert len(set(widths)) > 1, (
        f"every value bar is the same width ({widths}); the bars are not encoding "
        "magnitude"
    )


def wait_for_canvas_settled(session: DriverSession, timeout_s: int = 60) -> str:
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


def install_ipc_recorder(session: DriverSession) -> str:
    """Record every renderer IPC call AND ITS OUTCOME, from before the run starts.

    Counting only calls made during a late window cannot answer the question
    that is left: a request issued once and never answered looks identical to a
    request that was never issued, because both add nothing to the window.

    So this installs at mount time and tracks each call's fate — resolved,
    rejected, or still pending. `pending > 0` for the artifact metadata path
    means the reply never came back; `made == 0` means the component never
    asked. Those are the only two candidates left, and they need opposite fixes.
    """

    return str(
        session.evaluate(
            """(() => {
              try {
                if (window.__ipc) return 'already';
                const rec = {};
                const bump = (k, f) => {
                  rec[k] = rec[k] || { made: 0, resolved: 0, rejected: 0 };
                  rec[k][f] += 1;
                };
                const inner = window.bridge.ipc.invoke.bind(window.bridge.ipc);
                window.__ipc = rec;
                window.bridge.ipc.invoke = function (channel, payload) {
                  const key = channel === 'transport.request' && payload && payload.path
                    ? 'GET ' + String(payload.path)
                        .replace(/art_[0-9a-f-]+/g, '{artifactId}')
                        .replace(/run_[0-9a-f-]+/g, '{runId}')
                        .replace(/conv_[0-9a-f-]+/g, '{convId}')
                    : channel;
                  bump(key, 'made');
                  let p;
                  try { p = inner(channel, payload); }
                  catch (e) { bump(key, 'rejected'); throw e; }
                  return Promise.resolve(p).then(
                    (v) => { bump(key, 'resolved'); return v; },
                    (e) => { bump(key, 'rejected'); throw e; },
                  );
                };
                return 'installed';
              } catch (e) { return 'FAILED: ' + (e && e.message || e); }
            })()"""
        )
    )


def dump_ipc(session: DriverSession) -> dict:
    """Read the recorder, keeping only rows that are interesting or unfinished."""

    raw = session.evaluate(
        """(() => {
          const rec = window.__ipc || {};
          const out = {};
          for (const k of Object.keys(rec)) {
            const r = rec[k];
            const pending = r.made - r.resolved - r.rejected;
            if (pending > 0 || k.indexOf('artifact') !== -1) {
              out[k] = r.made + ' made / ' + r.resolved + ' resolved / '
                     + r.rejected + ' rejected / ' + pending + ' PENDING';
            }
          }
          out['__totalChannels'] = String(Object.keys(rec).length);
          return JSON.stringify(out);
        })()"""
    )
    try:
        return json.loads(raw) if isinstance(raw, str) else {"ipc": "no result"}
    except Exception:  # noqa: BLE001 — diagnostic only
        return {"ipc": f"unparseable: {str(raw)[:200]}"}


def count_ipc(session: DriverSession, seconds: int = 4) -> dict:
    """Count IPC calls the RENDERER makes on its own over a quiet window.

    A canvas parked on `artifact-loading` with zero content streams opened has
    two possible causes, and the stream-handle counter cannot tell them apart:
    an effect that never ran, or an effect re-running faster than its metadata
    request completes (each run aborts the previous one before it ever reaches
    `getArtifactContent`, so both leave the handle counter at zero).

    Nobody is driving the app during this window, so any repeated
    `transport.request` for an artifact path is the component looping.
    """

    patched = session.evaluate(
        """(() => {
          try {
            if (window.__ipcCounts) return 'already';
            const counts = {};
            const inner = window.bridge.ipc.invoke.bind(window.bridge.ipc);
            window.__ipcCounts = counts;
            window.bridge.ipc.invoke = function (channel, payload) {
              const key = channel === 'transport.request' && payload && payload.path
                ? 'transport.request ' + String(payload.path).replace(/art_[0-9a-f-]+/, '{id}')
                : channel;
              counts[key] = (counts[key] || 0) + 1;
              return inner(channel, payload);
            };
            return 'patched';
          } catch (e) { return 'FAILED: ' + (e && e.message || e); }
        })()"""
    )
    if not isinstance(patched, str) or patched.startswith("FAILED"):
        return {"ipcCounting": str(patched)}
    time.sleep(seconds)
    counts = session.evaluate("JSON.stringify(window.__ipcCounts || {})")
    try:
        return {
            "ipcCounting": f"over {seconds}s",
            "calls": json.loads(counts) if isinstance(counts, str) else {},
        }
    except Exception:  # noqa: BLE001 — diagnostic only
        return {"ipcCounting": f"unparseable: {str(counts)[:160]}"}


def probe_canvas(session: DriverSession) -> dict:
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


def read_strip(session: DriverSession) -> dict:
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


def assert_no_alert(before: dict, after: dict, clicked_uri: str) -> None:
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


#: Kept in step with ``surface-floor/fixture_mcp.PORT``: same env var, same
#: default, so a journey and a fixture started from two different shells still
#: meet.
FIXTURE_PORT: Final[int] = int(os.environ.get("SURFACE_FIXTURE_PORT", "8931"))
FIXTURE_ORIGIN: Final[str] = f"http://127.0.0.1:{FIXTURE_PORT}"
FIXTURE_URL: Final[str] = f"{FIXTURE_ORIGIN}/mcp"
FIXTURE_MANIFEST: Final[str] = f"{FIXTURE_ORIGIN}/shapes"

#: The fixture revision this phase knows how to read
#: (``surface-floor/fixture_mcp.REVISION``). A mismatch stops the phase rather
#: than measuring a fixture whose shape numbers mean something else.
EXPECTED_REVISION: Final[str] = "eight-shapes.1"

#: Slot labels that belong to the MCP TRANSPORT, not to any connector.
#: ``langchain-mcp-adapters`` converts each content block to
#: ``{"type": "text", "text": …, "id": …}``; when the ladder binds the envelope
#: instead of the payload inside it, these three are exactly what the table
#: draws as its columns. Measured against the real projector, not guessed — the
#: historical defect is rung 0 inferring a perfectly valid table over
#: ``items_path: "result"`` and stuffing the connector's whole payload into one
#: ``Text`` cell.
TRANSPORT_SLOTS: Final[frozenset[str]] = frozenset({"id", "type", "text"})

#: Slot labels that can only have come from the fixture's own rows. Two of these
#: with none of :data:`TRANSPORT_SLOTS` is what "the surface bound the payload"
#: means operationally.
CONNECTOR_SLOTS: Final[frozenset[str]] = frozenset(
    {
        "number",
        "title",
        "status",
        "urgency",
        "assignee",
        "service",
        "created at",
        "labels",
        "email",
        "team",
        "has next page",
    }
)

#: How many connector-named slots a surface must draw before it counts as bound.
#: One is not enough: an envelope table that happened to pick up a stray key
#: would clear a threshold of one.
MIN_BOUND_SLOTS: Final[int] = 2


@dataclass(frozen=True)
class Shape:
    """One row of the matrix: an envelope, and the tool that serves it.

    ``expected_rows`` is what a CORRECT render draws — three incidents for a
    collection, ``None`` for the shapes whose result is one record or a
    paragraph. It is the fixture's dataset size, cross-checked against the
    served manifest at startup so the number lives in one place.
    """

    number: int
    tool: str
    wire: str
    ask: str
    expected_rows: int | None


#: The prompt names the tool explicitly. Every other connector phase here
#: deliberately asks for an OUTCOME so a descriptor rename does not look like a
#: broken connector — this one needs the opposite: the measurement is
#: per-envelope, and a run that picked a neighbouring tool would attribute one
#: envelope's result to another. The suffix is shared so the phrasing cannot
#: drift between shapes and become a variable of its own.
_ASK_SUFFIX: Final[str] = (
    " Call it exactly once and show me what it returned. Do not summarise the "
    "result in prose, and do not call any other tool."
)


def _ask(tool: str) -> str:
    return f"Use the incidents connector and call its `{tool}` tool.{_ASK_SUFFIX}"


SHAPES: Final[tuple[Shape, ...]] = (
    Shape(
        1, "list_incidents", "json object in a text block", _ask("list_incidents"), 3
    ),
    Shape(
        2,
        "get_incident",
        "nested json document in a text block",
        _ask("get_incident") + " Use incident number 4471.",
        None,
    ),
    Shape(
        3,
        "list_incidents_structured",
        "structuredContent",
        _ask("list_incidents_structured"),
        3,
    ),
    Shape(
        4,
        "list_incidents_array",
        "json array at root",
        _ask("list_incidents_array"),
        3,
    ),
    Shape(
        5,
        "summarize_incidents_prose",
        "prose",
        _ask("summarize_incidents_prose"),
        None,
    ),
    Shape(
        6,
        "list_incidents_markdown",
        "markdown table",
        _ask("list_incidents_markdown"),
        3,
    ),
    Shape(7, "export_incidents_csv", "csv", _ask("export_incidents_csv"), 3),
    Shape(8, "incident_briefing", "three content blocks", _ask("incident_briefing"), 3),
)

#: Shape 1 is the Linear form — the one every real connector produces — so the
#: deep four-hop identity trace runs on it. Before the fixture was rewritten the
#: trace ran on a ``structuredContent`` payload, which is why it never caught
#: anything.
TRACE_SHAPE: Final[int] = 1

#: Only a subset of the matrix can be driven when time or budget is short.
#: ``SURFACE_FLOOR_SHAPES=1,4`` is the phase equivalent of the retired
#: ``floor_e2e.py --shapes 1,4``.
SHAPES_ENV: Final[str] = "SURFACE_FLOOR_SHAPES"


def selected_shapes() -> tuple[Shape, ...]:
    """The shapes this run drives, defaulting to all eight."""

    raw = os.environ.get(SHAPES_ENV, "").strip()
    if not raw:
        return SHAPES
    wanted = {int(part) for part in raw.split(",") if part.strip()}
    chosen = tuple(shape for shape in SHAPES if shape.number in wanted)
    assert chosen, (
        f"{SHAPES_ENV}={raw!r} names no shape in {[s.number for s in SHAPES]}"
    )
    return chosen


@dataclass
class ShapeOutcome:
    """What one envelope actually produced, from the DOM and from the server."""

    shape: Shape
    called: bool = False
    rendered: bool = False
    kind: str = "-"
    rows: int = 0
    slots: list[str] = field(default_factory=list)
    server_items_path: str | None = None
    server_rows: int | None = None
    note: str = ""

    @property
    def slot_keys(self) -> set[str]:
        return {slot.strip().lower() for slot in self.slots if slot.strip()}

    @property
    def bound(self) -> bool:
        """Did the surface bind the CONNECTOR's payload, or the transport's envelope?

        The distinction a single mounted-renderer assertion has no way to make.
        A table drawn over ``{"result": [<content blocks>]}`` is a real,
        mounted, non-empty table — it just describes the wire format, and shape
        8 draws three rows of it, which from across the room looks exactly like
        three incidents. Requiring connector-named slots AND forbidding
        transport-named ones separates the two.
        """

        keys = self.slot_keys
        if keys & TRANSPORT_SLOTS:
            return False
        return len(keys & CONNECTOR_SLOTS) >= MIN_BOUND_SLOTS

    @property
    def verdict(self) -> str:
        if not self.called:
            # What is actually measured is "no surface snapshot carries
            # op=<tool>". That is either a tool the run never called or a
            # PRESENT stage that dropped it, and the two are not distinguishable
            # from here — so the verdict says what was observed and ``note``
            # carries the endpoint's own words.
            return "NO READ ON LEDGER"
        if not self.rendered:
            return "NO SURFACE"
        if not self.bound:
            return "ENVELOPE BOUND"
        expected = self.shape.expected_rows
        if expected is not None and self.rows != expected:
            return f"PARTIAL ({self.rows}/{expected})"
        return "OK"

    @property
    def ok(self) -> bool:
        return self.verdict == "OK"


def app_write(session: DriverSession, method: str, path: str, body: dict) -> object:
    """Authenticated POST/PATCH through the app. ``_lib.transport`` is GET-shaped.

    Module-level because two journeys need it (AS-9 registers a connector, AS-10
    registers one AND binds a per-chat write scope), and a second copy of the
    IPC spelling is a second thing to fix the day the bridge changes.
    """

    payload = json.dumps({"method": method, "path": path, "body": body})
    js = (
        "(async()=>{try{const r=await window.bridge.ipc.invoke("
        f'"transport.request",{payload});'
        'if(r&&r.kind==="transport-result"){'
        'if(!r.ok)return "ERR:HTTP "+String(r.error?.status??"?")+" "'
        '+String(r.error?.message??"");'
        "return JSON.stringify(r.value);}return JSON.stringify(r);}"
        'catch(e){return "ERR:"+e.message}})()'
    )
    raw = session.evaluate(js)
    if isinstance(raw, str) and raw.startswith("ERR:"):
        raise RuntimeError(f"{method} {path} -> {raw}")
    return json.loads(raw)


class FloorJourney:
    """Drives the app once per envelope and judges the surface each one produces.

    One dataset, eight wire forms. Nothing about the DATA varies between them,
    so any difference in what renders is attributable to the envelope and to
    nothing else — which is the only way to say "the connector's columns" and
    "the MCP envelope's columns" apart with evidence.
    """

    def __init__(self, session: DriverSession, shapes: tuple[Shape, ...]) -> None:
        self.session = session
        self.shapes = shapes
        self.findings: list[str] = []
        self.outcomes: list[ShapeOutcome] = []

    # -- helpers ---------------------------------------------------------

    def post(self, path: str, body: dict) -> object:
        """Authenticated POST through the app."""

        return app_write(self.session, "POST", path, body)

    def note(self, ok: bool, claim: str) -> bool:
        self.findings.append(f"{'PASS' if ok else 'FAIL'}  {claim}")
        return ok

    # -- fixture identity -------------------------------------------------

    @staticmethod
    def read_manifest() -> dict:
        """The RUNNING fixture's own account of what it serves.

        Read over plain HTTP before anything is registered, because a stale
        fixture is a silent failure with a convincing face: a previous session's
        server left listening on the port registers fine, initialises fine, and
        then serves ITS tool list. That happened while these shapes were being
        written — every newly added tool came back "Unknown tool" from a server
        that looked perfectly healthy.
        """

        with urllib.request.urlopen(FIXTURE_MANIFEST, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def check_fixture(self) -> None:
        """Refuse to MEASURE a fixture this phase does not recognise.

        A mismatch skips rather than fails: it is a statement about this box
        (which process is listening on the port), not about the product. Exit
        `3` is still non-zero, so it cannot be mistaken for a pass.
        """

        manifest = self.read_manifest()
        revision = manifest.get("revision")
        require(
            revision == EXPECTED_REVISION,
            f"the fixture on :{FIXTURE_PORT} reports revision {revision!r}, not "
            f"{EXPECTED_REVISION!r} — a stale server is listening, or the "
            "fixture changed without this phase. Stop it and restart it.",
        )
        served = {row.get("tool") for row in manifest.get("shapes", [])}
        missing = sorted({shape.tool for shape in SHAPES} - served)
        require(
            not missing,
            f"the fixture on :{FIXTURE_PORT} does not serve {missing} — it is "
            "not the one this phase was written against",
        )
        rows = manifest.get("rows")
        expected = {s.expected_rows for s in SHAPES if s.expected_rows is not None}
        assert not expected or expected == {rows}, (
            f"the fixture serves {rows} rows; the matrix expects "
            f"{sorted(expected)}. Update Shape.expected_rows."
        )
        log(
            f"fixture {manifest.get('fixture')!r} rev {revision} on "
            f":{FIXTURE_PORT} — {len(served)} shapes, {rows} rows"
        )

    # -- steps -----------------------------------------------------------

    def register_fixture(self) -> str:
        """Register the loopback connector. No OAuth: ``auth_mode: none``."""

        created = self.post(
            "/v1/mcp/servers",
            {
                "url": FIXTURE_URL,
                "display_name": "Incidents",
                "transport": "http",
                "auth_mode": "none",
            },
        )
        assert isinstance(created, dict), created
        server_id = str(created.get("id") or created.get("server_id") or "")
        assert server_id, f"no server id in {created}"
        return server_id

    # -- run plumbing -----------------------------------------------------

    def _run_ids(self, limit: int = 20) -> list[str]:
        """Run ids for this profile, newest first, from the API not the DOM.

        The run cockpit carries no ``data-run-id`` — that attribute exists only
        on the Activity and Routines rows — so scraping it silently yielded
        ``""`` and disarmed both ledger hops. Each shape opens a NEW chat, so
        the shared ``wait_for_new_run`` (which is per-conversation and takes a
        conversation id) would need the route to have re-bound first; polling
        the profile-wide run list is the same authenticated transport and does
        not depend on the hash having changed yet.
        """

        try:
            runs = self.session.transport("GET", f"/v1/agent/runs?limit={limit}")
        except Exception as exc:  # noqa: BLE001 — trace only
            print(f"[trace] run lookup failed: {exc}")
            return []
        rows = (runs or {}).get("runs") or (runs or {}).get("items") or []
        return [str(row.get("id") or row.get("run_id") or "") for row in rows]

    def _run_id(self) -> str:
        ids = self._run_ids(limit=1)
        return ids[0] if ids else ""

    def _wait_for_new_run(self, before: set[str], timeout_s: int = 120) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for run_id in self._run_ids():
                if run_id and run_id not in before:
                    return run_id
            time.sleep(1)
        return ""

    def _wait_terminal(self, run_id: str, timeout_s: int = 180) -> str:
        """Poll to a terminal status. Returns the status, never raises.

        Deliberately NOT ``_lib.wait_for_terminal_run``, which asserts the run
        completed: a failed run is a matrix CELL, not an abort. The deliverable
        is all eight envelopes measured, including the ones that break their
        run. Raising here would delete every row after the first bad one, which
        is how a one-read journey hides a systemic failure behind a single
        stack trace.
        """

        deadline = time.time() + timeout_s
        status = "unknown"
        while time.time() < deadline:
            try:
                result = self.session.transport("GET", f"/v1/agent/runs/{run_id}")
            except Exception as exc:  # noqa: BLE001 — a cell, not an abort
                return f"lookup-failed: {exc}"
            status = str((result or {}).get("status") or "unknown")
            if status in TERMINAL_STATUSES:
                return status
            time.sleep(1)
        return f"not-terminal ({status})"

    # -- pipeline trace ---------------------------------------------------

    def _hop_ledger(self, run_id: str) -> dict:
        """Hop 1 — what the emitter actually WROTE, read off disk.

        Read from the app's own JSONL store rather than from injected
        instrumentation: a probe that lives in the client can only ever prove
        what the client believes. The file is the emitter's own output, so a
        disagreement between this hop and hop 2 localises the break to the
        transport allow-list — which has silently stripped a field before.
        """

        root = self.session._user_data_dir
        found: dict = {"searched": str(root), "events": [], "error": None}
        if not run_id:
            # Without a run id every conversation's rows would be collected and
            # the identity check could pass by accident off an unrelated run.
            found["error"] = "no run_id in DOM — refusing to guess"
            return found
        try:
            # `events.jsonl` is per-CONVERSATION, not per-run
            # (`runtime_adapters/file/_paths.py:104` — conversation_dir/EVENTS_FILE),
            # so the run id never appears in the path and every row must be
            # filtered on its own envelope. Filtering by filename here silently
            # skipped the right ledger on any conversation past its first turn.
            for path in root.rglob("events.jsonl"):
                for line in path.read_text(errors="replace").splitlines():
                    if '"surface.created"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("run_id") != run_id:
                        continue
                    payload = row.get("payload") or row.get("data") or {}
                    state = payload.get("state")
                    found["events"].append(
                        {
                            "surface_id": payload.get("surface_id"),
                            "state_keys": None if state is None else sorted(state),
                            "rows": len((state or {}).get("data") or [])
                            if state
                            else 0,
                            "file": str(path),
                        }
                    )
        except OSError as exc:
            # One unreadable file must not take the whole journey down with it.
            found["error"] = f"{type(exc).__name__}: {exc}"
        return found

    def _hop_server(self, run_id: str) -> dict:
        """Hop 2 — what the HTTP endpoint SERVES for those same surfaces."""

        if not run_id:
            return {"error": "no run_id in DOM"}
        try:
            served = self.session.transport("GET", f"/v1/agent/runs/{run_id}/surfaces")
        except Exception as exc:  # noqa: BLE001 — trace only
            return {"error": str(exc)}
        return {
            "surfaces": [
                {
                    "surface_id": row.get("surface_id"),
                    "state_keys": (
                        None if row.get("state") is None else sorted(row["state"])
                    ),
                }
                for row in (served or {}).get("surfaces", [])
            ]
        }

    def _hop_client(self) -> dict:
        """Hop 3 — what the canvas KEYS its tabs by, straight from the DOM."""

        # `.tc-tab[data-uri]` (TcTabs.tsx:88), NOT `[data-testid^=tc-tab]` — that
        # prefix also matches the `tc-tabs-unpin-*` close buttons, and the
        # textContent fallback then yielded the tab LABEL ("incidents ·
        # list_incidents") instead of the URI. Comparing labels to surface ids
        # made the codec check vacuous: a label can never contain "%3A".
        raw = self.session.evaluate(
            "(()=>{const t=[...document.querySelectorAll('.tc-tab[data-uri]')]"
            ".map(e=>e.getAttribute('data-uri'));"
            "const slot=document.querySelector('[data-canvas-slot-testid=tc-surface-slot]');"
            "return JSON.stringify({tabs:t, activeUri: slot?slot.getAttribute('data-active-uri'):null});})()"
        )
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {"raw": raw}

    def diagnose(self, run_id: str) -> dict:
        """Trace all four hops and print them as one table.

        The architecture's central invariant is that ``surface_id`` is ONE value:
        what the ledger writes, what the endpoint serves, and what the canvas
        keys its tabs by are the same string with no codec between them. The
        bug this journey was written to catch was precisely a violation of that
        — the canvas minted ``table://legacy-v2/table%3A%2F%2F…`` and then
        looked it up in a map that could never contain it. So the trace prints
        the identity at each hop side by side; divergence is the finding.
        """

        ledger = self._hop_ledger(run_id)
        server = self._hop_server(run_id)
        client = self._hop_client()

        print(f"\n[trace] run_id: {run_id!r}")
        print("[trace] hop 1 LEDGER (events.jsonl on disk)")
        for e in ledger["events"] or [{"surface_id": None, "state_keys": "NO EVENTS"}]:
            print(
                f"          surface_id={e.get('surface_id')!r} "
                f"state={e.get('state_keys')} rows={e.get('rows', 0)}"
            )
        if not ledger["events"]:
            print(f"          (searched {ledger['searched']})")
            if ledger.get("error"):
                print(f"          ERROR: {ledger['error']}")
        print("[trace] hop 2 SERVER  (GET /v1/agent/runs/{id}/surfaces)")
        for s in server.get("surfaces") or [{"surface_id": server.get("error")}]:
            print(
                f"          surface_id={s.get('surface_id')!r} state={s.get('state_keys')}"
            )
        print("[trace] hop 3 CLIENT  (canvas tab keys)")
        print(f"          tabs={client.get('tabs')} active={client.get('activeUri')!r}")

        ledger_ids = {e["surface_id"] for e in ledger["events"] if e.get("surface_id")}
        server_ids = {
            s["surface_id"] for s in server.get("surfaces", []) if s.get("surface_id")
        }
        client_ids = {t for t in (client.get("tabs") or []) if t}
        print("[trace] hop 4 IDENTITY")
        print(f"          ledger={sorted(ledger_ids)}")
        print(f"          server={sorted(server_ids)}")
        print(f"          client={sorted(client_ids)}")

        return {
            "run_id": run_id,
            "ledger_ids": ledger_ids,
            "server_ids": server_ids,
            "client_ids": client_ids,
            "ledger_error": ledger.get("error"),
        }

    # -- reading what was drawn -------------------------------------------

    def read_surface(self) -> dict:
        """Read what is actually on screen, out of the live DOM.

        ``slots`` is the load-bearing field and it is read from the elements the
        renderers really emit: ``th`` for a table, and the label span of each
        ``[data-testid^=field-]`` row for a record. An earlier version looked
        for ``[data-testid$=-label]``, which no renderer has ever emitted
        (``primitives.tsx::FieldRow`` gives the row ``field-<path>`` and the
        value ``field-<path>-value``; the label span carries no test id) — so
        the record branch reported zero slots whatever it drew, and the bound /
        unbound verdict was decided entirely by the table branch.
        """

        js = """(()=>{
          const el = document.querySelector('[data-testid=table-renderer]')
                  || document.querySelector('[data-testid=record-renderer]');
          if(!el) return JSON.stringify({present:false});
          const q = (s)=>[...el.querySelectorAll(s)].map(n=>n.textContent.trim());
          const headers = q('th');
          const fieldRows = [...el.querySelectorAll('[data-testid^=field-]')]
            .filter(n=>!/-(value|badge)$/.test(n.getAttribute('data-testid')||''));
          const fieldLabels = fieldRows
            .map(n=>((n.firstElementChild||{}).textContent||'').trim())
            .filter(Boolean);
          return JSON.stringify({
            present:true,
            kind: el.getAttribute('data-testid'),
            spec: el.getAttribute('data-spec'),
            title: (el.querySelector('[data-testid=surface-title]')||{}).textContent,
            headers: headers,
            fieldLabels: fieldLabels,
            slots: headers.length ? headers : fieldLabels,
            rows: el.querySelectorAll('[data-testid^=table-row-]').length
                  || fieldRows.length,
            firstRow: q('[data-testid^=table-cell-0-]'),
            badges: q('[data-surface-format=badge]'),
            body: el.textContent.slice(0, 400),
          });
        })()"""
        return json.loads(self.session.evaluate(js))

    def read_server_surface(self, run_id: str, tool: str) -> dict:
        """The served surface for ``tool``, keyed on the snapshot's own ``op``.

        Attributing by ``op`` rather than by "the newest surface" is what keeps
        a shape's verdict about that shape. A run that ignored the instruction
        and called a neighbouring tool still produces a surface, and scoring
        that one here would move a real finding onto the wrong envelope — the
        matrix would then be internally consistent and wrong, which is the
        hardest kind of wrong to notice.
        """

        try:
            served = self.session.transport("GET", f"/v1/agent/runs/{run_id}/surfaces")
        except Exception as exc:  # noqa: BLE001 — a cell, not an abort
            return {"error": str(exc)}
        for row in (served or {}).get("surfaces", []):
            if row.get("op") != tool:
                continue
            state = row.get("state") or {}
            spec = state.get("spec") or {}
            items_path = spec.get("items_path")
            bound = state.get("data")
            if isinstance(items_path, str) and items_path:
                for segment in items_path.split("."):
                    bound = bound.get(segment) if isinstance(bound, dict) else None
            return {
                "surface_id": row.get("surface_id"),
                "kind": row.get("kind"),
                "items_path": items_path,
                "rows": len(bound) if isinstance(bound, list) else None,
                "labels": [
                    entry.get("label")
                    for entry in (spec.get("columns") or spec.get("fields") or [])
                ],
            }
        return {"error": f"no served surface for op={tool!r}"}

    # -- the matrix --------------------------------------------------------

    def drive_shape(self, shape: Shape, *, first: bool) -> ShapeOutcome:
        """One envelope: one chat, one read, one row of the matrix."""

        outcome = ShapeOutcome(shape=shape)
        log(f"shape {shape.number} — {shape.tool} ({shape.wire})")
        if not first:
            new_chat(self.session)
        before = set(self._run_ids())
        self.session.send(shape.ask)

        run_id = self._wait_for_new_run(before)
        if not run_id:
            outcome.note = "no run persisted"
            return outcome
        status = self._wait_terminal(run_id)
        log(f"  run {run_id} -> {status}")

        served = self.read_server_surface(run_id, shape.tool)
        outcome.called = "error" not in served
        outcome.note = str(served.get("error") or "")
        if outcome.called:
            outcome.server_items_path = served.get("items_path")
            outcome.server_rows = served.get("rows")

        # The canvas needs a beat after the run seals before the newest surface
        # is the mounted one; a read taken at the terminal event catches the
        # PREVIOUS shape's tab and attributes it to this row.
        time.sleep(3)
        drawn = self.read_surface()
        outcome.rendered = bool(drawn.get("present"))
        if outcome.rendered:
            outcome.kind = str(drawn.get("kind") or "-")
            outcome.rows = int(drawn.get("rows") or 0)
            outcome.slots = [str(slot) for slot in (drawn.get("slots") or [])]
        self.session.shot(f"shape-{shape.number:02d}-{shape.tool}")
        log(
            f"  drawn kind={outcome.kind} rows={outcome.rows} "
            f"slots={outcome.slots} | served items_path="
            f"{outcome.server_items_path!r} rows={outcome.server_rows}"
        )
        return outcome

    def print_matrix(self) -> None:
        """The deliverable: one line per envelope, and why it failed."""

        print("\n" + "=" * 96)
        print("SHAPE MATRIX — one dataset, eight envelopes")
        print("=" * 96)
        print(
            f"{'#':>2}  {'wire form':<38} {'rendered':<14} {'rows':>5}  "
            f"{'bound':<6} verdict"
        )
        print("-" * 96)
        for outcome in self.outcomes:
            rendered = (
                f"yes — {outcome.kind.replace('-renderer', '')}"
                if outcome.rendered
                else "NO"
            )
            print(
                f"{outcome.shape.number:>2}  {outcome.shape.wire:<38} "
                f"{rendered:<14} {outcome.rows:>5}  "
                f"{('yes' if outcome.bound else 'NO'):<6} {outcome.verdict}"
            )
        print("-" * 96)
        for outcome in self.outcomes:
            if outcome.ok:
                continue
            if outcome.slots:
                why = (
                    "those are the MCP envelope's own fields, not the connector's"
                    if outcome.slot_keys & TRANSPORT_SLOTS
                    else "no connector field was bound"
                )
                print(
                    f"    shape {outcome.shape.number}: drew {outcome.slots} over "
                    f"items_path={outcome.server_items_path!r} — {why}"
                )
            elif outcome.note:
                print(f"    shape {outcome.shape.number}: {outcome.note}")
        passed = sum(1 for outcome in self.outcomes if outcome.ok)
        print(f"\n{passed}/{len(self.outcomes)} envelopes render the connector's data.")
        print("=" * 96)

    # -- entry point -------------------------------------------------------

    def run(self) -> None:
        """Drive every selected envelope, then assert. Returning IS the pass.

        Note what this does NOT do: it does not sign in and it does not add a
        provider key. The boot's ``setup`` already did both, and re-running
        ``sign_in_local`` against a signed-in app fails on a gate that is no
        longer there.
        """

        s = self.session
        self.check_fixture()

        server_id = self.register_fixture()
        log(f"registered loopback connector: {server_id}")

        servers = s.transport("GET", "/v1/mcp/servers")
        rows = servers.get("servers", servers) if isinstance(servers, dict) else servers
        self.note(bool(rows), f"the connector is registered ({len(rows)} server(s))")
        s.shot("floor-00-connector-registered")

        trace_run_id = ""
        for index, shape in enumerate(self.shapes):
            outcome = self.drive_shape(shape, first=index == 0)
            self.outcomes.append(outcome)
            if shape.number == TRACE_SHAPE:
                trace_run_id = self._run_id()

        self.print_matrix()

        # -- the deep trace, on the shape every real connector produces ----
        if trace_run_id:
            trace = self.diagnose(trace_run_id)
            # ONE IDENTITY. The defect this trace exists to catch was the canvas
            # minting `table://legacy-v2/table%3A%2F%2F…` and resolving it
            # against a map that could never hold it. Both halves are asserted:
            # no codec artefact may appear in a tab key, and a tab key must be a
            # value the ledger itself wrote. Either alone would pass while the
            # bug was live.
            codecs = [
                uri
                for uri in trace["client_ids"]
                if "legacy-v2" in uri
                or "surfaces-v2" in uri
                or "%3A" in uri
                or "%2F" in uri
            ]
            self.note(not codecs, f"no URI codec survives on a tab key ({codecs})")
            # Both notes are UNCONDITIONAL. A guard here (`if ledger_ids:`) would
            # turn "the trace could not find the ledger" into a silent pass,
            # which is the gate-that-cannot-start failure this repo has already
            # paid for once. An empty ledger while a renderer is mounted is
            # itself the finding.
            self.note(
                bool(trace["ledger_ids"]),
                "the ledger on disk has surface.created rows for the traced run "
                f"({trace['ledger_error'] or sorted(trace['ledger_ids'])})",
            )
            shared = trace["client_ids"] & trace["ledger_ids"]
            self.note(
                bool(shared),
                "a tab key is byte-identical to a surface_id the ledger wrote "
                f"(shared={sorted(shared)})",
            )

        # -- per-shape claims, so a failure names its envelope ---------------
        for outcome in self.outcomes:
            shape = outcome.shape
            self.note(
                outcome.rendered,
                f"shape {shape.number} ({shape.wire}) mounted a renderer",
            )
            # THE claim this phase exists for. A mounted, non-empty table is not
            # evidence — shapes 4-8 historically drew `ID / Type / Text` over
            # `items_path: "result"` with the connector's whole payload in one
            # cell, and shape 8 drew three rows of it, which looks like three
            # incidents from across the room.
            self.note(
                outcome.bound,
                f"shape {shape.number} ({shape.wire}) bound the CONNECTOR's "
                f"fields, not the transport's — drew {outcome.slots} over "
                f"items_path={outcome.server_items_path!r}",
            )
            if shape.expected_rows is not None:
                self.note(
                    outcome.rows == shape.expected_rows,
                    f"shape {shape.number} drew "
                    f"{outcome.rows}/{shape.expected_rows} rows",
                )

        print("\n".join(self.findings))
        failed = [finding for finding in self.findings if finding.startswith("FAIL")]
        # The phase runner reads an EXCEPTION, not a return code. An earlier
        # version of this class returned 0/1 and its caller discarded the value,
        # so every finding above was decorative and the phase could not go red.
        assert not failed, "the surface floor did not hold:\n" + "\n".join(failed)


MAILBOX_URL = "http://127.0.0.1:8932/mcp"
MAILBOX_MANIFEST = "http://127.0.0.1:8932/mailbox"
MAILBOX_REVISION = "local-mailbox.1"

MAILBOX_LIST_ASK = (
    "Use the mailbox connector to list the messages in my mailbox, then stop "
    "and show me what came back. Do not summarise them in prose."
)
MAILBOX_DRAFT_ASK = (
    "Use the mailbox connector to draft a reply to message m-1041. Then stop. "
    "Do not send it and do not describe it in prose — just draft it."
)
MAILBOX_SEND_ASK = (
    "Now send that reply using the mailbox connector's send tool, to "
    "jordan.reyes@acme.example, with no Cc."
)


class MailboxJourney:
    """A local mailbox: a read that draws a table, a compose that draws email://.

    The third step is the one that cannot be measured from the DOM alone. "The
    write gate takes the decision" is a claim about a code path, so the journey
    DECLINES the gate and then asks the fixture — over plain HTTP, outside the
    app entirely — whether ``send_reply`` was ever called. ``sent == 0`` after a
    decline is the measurement; a screenshot of a button is not.
    """

    def __init__(self, session: DriverSession, *, left: str | None = None) -> None:
        self.session = session
        #: The conversation the caller's `new_chat` walked out of — see
        #: `wait_for_conversation_id` for why binding it again is possible.
        self.left = left
        self.findings: list[str] = []

    # -- helpers ---------------------------------------------------------

    def note(self, ok: bool, claim: str) -> bool:
        self.findings.append(f"{'PASS' if ok else 'FAIL'}  {claim}")
        return ok

    @staticmethod
    def manifest() -> dict:
        """The fixture's own account of itself, read over plain HTTP.

        Same guard the surface-floor fixture carries and for the same measured
        reason: a PREVIOUS session's server left listening on the port answers
        registration happily and serves its own older tool list, so the journey
        measures a fixture nobody edited.
        """

        with urllib.request.urlopen(MAILBOX_MANIFEST, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def register(self) -> str:
        """Register the loopback mailbox. No OAuth: ``auth_mode: none``."""

        created = app_write(
            self.session,
            "POST",
            "/v1/mcp/servers",
            {
                "url": MAILBOX_URL,
                "display_name": "Mailbox",
                "transport": "http",
                "auth_mode": "none",
            },
        )
        assert isinstance(created, dict), created
        server_id = str(created.get("id") or created.get("server_id") or "")
        assert server_id, f"no server id in {created}"
        return server_id

    def bind_write_scope(self, conversation_id: str) -> None:
        """Grant the chat read+write on the mailbox.

        Connector scope is PER CHAT and must exist before the run that needs it.
        Without the write half the send is DENIED outright rather than gated,
        and a journey that skipped this would report "nothing sent" while having
        measured the allowlist instead of the gate.
        """

        servers = self.session.transport("GET", "/v1/mcp/servers")
        rows = servers.get("servers", servers) if isinstance(servers, dict) else servers
        names = [
            str(row.get("name") or row.get("display_name") or "")
            for row in rows or []
            if MAILBOX_URL.rsplit("/", 1)[0] in str(row.get("url") or "")
            or "mailbox" in str(row.get("name") or "").lower()
        ]
        assert names, f"the mailbox connector is not in the server list: {rows}"
        app_write(
            self.session,
            "PATCH",
            f"/v1/agent/conversations/{conversation_id}/connectors",
            {"scopes": {names[0]: ["read", "write"]}},
        )

    def tab_uris(self) -> list[str]:
        """The canvas tab keys, straight from the DOM.

        `.tc-tab[data-uri]`, never `[data-testid^=tc-tab]` — that prefix also
        matches the unpin/close buttons and yields the tab LABEL instead of the
        URI, which makes a scheme assertion vacuous.
        """

        raw = self.session.evaluate(
            "JSON.stringify([...document.querySelectorAll('.tc-tab[data-uri]')]"
            ".map(e=>e.getAttribute('data-uri')))"
        )
        try:
            return [uri for uri in json.loads(raw or "[]") if uri]
        except json.JSONDecodeError:
            return []

    def read_composer(self) -> dict:
        """What the email composer is actually showing."""

        js = """(()=>{
          const el = document.querySelector('[data-testid=email-renderer]');
          if(!el) return JSON.stringify({present:false});
          const v = (id)=>{const n=el.querySelector('[data-testid='+id+']');
                           return n ? n.value : null;};
          return JSON.stringify({
            present:true,
            to: v('email-to'), cc: v('email-cc'), subject: v('email-subject'),
            body: el.textContent.slice(0, 600),
          });
        })()"""
        return json.loads(self.session.evaluate(js))

    # -- steps -----------------------------------------------------------

    def a_read_draws_a_table(self) -> None:
        """The first turn of a fresh chat, so it meets the empty cockpit."""

        s = self.session
        s.send_first_run_message(MAILBOX_LIST_ASK)
        drew = s.wait_for("[data-testid=table-renderer]", 180)
        s.shot("mailbox-01-list")
        if not self.note(drew, "the mailbox read drew a table"):
            return
        headers = json.loads(
            s.evaluate(
                "JSON.stringify([...document.querySelectorAll("
                "'[data-testid=table-renderer] th')].map(n=>n.textContent.trim()))"
            )
        )
        lowered = {header.lower() for header in headers}
        # The discriminator is WHOSE vocabulary the columns come from. `id` /
        # `type` / `text` are what langchain-mcp-adapters puts on a converted
        # content block, so any of them means the table bound the MCP envelope
        # rather than the mailbox — a surface that looks, from across the room,
        # like three messages.
        self.note(
            bool(lowered & {"subject", "from"}),
            f"the columns are the mailbox's ({headers})",
        )
        self.note(
            not (lowered & {"type", "text"}),
            f"no transport slot leaked into the columns ({headers})",
        )

    def a_compose_draws_the_email_surface(self) -> None:
        s = self.session
        s.send(MAILBOX_DRAFT_ASK)
        drew = s.wait_for("[data-testid=email-renderer]", 180)
        s.shot("mailbox-02-compose")
        if not self.note(drew, "the compose mounted EmailRenderer"):
            uris = self.tab_uris()
            print(f"[mailbox] tabs without an email surface: {uris}")
            return
        uris = [uri for uri in self.tab_uris() if uri.startswith("email://")]
        self.note(bool(uris), f"a canvas tab is keyed on an email:// uri ({uris})")
        self.note(
            all(uri.count("://") == 1 and "%3A" not in uri for uri in uris),
            f"the email uri is ONE identity, no codec ({uris})",
        )
        composed = self.read_composer()
        print(f"[mailbox] composer: {json.dumps(composed)[:500]}")
        self.note(bool(composed.get("to")), f"To is bound ({composed.get('to')!r})")
        self.note(
            bool(composed.get("subject")),
            f"Subject is bound ({composed.get('subject')!r})",
        )
        self.note(
            "Confirming the locked-price block" in (composed.get("body") or ""),
            "the drafted body is on screen",
        )
        # The sibling task's reason for existing, asserted here: the fixture
        # sends an EMPTY cc and the surface must show an empty cc. A Cc the user
        # never saw is the failure this surface must not have.
        self.note(
            (composed.get("cc") or "") == "",
            f"Cc is empty, exactly as the connector sent it ({composed.get('cc')!r})",
        )

    def a_send_parks_at_the_write_gate(self, conversation_id: str) -> None:
        s = self.session
        before = self.manifest().get("sent")
        self.bind_write_scope(conversation_id)
        s.send(MAILBOX_SEND_ASK)
        parked = s.wait_for("[data-testid=tc-write-gate]", 180)
        s.shot("mailbox-03-write-gate")
        if not self.note(parked, "the send parked at the existing write gate"):
            return
        # Decline, which is the safe terminal state AND the measurement: the
        # question is whether the connector was reached, and only the far end
        # can answer that.
        s.click("[data-testid^=tc-chat-approval-reject-]")
        time.sleep(6)
        after = self.manifest().get("sent")
        self.note(
            after == before,
            f"a declined send never reached the connector (sent {before} -> {after})",
        )
        s.shot("mailbox-04-declined")

    def run(self) -> int:
        s = self.session
        manifest = self.manifest()
        assert manifest.get("revision") == MAILBOX_REVISION, (
            f"the server on 8932 reports revision {manifest.get('revision')!r}, "
            f"not {MAILBOX_REVISION!r} — you are talking to a stale fixture"
        )
        self.note(
            manifest.get("sent") == 0,
            f"the fixture starts with an empty outbox ({manifest.get('sent')})",
        )

        server_id = self.register()
        print(f"[mailbox] registered loopback connector: {server_id}")

        self.a_read_draws_a_table()
        conversation_id = wait_for_conversation_id(s, excluding=self.left)
        self.a_compose_draws_the_email_surface()
        self.a_send_parks_at_the_write_gate(conversation_id)

        print("\n".join(self.findings))
        return 0 if all(f.startswith("PASS") for f in self.findings) else 1


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in_and_key(s: DriverSession) -> None:
    preflight_staged_runtime(target=SOURCE_TARGET)
    provider, key = byok_provider()
    STATE["provider"], STATE["key"] = provider, key
    log(f"provider={provider} key_len={len(key)} (value withheld)")
    status = s.rpc("status")
    assert status.get("target") == "source", f"target={status.get('target')!r}"
    assert status.get("posture") == "prod", f"posture={status.get('posture')!r}"
    s.sign_in_local()
    s.ftue_add_key(provider, key)
    catalog = s.transport("GET", "/v1/agent/models")
    assert any(
        isinstance(m, dict) and m.get("provider") == provider and m.get("configured")
        for m in catalog.get("models", [])
    ), "entered BYOK provider was not configured"


# ── AS-1: the counterexample ─────────────────────────────────────────────────
def as1_plain_chat_publishes_nothing(s: DriverSession) -> None:
    """An ordinary question must produce NO rich UI at all.

    G0 is the counterexample protecting the "surfaces, not transcripts" rule
    from becoming "surface everything". It runs first, and in a conversation of
    its own, because it asserts an ABSENCE — once anything has published, the
    claim is unfalsifiable.
    """

    s.send_first_run_message(PLAIN_PROMPT)
    conversation_id = wait_for_conversation_id(s)
    run_id = wait_for_new_run(s, conversation_id, 0)
    wait_for_terminal_run(s, run_id)
    time.sleep(2)
    s.shot("g0-plain-answer-no-rich-ui")

    assistant = s.evaluate(
        "document.querySelectorAll('[data-testid^=tc-chat-message-]"
        "[data-role=assistant]').length"
    )
    assert int(assistant or 0) >= 1, "no assistant message for a plain question"

    leaked = [sel for sel in RICH_UI_SELECTORS if s.present(sel)]
    assert not leaked, f"plain chat rendered rich UI: {leaked}"

    runs = runs_for_conversation(s, conversation_id)
    assert len(runs) == 1, f"expected exactly one run, got {len(runs)}"
    events = run_events(s, run_id)
    finals = [e for e in events if e.get("event_type") == "final_response"]
    assert len(finals) == 1, f"expected exactly one final_response, got {len(finals)}"
    artifacts = [
        e for e in events if str(e.get("event_type", "")).startswith("artifact.")
    ]
    assert not artifacts, f"plain chat emitted artifact events: {artifacts!r}"
    log(f"one run, one final_response, {len(events)} events, no rich UI")


# ── AS-2…AS-7: one published dataset, read many ways ─────────────────────────
def as2_publish_a_dataset(s: DriverSession) -> None:
    """Publish the dataset every later phase reads, and police the tools used.

    Deliberately requests no workspace grant, opens no native folder picker,
    stages no filesystem effect, and writes no local file.
    """

    left = new_chat(s)
    s.send(CREATE_PROMPT)
    conversation_id = wait_for_conversation_id(s, excluding=left)
    run_id = wait_for_new_run(s, conversation_id, 0)
    wait_for_terminal_run(s, run_id)

    events = run_events(s, run_id)
    assert_only_workspace_or_artifact_tools(events)
    assert_no_workspace_apply(events)
    artifact = dataset_artifact_from_run(events)
    assert_artifact_named_forecast(artifact_detail(s, artifact.artifact_id))

    STATE.update(
        {"conversation_id": conversation_id, "run_id": run_id, "artifact": artifact}
    )
    log(f"published {artifact.artifact_id} in run {run_id}")


def as3_the_canvas_presents_it_without_navigation(s: DriverSession) -> None:
    """DEPENDS ON AS-2, and must precede AS-4.

    G2A reached the artifact by clicking into the Sources rail. That is a real
    path, but not the path a user takes: the reported failure was simply
    "finish a run, look at Studio, the table is not there" — and G2A passed
    throughout. Once AS-4 has navigated, this claim cannot be made.
    """

    require(STATE.get("run_id"), "needs the run AS-2 creates")
    assert_artifact_precedes_the_seal(run_events(s, STATE["run_id"]))
    assert_canvas_presents_without_navigation(s)
    s.shot("canvas-auto-presented-csv")


def as4_the_dataset_surface_renders_from_sources(s: DriverSession) -> None:
    """DEPENDS ON AS-2. Open it by hand and check the surface and the bytes."""

    artifact = STATE.get("artifact")
    require(artifact, "needs the artifact AS-2 publishes")
    open_artifact_from_sources(s)
    assert_dataset_surface(s)
    assert_initial_csv_semantics(read_artifact_bytes(s, artifact))
    s.shot("generated-csv-surface")


def as5_the_canvas_survives_a_chat_only_follow_up(s: DriverSession) -> None:
    """DEPENDS ON AS-2. PRD-02: a second message must not erase turn 1's surface.

    Where the surface was being lost: the canvas folds `session.events`, and
    binding a new run replaces that stream, so an artifact published on turn 1
    stopped existing as far as the canvas was concerned.
    """

    conversation_id = STATE.get("conversation_id")
    run_one = STATE.get("run_id")
    require(conversation_id and run_one, "needs the run AS-2 creates")

    assert s.wait_for("[data-testid=artifact-dataset-renderer]", 60), (
        "the dataset is not on the canvas before the follow-up; PRD-02 cannot "
        "be assessed"
    )
    assert_canvas_shows_the_dataset(canvas_state(s), when="before the follow-up")

    before = len(runs_for_conversation(s, conversation_id))
    s.send(FOLLOW_UP_PROMPT)
    run_two = wait_for_new_run(s, conversation_id, before)
    assert run_two != run_one, "the follow-up did not bind a new run"
    wait_for_terminal_run(s, run_two)

    assert_canvas_shows_the_dataset(canvas_state(s), when="after the follow-up")
    s.shot("dataset-still-open-after-followup")

    # Turn 2 produced no artifact: identity widened, run state did not.
    turn_two = {e.get("event_type") for e in run_events(s, run_two)}
    assert "artifact.created" not in turn_two, (
        "the follow-up produced an artifact; this no longer tests a chat-only turn"
    )
    canvas = s.transport("GET", f"/v1/agent/conversations/{conversation_id}/canvas")
    subjects = canvas.get("subjects", [])
    assert subjects, "conversation canvas returned no subjects"
    assert any(sub.get("run_id") == run_one for sub in subjects), (
        "the subject was not attributed to the run that produced it"
    )


def as6_artifact_edit_regressions(s: DriverSession) -> None:
    """DEPENDS ON AS-2. Defects reported from live use, reproduced.

    BUG 1 — "Save patched revision" returned 409 and the surface claimed a
    newer revision existed, on a run that had already gone terminal.
    BUG 2 — asking for another row minted a SECOND dataset artifact instead of
    revising the one on screen.
    RECOVERY — the hand edit BUG 1 saves is precisely what makes the agent's
    parent revision stale, so BUG 2's fix is only half an answer: the revise
    then has to land on top of the user's newer revision. That half failed
    INTERMITTENTLY on live boots — same prompt, one run re-read and retried,
    another reported a dead end and left the request undone — so it is asserted
    under its own name rather than inside BUG 2, which never regressed.

    All were fixed on unit-test evidence alone; this asserts the FACADE TRUTH,
    not the DOM's opinion.
    """

    artifact = STATE.get("artifact")
    conversation_id = STATE.get("conversation_id")
    require(artifact and conversation_id, "needs the artifact AS-2 publishes")

    open_artifact_from_sources(s)
    assert_dataset_surface(s)
    assert s.wait_for(CELL), "no editable dataset cell"

    # BUG 1 — save a cell edit while the run is terminal.
    s.fill(CELL, "edited-by-journey")
    s.shot("cell-edited")
    s.click(SAVE)
    revision = wait_for_revision(s, artifact.artifact_id, 2)
    s.shot("after-save")
    page_text = s.evaluate("document.body.innerText") or ""
    assert STALE_CLAIM not in page_text, (
        f"BUG 1 REGRESSED: the surface claimed {STALE_CLAIM!r} after a save on "
        "a terminal run"
    )
    assert revision >= 2, (
        "BUG 1 REGRESSED: saving a cell edit on a completed run did not append "
        f"a revision (still at r{revision})"
    )

    # BUG 2 — ask for another row; it must REVISE, not re-publish.
    before_ids = dataset_artifact_ids(s, conversation_id)
    assert artifact.artifact_id in before_ids
    # Back to Chat first: `open_artifact_from_sources` left the rail on Sources,
    # where the composer is not mounted at all, so a fill would fail on a
    # missing selector rather than on anything about the product.
    s.click('[role=tab]:has-text("Chat")')
    assert s.wait_for("[data-testid=composer-textarea]"), (
        "returning to the Chat tab did not mount the composer"
    )
    before_runs = len(runs_for_conversation(s, conversation_id))
    s.send(ADD_ROW_PROMPT)
    added_run = wait_for_new_run(s, conversation_id, before_runs)
    wait_for_terminal_run(s, added_run)

    extra = dataset_artifact_ids(s, conversation_id) - before_ids
    assert not extra, (
        f"BUG 2 REGRESSED: adding a row minted a second dataset artifact "
        f"({sorted(extra)}) instead of revising"
    )
    # RECOVERY — a separate claim from BUG 2, and separately named, because the
    # two fail for opposite reasons: BUG 2 is the agent writing a NEW artifact,
    # this is the agent writing NOTHING. Reporting a stale-revision surrender as
    # "BUG 2 REGRESSED" sends the reader hunting for a duplicate that is not
    # there. The revise is stale by construction — the cell edit above minted
    # r2 while the agent still holds r1 — so this is the ordinary loop, not a
    # contrived race.
    revised = wait_for_revision(s, artifact.artifact_id, revision + 1)
    assert revised > revision, (
        f"REVISION CONFLICT NOT RECOVERED: 'add a row' left the artifact at "
        f"r{revised}. No second artifact was minted, so BUG 2 is intact — the "
        f"revise lost the compare-and-append against the hand edit that made "
        f"r{revision} and the change was never re-applied on top of it, so the "
        f"user's request silently did nothing."
    )
    s.shot("after-add-row")


def as7_the_studio_identity_colour(s: DriverSession) -> None:
    """DEPENDS ON AS-2. The accent, measured with getComputedStyle in the real app.

    This cannot be a unit test: every layer of the colour system has already
    produced a defect that a passing unit test walked straight past.
    """

    conversation_id = STATE.get("conversation_id")
    require(conversation_id, "needs the conversation AS-2 creates")

    if not s.wait_for("[data-testid=artifact-dataset-renderer]", 90):
        # Self-diagnosing on the most likely failure, so a red run names its
        # cause instead of only its symptom.
        s.shot("no-dataset-diagnostic")
        diagnostic = {
            "lifecycle": s.evaluate(
                "document.querySelector('[data-testid=canvas-lifecycle-panel]')"
                "?.getAttribute('data-lifecycle') ?? 'absent'"
            ),
            "tabs": s.evaluate(
                "[...document.querySelectorAll('[data-testid=tc-tabs] [role=tab]')]"
                ".map(t => t.getAttribute('data-uri'))"
            ),
            "frameText": s.evaluate(
                "document.querySelector('[data-testid=artifact-frame]')"
                "?.textContent?.slice(0, 120) ?? 'absent'"
            ),
            "artifactProbe": probe_artifact(s),
        }
        raise AssertionError(
            "Studio never presented the dataset for the published CSV; "
            f"diagnostic={json.dumps(diagnostic, sort_keys=True)}"
        )

    canvas = s.transport("GET", f"/v1/agent/conversations/{conversation_id}/canvas")
    subjects = canvas.get("subjects") or []
    assert subjects, "conversation canvas returned no subjects"
    artifact = next((sub for sub in subjects if sub.get("kind") == "artifact"), None)
    assert artifact is not None, "no artifact subject on the canvas"
    assert "accent" in artifact, (
        "the canvas subject has no `accent` field — the seam a publish_artifact "
        "colour travels through is missing on the wire"
    )
    observed = read_surface_colour(s)
    s.shot("surface-colour-canvas")
    log(f"observed={observed} canvas_accent={artifact.get('accent')}")
    assert_colour(observed, artifact.get("accent"))


def as8_switching_finished_artifacts_raises_no_alert(s: DriverSession) -> None:
    """Two artifacts, run SEALED, click the older tab — no follow-live banner.

    The exact user action that used to raise a full-bleed
    `PINNED TO <TAB> · THE RUN HAS MOVED ON` banner, offering to follow a
    stream that had already ended.

    Its own conversation, and the IPC recorder is installed BEFORE the run so
    the artifact surface's very first request is captured — installing after
    the hang can only see calls that come later, which is why an earlier
    window read zero and proved nothing.
    """

    left = new_chat(s)
    recorder = install_ipc_recorder(s)
    assert recorder == "installed", (
        f"could not install the IPC recorder ({recorder!r}); the run would "
        "produce an unfalsifiable result"
    )
    s.send(FOLLOW_LIVE_CREATE_PROMPT)
    conversation_id = wait_for_conversation_id(s, excluding=left)
    run_id = wait_for_new_run(s, conversation_id, 0)
    wait_for_terminal_run(s, run_id)

    assert s.wait_for("[data-testid=tc-tabs] [role=tab]", 90), (
        "no surface tab ever appeared; the run published nothing"
    )
    deadline = time.time() + 60
    before = read_strip(s)
    while time.time() < deadline and len(before.get("tabs") or []) < 2:
        time.sleep(1)
        before = read_strip(s)
    # Let the artifact CONTENT resolve before the screenshot: the tab strip is
    # projected from the ledger fold and lands almost immediately, the artifact
    # body is a separate fetch behind it. Shooting between the two records
    # "Loading artifact…", indistinguishable from a broken content fetch.
    wait_for_canvas_settled(s)
    s.shot("two-artifacts-sealed")

    tabs = before.get("tabs") or []
    require(len(tabs) >= 2, f"the run published {len(tabs)} artifact(s); need two")

    s.click("[data-testid=tc-tabs] [role=tab]")
    time.sleep(2)
    after = read_strip(s)
    s.shot("older-tab-selected")
    log(f"ipc calls recorded: {count_ipc(s)}")
    try:
        assert_no_alert(s, after)
    except AssertionError:
        # The banner is a symptom; what the canvas thought and what the surface
        # actually requested is the cause. Dump both before failing, or the next
        # step is another full boot just to find out.
        print(f"  DIAGNOSTIC canvas={json.dumps(probe_canvas(s), sort_keys=True)}")
        print(f"  DIAGNOSTIC ipc={json.dumps(dump_ipc(s), sort_keys=True)[:1200]}")
        raise


def as9_the_inference_floor(s: DriverSession) -> None:
    """The eight-shape matrix: one dataset, eight MCP envelopes, one question.

    A connector nobody wrote a spec for must still render a legible, shaped
    surface — with no model call and no provider credential involved in the
    shaping, and the ledger's provenance agreeing with what is drawn.

    **Why eight and not one.** This phase used to drive a single read and pass
    against a fixture whose tools returned ``structuredContent`` — the one wire
    form effectively no real MCP server produces. It was 10/10 green while every
    real connector was broken. The fixture now serves the SAME three incidents
    in eight envelopes (``surface-floor/fixture_mcp.py``), so any difference in
    what renders is caused by the envelope and nothing else.

    **What is asserted.** Not "a table mounted" — shapes 4-8 historically
    mounted a perfectly real table over the MCP ENVELOPE: ``items_path:
    "result"``, columns ``ID / Type / Text``, one row per content block with the
    connector's whole payload in a single cell (shape 8 drew three such rows,
    which from across the room looks exactly like three incidents). That is more
    dangerous than an empty surface because it looks like it worked. So the
    discriminator is whose vocabulary the drawn slots come from:
    :data:`TRANSPORT_SLOTS` disqualifies, :data:`CONNECTOR_SLOTS` is what counts.

    Needs the loopback fixture MCP server; skips when it is not listening, and
    skips rather than measures when the server that IS listening reports a
    revision this phase does not recognise.

    Set ``SURFACE_FLOOR_SHAPES=1,4`` to drive a subset (eight real runs is the
    most expensive phase in this file).
    """

    try:
        urllib.request.urlopen(FIXTURE_URL, timeout=2)
    except urllib.error.HTTPError:
        pass  # an HTTP error still proves something is listening
    except Exception:  # noqa: BLE001
        require(
            False,
            f"no fixture MCP server on {FIXTURE_URL} — start "
            "tools/desktop-journeys/surface-floor/fixture_mcp.py",
        )
    new_chat(s)
    FloorJourney(s, selected_shapes()).run()


def as10_the_local_mailbox_and_its_email_surface(s: DriverSession) -> None:
    """A local mailbox reads as a table and composes as an ``email://`` surface.

    The one connector shape the product has a hand-built renderer for and has
    never reached: ``EmailRenderer`` shipped registered on ``email`` in Phase 4
    and nothing in the tree minted that scheme until the projector's draft rung.

    Local on purpose. Gmail and Drive are gated OFF pending Google's CASA
    restricted-scope review, so there is no mail connector an automated journey
    may authorise — and a local mailbox needs no vendor, no OAuth, and no
    compliance sign-off to be observable end to end.

    Needs the loopback mailbox fixture; skips when it is not listening.
    """

    try:
        urllib.request.urlopen(MAILBOX_URL, timeout=2)
    except urllib.error.HTTPError:
        pass  # an HTTP error still proves something is listening
    except Exception:  # noqa: BLE001
        require(
            False,
            f"no mailbox MCP server on {MAILBOX_URL} — start "
            "tools/desktop-journeys/local-mailbox/fixture_mcp.py",
        )
    MailboxJourney(s, left=new_chat(s)).run()


def main() -> int:
    plan = JourneyPlan("artifacts-and-surfaces")
    plan.boot(
        "source · fresh",
        lambda: DriverSession(name="artifacts-and-surfaces"),
        setup=sign_in_and_key,
        env=ARTIFACT_JOURNEY_ENVIRONMENT,
        clear_env=SECRET_ENVIRONMENT_NAMES,
        phases=[
            (
                "AS-1",
                "plain chat publishes no rich UI at all",
                as1_plain_chat_publishes_nothing,
            ),
            (
                "AS-2",
                "publish the dataset every later phase reads",
                as2_publish_a_dataset,
            ),
            (
                "AS-3",
                "the canvas presents it without navigation [needs AS-2]",
                as3_the_canvas_presents_it_without_navigation,
            ),
            (
                "AS-4",
                "the dataset surface renders from Sources [needs AS-2]",
                as4_the_dataset_surface_renders_from_sources,
            ),
            (
                "AS-5",
                "the canvas survives a chat-only follow-up [needs AS-2]",
                as5_the_canvas_survives_a_chat_only_follow_up,
            ),
            (
                "AS-6",
                "artifact edit regressions: save, and revise-not-republish [needs AS-2]",
                as6_artifact_edit_regressions,
            ),
            (
                "AS-7",
                "the Studio identity colour, measured live [needs AS-2]",
                as7_the_studio_identity_colour,
            ),
            (
                "AS-8",
                "switching between finished artifacts raises no alert",
                as8_switching_finished_artifacts_raises_no_alert,
            ),
            (
                "AS-9",
                "the inference floor: eight MCP envelopes, one dataset, "
                "connector columns not envelope columns",
                as9_the_inference_floor,
            ),
            (
                "AS-10",
                "a local mailbox reads as a table and composes as email://",
                as10_the_local_mailbox_and_its_email_surface,
            ),
        ],
    )
    code = plan.finish()
    key = STATE.get("key")
    if key:
        assert_no_plaintext_secret(key, (plan_run_dir(),))
    return code


def plan_run_dir():
    from _lib import RUNS_DIR

    return RUNS_DIR / "artifacts-and-surfaces"


if __name__ == "__main__":
    raise SystemExit(main())
