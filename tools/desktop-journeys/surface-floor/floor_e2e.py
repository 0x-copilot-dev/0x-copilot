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

**Prerequisites**

1. ``node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64``
   (re-stage after ANY ``services/*`` change — the stage is a snapshot)
2. ``npm run build --workspace @0x-copilot/desktop``
3. the fixture server running: ``python tools/desktop-journeys/surface-floor/fixture_mcp.py``
4. a provider key in ``services/ai-backend/.env``
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lib import DriverSession, load_env_key  # noqa: E402

FIXTURE_URL = "http://127.0.0.1:8931/mcp"
ASK = (
    "Use the incidents connector to list the open incidents, then stop and "
    "show me what came back. Do not summarise them in prose."
)


class FloorJourney:
    """Drives the app to a real MCP read and judges the surface it produces."""

    def __init__(self, session: DriverSession) -> None:
        self.session = session
        self.findings: list[str] = []

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

    def wait_for_surface(self, timeout_s: int = 180) -> bool:
        """A rendered archetype, not merely a finished run.

        Keyed on the renderer's own testid rather than on run status: a run can
        complete having rendered nothing, which is exactly the failure this
        journey exists to catch.
        """

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.session.present("[data-testid=table-renderer]") or (
                self.session.present("[data-testid=record-renderer]")
            ):
                return True
            time.sleep(2)
        return False

    # -- pipeline trace ---------------------------------------------------

    def _run_id(self) -> str:
        return (
            self.session.evaluate(
                "(()=>{const el=document.querySelector('[data-run-id]');"
                "return el?el.getAttribute('data-run-id'):'';})()"
            )
            or ""
        )

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

        raw = self.session.evaluate(
            "(()=>{const t=[...document.querySelectorAll('[data-testid^=tc-tab]')]"
            ".map(e=>e.getAttribute('data-uri')||e.getAttribute('data-tab-uri')||e.textContent.trim());"
            "const slot=document.querySelector('[data-canvas-slot-testid=tc-surface-slot]');"
            "return JSON.stringify({tabs:t, activeUri: slot?slot.getAttribute('data-active-uri'):null});})()"
        )
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {"raw": raw}

    def diagnose(self) -> dict:
        """Trace all four hops and print them as one table.

        The architecture's central invariant is that ``surface_id`` is ONE value:
        what the ledger writes, what the endpoint serves, and what the canvas
        keys its tabs by are the same string with no codec between them. The
        bug this journey was written to catch was precisely a violation of that
        — the canvas minted ``table://legacy-v2/table%3A%2F%2F…`` and then
        looked it up in a map that could never contain it. So the trace prints
        the identity at each hop side by side; divergence is the finding.
        """

        run_id = self._run_id()
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

    def read_surface(self) -> dict:
        """Read what is actually on screen, out of the live DOM."""

        js = """(()=>{
          const el = document.querySelector('[data-testid=table-renderer]')
                  || document.querySelector('[data-testid=record-renderer]');
          if(!el) return JSON.stringify({present:false});
          const q = (s)=>[...el.querySelectorAll(s)].map(n=>n.textContent.trim());
          return JSON.stringify({
            present:true,
            kind: el.getAttribute('data-testid'),
            spec: el.getAttribute('data-spec'),
            title: (el.querySelector('[data-testid=surface-title]')||{}).textContent,
            headers: q('th'),
            firstRow: q('[data-testid^=table-cell-0-]'),
            fieldLabels: q('[data-testid^=field-][data-testid$=-label]'),
            badges: q('[data-surface-format=badge]'),
            body: el.textContent.slice(0, 400),
          });
        })()"""
        return json.loads(self.session.evaluate(js))

    def run(self) -> int:
        s = self.session
        s.sign_in_local()
        s.ftue_add_key("openai", load_env_key("openai"))
        s.shot("01-signed-in")

        server_id = self.register_fixture()
        print(f"[floor] registered loopback connector: {server_id}")

        servers = s.transport("GET", "/v1/mcp/servers")
        rows = servers.get("servers", servers) if isinstance(servers, dict) else servers
        self.note(bool(rows), f"the connector is registered ({len(rows)} server(s))")
        s.shot("02-connector-registered")

        s.send_first_run_message(ASK)
        rendered = self.wait_for_surface()
        s.shot("03-after-run")

        if not self.note(rendered, "a surface renderer mounted on screen"):
            print("\n".join(self.findings))
            return 1

        trace = self.diagnose()

        # ONE IDENTITY. The defect this journey exists to catch was the canvas
        # minting `table://legacy-v2/table%3A%2F%2F…` and resolving it against a
        # map that could never hold it. Both halves are asserted: no codec
        # artefact may appear in a tab key, and a tab key must be a value the
        # ledger itself wrote. Either alone would pass while the bug was live.
        codecs = [
            uri
            for uri in trace["client_ids"]
            if "legacy-v2" in uri
            or "surfaces-v2" in uri
            or "%3A" in uri
            or "%2F" in uri
        ]
        self.note(not codecs, f"no URI codec survives on a tab key ({codecs})")
        # Both notes are UNCONDITIONAL. A guard here (`if ledger_ids:`) would turn
        # "the trace could not find the ledger" into a silent pass, which is the
        # gate-that-cannot-start failure this repo has already paid for once.
        # An empty ledger while a renderer is mounted is itself the finding.
        self.note(
            bool(trace["ledger_ids"]),
            f"the ledger on disk has surface.created rows for this run "
            f"({trace['ledger_error'] or sorted(trace['ledger_ids'])})",
        )
        shared = trace["client_ids"] & trace["ledger_ids"]
        self.note(
            bool(shared),
            "a tab key is byte-identical to a surface_id the ledger wrote "
            f"(shared={sorted(shared)})",
        )

        surface = self.read_surface()
        print("[floor] on-screen surface:", json.dumps(surface, indent=2)[:900])

        self.note(surface.get("spec") == "present", "the renderer received a SPEC")
        self.note(
            "No spec matched" not in (surface.get("body") or ""),
            "the retired apology does not appear",
        )
        title = (surface.get("title") or "").strip()
        self.note(title not in ("", "Untitled"), f"the header is titled ({title!r})")
        slots = surface.get("headers") or surface.get("fieldLabels") or []
        self.note(len(slots) >= 3, f"at least 3 bound slots ({slots})")
        self.note(bool(surface.get("badges")), "a low-cardinality value drew as a chip")

        s.shot("04-surface")
        print("\n".join(self.findings))
        return 0 if all(f.startswith("PASS") for f in self.findings) else 1


def main() -> int:
    with DriverSession("surface-floor") as session:
        return FloorJourney(session).run()


if __name__ == "__main__":
    raise SystemExit(main())
