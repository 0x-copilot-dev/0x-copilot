#!/usr/bin/env python3
"""SF-1 — the inference floor, end to end on the real packaged app.

**The claim under test.** A connector nobody wrote a spec for still renders a
legible, shaped surface — with no model call and no provider credential involved
in the shaping — and the ledger's provenance agrees with what is drawn.

**Why this is a journey and not a unit test.** The generative-UI audit that
motivated this work found four independent breaks between a matched spec and the
screen, and ~13,000 unit tests were green over every one of them. Each break sat
at an injected seam — a scheduler, a store, a completion port, a tuple half — and
tests inject past seams. Two of them are only observable here:

* the spec is rebuilt field-by-field by ``RuntimeEventPresentationProjector``
  before it is persisted, so an emitter test and a fold test can both pass while
  the allow-list silently strips it;
* the PRESENT stage only runs for a tool the policy layer classified READ, which
  depends on a real MCP descriptor arriving over a real transport.

**What is real and what is a double.** Everything inside the app is real: the
real MCP client, the real per-tool middleware stack, the real ``SurfaceProjector``
climbing the real ladder, the real ``WorkLedgerEmitter``, the real hydration
endpoint, the real renderers. The single substitution is the vendor on the far
end — a loopback MCP server (``tools/desktop-journeys/surface-floor/fixture_mcp.py``) — because every
connector in the desktop profile requires an OAuth authorization that an
automated journey must not complete in the user's name. That substitution is at
the network boundary, which is the same place the Hermes harness draws it.

**This journey now walks a matrix, and is EXPECTED TO FAIL.**

It used to drive one read and pass 10/10 — against a fixture that returned
``structuredContent``, which is the one wire form effectively no real MCP server
produces. A green check over a path nobody takes is worse than no check, so the
fixture was rebuilt to serve the same three incidents in **eight** envelopes and
this journey drives one read per envelope and prints what each one drew.

Measured through the real projector, shapes 4–8 do NOT simply "render nothing" —
they render a table over the **MCP envelope**: ``items_path: "result"``, columns
``ID / Type / Text``, one row per content block, the connector's whole payload
stuffed into a single cell. That is more dangerous than an empty surface,
because it looks like it worked. Hence :data:`TRANSPORT_SLOTS` and the ``bound``
column: a surface counts only when the slots it drew name the CONNECTOR's fields
rather than the transport's.

So a non-zero exit here is the current, correct answer. A green run today would
mean the instrument broke, not that the pipeline was fixed.

**Prerequisites**

1. ``node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64``
   (re-stage after ANY ``services/*`` change — the stage is a snapshot)
2. ``npm run build --workspace @0x-copilot/desktop``
3. the fixture server running: ``python tools/desktop-journeys/surface-floor/fixture_mcp.py``
4. a provider key in ``services/ai-backend/.env``

Usage::

    python tools/desktop-journeys/surface-floor/floor_e2e.py             # all 8 shapes
    python tools/desktop-journeys/surface-floor/floor_e2e.py --shapes 1,4,8
    SURFACE_FIXTURE_PORT=8947 python .../floor_e2e.py                    # other port
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import DriverSession, load_env_key  # noqa: E402

#: Kept in step with ``fixture_mcp.PORT``: same env var, same default, so a
#: journey and a fixture started from two different shells still meet.
FIXTURE_PORT: Final[int] = int(os.environ.get("SURFACE_FIXTURE_PORT", "8931"))
FIXTURE_ORIGIN: Final[str] = f"http://127.0.0.1:{FIXTURE_PORT}"
FIXTURE_URL: Final[str] = f"{FIXTURE_ORIGIN}/mcp"
FIXTURE_MANIFEST: Final[str] = f"{FIXTURE_ORIGIN}/shapes"

#: The revision this journey knows how to read (``fixture_mcp.REVISION``). A
#: mismatch stops the run rather than measuring a fixture whose shape numbers
#: mean something else.
EXPECTED_REVISION: Final[str] = "eight-shapes.1"

TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "cancelled", "rejected", "timed_out"}
)

#: Slot labels that belong to the MCP TRANSPORT, not to any connector.
#: ``langchain-mcp-adapters`` converts each content block to
#: ``{"type": "text", "text": …, "id": …}``; when the ladder binds the envelope
#: instead of the payload inside it, these three are exactly what the table
#: draws as its columns. Measured against the real projector, not guessed.
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


#: The prompt names the tool explicitly. Every other connector journey
#: deliberately asks for an OUTCOME so a descriptor rename does not look like a
#: broken connector — here the opposite is required: the measurement is
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
        1,
        "list_incidents",
        "json object in a text block",
        _ask("list_incidents"),
        3,
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
#: deep four-hop identity trace runs on it. Before this rewrite the trace ran on
#: a ``structuredContent`` payload, which is why it never caught anything.
TRACE_SHAPE: Final[int] = 1


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

        The distinction the old journey had no way to make. A table drawn over
        ``{"result": [<content blocks>]}`` is a real, mounted, non-empty table —
        it just describes the wire format. Requiring connector-named slots AND
        forbidding transport-named ones separates the two, and it is the one
        assertion here that shapes 4–8 cannot satisfy by accident.
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


class FloorJourney:
    """Drives the app once per envelope and judges the surface each one produces."""

    def __init__(self, session: DriverSession, shapes: tuple[Shape, ...]) -> None:
        self.session = session
        self.shapes = shapes
        self.findings: list[str] = []
        self.outcomes: list[ShapeOutcome] = []

    # -- helpers ---------------------------------------------------------

    def post(self, path: str, body: dict) -> object:
        """Authenticated POST through the app. ``_lib.transport`` is GET-shaped."""

        payload = json.dumps({"method": "POST", "path": path, "body": body})
        js = (
            "(async()=>{try{const r=await window.bridge.ipc.invoke("
            f'"transport.request",{payload});'
            'if(r&&r.kind==="transport-result"){'
            'if(!r.ok)return "ERR:HTTP "+String(r.error?.status??"?")+" "'
            '+String(r.error?.message??"");'
            "return JSON.stringify(r.value);}return JSON.stringify(r);}"
            'catch(e){return "ERR:"+e.message}})()'
        )
        raw = self.session.evaluate(js)
        if isinstance(raw, str) and raw.startswith("ERR:"):
            raise RuntimeError(f"POST {path} -> {raw}")
        return json.loads(raw)

    def note(self, ok: bool, claim: str) -> bool:
        self.findings.append(f"{'PASS' if ok else 'FAIL'}  {claim}")
        return ok

    # -- fixture identity -------------------------------------------------

    @staticmethod
    def read_manifest() -> dict:
        """The RUNNING fixture's own account of what it serves.

        Read over plain HTTP before anything is registered, and it is the first
        thing this journey does, because a stale fixture is a silent failure
        with a convincing face: a previous session's server left listening on
        the port registers fine, initialises fine, and then serves ITS tool
        list. That happened while these shapes were being written — every newly
        added tool came back "Unknown tool" from a server that looked perfectly
        healthy. Reading the revision turns twenty minutes of confusion into one
        line.
        """

        try:
            with urllib.request.urlopen(FIXTURE_MANIFEST, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise SystemExit(
                f"[floor] no fixture manifest at {FIXTURE_MANIFEST} ({exc}).\n"
                f"        start it with: SURFACE_FIXTURE_PORT={FIXTURE_PORT} "
                f"python tools/desktop-journeys/surface-floor/fixture_mcp.py"
            ) from exc

    def check_fixture(self) -> None:
        """Refuse to measure a fixture this journey does not recognise."""

        manifest = self.read_manifest()
        revision = manifest.get("revision")
        if revision != EXPECTED_REVISION:
            raise SystemExit(
                f"[floor] the fixture on :{FIXTURE_PORT} reports revision "
                f"{revision!r}; this journey expects {EXPECTED_REVISION!r}. "
                "Either a stale server is listening or the fixture changed "
                "without the journey. Stop the old process and restart it."
            )
        served = {row.get("tool") for row in manifest.get("shapes", [])}
        missing = sorted({shape.tool for shape in SHAPES} - served)
        if missing:
            raise SystemExit(
                f"[floor] the fixture does not serve {missing} — it is not the "
                "one this journey was written against."
            )
        rows = manifest.get("rows")
        expected = {s.expected_rows for s in SHAPES if s.expected_rows is not None}
        if expected and expected != {rows}:
            raise SystemExit(
                f"[floor] the fixture serves {rows} rows; the matrix expects "
                f"{sorted(expected)}. Update Shape.expected_rows."
            )
        print(
            f"[floor] fixture {manifest.get('fixture')!r} rev {revision} "
            f"on :{FIXTURE_PORT} — {len(served)} shapes, {rows} rows"
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
        ``""`` and disarmed both ledger hops.
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

        A failed run is a matrix CELL, not an abort: the deliverable is all
        eight envelopes measured, including the ones that break their run.
        Raising here would delete every row after the first bad one, which is
        how a one-read journey hides a systemic failure behind a single stack
        trace.
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

    def start_new_chat(self) -> None:
        """Leave the previous run behind so each envelope gets its own thread."""

        self.session.open_destination("Chats")
        assert self.session.wait_for("[data-testid=chats-new-chat]", 30), (
            "the Chats archive never offered a new chat"
        )
        self.session.click("[data-testid=chats-new-chat]")
        assert self.session.wait_for("[data-testid=composer-textarea]", 30), (
            "a new chat did not reveal a composer"
        )

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
            found["error"] = "no run_id — refusing to guess"
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
            return {"error": "no run_id"}
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
        ``[data-testid^=field-]`` row for a record. The previous version looked
        for ``[data-testid$=-label]``, which no renderer has ever emitted
        (``primitives.tsx::FieldRow`` gives the row ``field-<path>`` and the
        value ``field-<path>-value``; the label span carries no test id) — so
        the record branch reported zero slots whatever it drew, and the "at
        least 3 bound slots" claim was decided entirely by the table branch.
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
            badge: (el.querySelector('[data-testid=surface-badge]')||{}).textContent,
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
        print(f"\n[floor] shape {shape.number} — {shape.tool} ({shape.wire})")
        if not first:
            self.start_new_chat()
        before = set(self._run_ids())
        self.session.fill("[data-testid=composer-textarea]", shape.ask)
        time.sleep(0.3)
        self.session.click('button[aria-label="Send message"]')

        run_id = self._wait_for_new_run(before)
        if not run_id:
            outcome.note = "no run persisted"
            return outcome
        status = self._wait_terminal(run_id)
        print(f"[floor]   run {run_id} -> {status}")

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
        print(
            f"[floor]   drawn kind={outcome.kind} rows={outcome.rows} "
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

    def run(self) -> int:
        self.check_fixture()

        session = self.session
        session.sign_in_local()
        session.ftue_add_key("openai", load_env_key("openai"))
        session.shot("signed-in")

        server_id = self.register_fixture()
        print(f"[floor] registered loopback connector: {server_id}")

        servers = session.transport("GET", "/v1/mcp/servers")
        rows = servers.get("servers", servers) if isinstance(servers, dict) else servers
        self.note(bool(rows), f"the connector is registered ({len(rows)} server(s))")
        session.shot("connector-registered")

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
            # ONE IDENTITY. The defect this journey exists to catch was the
            # canvas minting `table://legacy-v2/table%3A%2F%2F…` and resolving
            # it against a map that could never hold it. Both halves are
            # asserted: no codec artefact may appear in a tab key, and a tab key
            # must be a value the ledger itself wrote. Either alone would pass
            # while the bug was live.
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
            self.note(
                bool(trace["client_ids"] & trace["ledger_ids"]),
                "a tab key is byte-identical to a surface_id the ledger wrote "
                f"(shared={sorted(trace['client_ids'] & trace['ledger_ids'])})",
            )

        # -- per-shape claims, so a failure names its envelope ---------------
        for outcome in self.outcomes:
            shape = outcome.shape
            self.note(
                outcome.rendered,
                f"shape {shape.number} ({shape.wire}) mounted a renderer",
            )
            self.note(
                outcome.bound,
                f"shape {shape.number} ({shape.wire}) bound the CONNECTOR's "
                f"fields, not the transport's — drew {outcome.slots}",
            )
            if shape.expected_rows is not None:
                self.note(
                    outcome.rows == shape.expected_rows,
                    f"shape {shape.number} drew "
                    f"{outcome.rows}/{shape.expected_rows} rows",
                )

        print("\n".join(self.findings))
        return 0 if all(f.startswith("PASS") for f in self.findings) else 1


def parse_args(argv: list[str]) -> tuple[Shape, ...]:
    parser = argparse.ArgumentParser(description="SF-1 surface floor shape matrix")
    parser.add_argument(
        "--shapes",
        default="",
        help="comma-separated shape numbers to drive (default: all eight)",
    )
    args = parser.parse_args(argv)
    if not args.shapes.strip():
        return SHAPES
    wanted = {int(part) for part in args.shapes.split(",") if part.strip()}
    chosen = tuple(shape for shape in SHAPES if shape.number in wanted)
    if not chosen:
        raise SystemExit(f"[floor] no such shapes: {sorted(wanted)}")
    return chosen


def main() -> int:
    shapes = parse_args(sys.argv[1:])
    with DriverSession("surface-floor") as session:
        return FloorJourney(session, shapes).run()


if __name__ == "__main__":
    raise SystemExit(main())
